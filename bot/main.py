"""
Главный цикл торгового бота.

Два параллельных цикла:
  1. Signal loop  — раз в день в SIGNAL_CHECK_HOUR:SIGNAL_CHECK_MINUTE UTC
                    проверяет on-chain сигналы, открывает позиции
  2. Monitor loop — каждые PRICE_CHECK_INTERVAL секунд
                    проверяет trailing stop и max_hold для открытых позиций

Запуск:
    python -m bot.main

Переключение в LIVE:
    В .env установить PAPER_MODE=false
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, date
from aiohttp import web

from bot.config import load_config
from bot.kraken_client import KrakenClient
from bot.paper_trader import PaperTrader
from bot.onchain import update_history, bootstrap_all_from_tsv
from bot.strategy import check_all_signals
from bot.positions import (
    Position, load_positions, add_position,
    remove_position, update_peak, log_trade, CapitalTracker,
)
from bot.telegram_bot import TelegramNotifier
from bot.commands import CommandHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bot.main")


class TradingBot:
    def __init__(self):
        self.cfg = load_config()
        self.kraken = KrakenClient(self.cfg.kraken_api_key, self.cfg.kraken_api_secret)
        self.paper = PaperTrader(self.kraken) if self.cfg.paper_mode else None
        self.tg = TelegramNotifier(
            self.cfg.telegram_token,
            self.cfg.telegram_chat_id,
            self.cfg.paper_mode,
        )
        self.capital = CapitalTracker(self.cfg.initial_capital, self.cfg.target_capital)
        self._last_signal_date: date | None = None
        self.cmd = CommandHandler(self)

        # Загружаем _traded_today из Redis (восстанавливаем после рестарта)
        from bot.storage import load_traded_today_raw
        _tt = load_traded_today_raw()
        today_str = date.today().isoformat()
        if _tt.get("date") == today_str:
            self._traded_today: set[str] = set(_tt.get("assets", []))
        else:
            self._traded_today: set[str] = set()
        self._traded_date: date = date.today()

    # ── Открытие позиции ──────────────────────────────────────────

    def open_position(self, asset: str, signal_ratio: float):
        cfg = self.cfg.assets[asset]
        positions = load_positions()

        # Проверки
        if asset in positions:
            log.info(f"{asset}: уже в позиции, пропускаем")
            return
        if len(positions) >= self.cfg.max_simultaneous:
            log.info(f"Достигнут лимит {self.cfg.max_simultaneous} позиций, пропускаем {asset}")
            return

        # Уже торговали сегодня — повторный вход запрещён
        from bot.storage import save_traded_today_raw
        today = date.today()
        if today != self._traded_date:
            self._traded_today.clear()
            self._traded_date = today
            save_traded_today_raw({"date": today.isoformat(), "assets": []})
        if asset in self._traded_today:
            log.info(f"{asset}: уже торговали сегодня, повторный вход пропущен")
            return

        capital_to_use = self.capital.capital * self.cfg.position_size
        pair = cfg.kraken_pair

        try:
            if self.cfg.paper_mode:
                order = self.paper.buy(pair, capital_to_use, asset)
            else:
                # LIVE: реальная покупка
                price = self.kraken.get_price(pair)
                volume = capital_to_use / price
                order = self.kraken.place_market_buy(pair, volume)
                order["price"] = price
                order["volume"] = volume
                order["cost"] = capital_to_use

            pos = Position(
                asset=asset,
                pair=pair,
                direction="long",
                entry_date=date.today().isoformat(),
                entry_price=order["price"],
                volume=order["volume"],
                deployed_capital=capital_to_use,
                peak_price=order["price"],
                trailing_stop_pct=cfg.trailing_stop,
                max_hold_days=cfg.max_hold,
                order_id=order.get("order_id", ""),
            )
            add_position(pos)

            self.tg.position_opened(
                asset=asset,
                price=pos.entry_price,
                volume=pos.volume,
                capital_used=capital_to_use,
                signal_ratio=signal_ratio,
                capital_total=self.capital.capital,
            )
            log.info(f"✓ Открыта позиция {asset} @ ${pos.entry_price:.4f}")

        except Exception as e:
            log.error(f"Ошибка открытия {asset}: {e}")
            self.tg.error_alert(f"Ошибка открытия {asset}: {e}")

    # ── Закрытие позиции ──────────────────────────────────────────

    def close_position(self, asset: str, reason: str):
        positions = load_positions()
        pos = positions.get(asset)
        if not pos:
            return

        pair = pos.pair
        try:
            if self.cfg.paper_mode:
                order = self.paper.sell(pair, pos.volume, asset)
                exit_price = order["price"]
            else:
                # LIVE: реальная продажа
                result = self.kraken.place_market_sell(pair, pos.volume)
                exit_price = self.kraken.get_price(pair)

            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
            pnl_usd = pnl_pct * pos.deployed_capital

            # Обновляем капитал
            target_hit = self.capital.apply_pnl(pnl_usd)

            # Логируем сделку
            log_trade(pos, exit_price, reason, self.capital.capital)
            remove_position(asset)
            self._traded_today.add(asset)
            from bot.storage import save_traded_today_raw
            save_traded_today_raw({
                "date": date.today().isoformat(),
                "assets": list(self._traded_today),
            })

            self.tg.position_closed(
                asset=asset,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                pnl_usd=pnl_usd,
                reason=reason,
                days_held=pos.days_held,
                capital_total=self.capital.capital,
            )

            # Проверяем цель (снятие сливок)
            if target_hit:
                self._do_harvest()

        except Exception as e:
            log.error(f"Ошибка закрытия {asset}: {e}")
            self.tg.error_alert(f"Ошибка закрытия {asset}: {e}")

    def _do_harvest(self):
        """Снимаем прибыль, закрываем все позиции, рестарт."""
        log.info("🎉 Цель достигнута — снятие прибыли!")

        # Закрываем все открытые позиции
        positions = load_positions()
        for asset in list(positions.keys()):
            self.close_position(asset, "harvest")

        profit = self.capital.harvest()
        self.tg.harvest_notification(
            profit=profit,
            count=self.capital.harvest_count,
            total_harvested=self.capital.total_harvested,
        )

    # ── Signal loop (раз в день) ──────────────────────────────────

    async def signal_loop(self):
        log.info("Signal loop запущен")
        while True:
            now = datetime.now(timezone.utc)
            target_h = self.cfg.signal_check_hour
            target_m = self.cfg.signal_check_minute

            if (now.hour == target_h and now.minute == target_m
                    and self._last_signal_date != now.date()):
                await self._run_signal_check()
                self._last_signal_date = now.date()

            await asyncio.sleep(30)  # проверяем время каждые 30 сек

    async def _run_signal_check(self):
        log.info("=== Ежедневная проверка сигналов ===")
        try:
            # 1. Обновляем on-chain данные (в отдельном потоке — не блокирует event loop)
            await asyncio.to_thread(update_history, list(self.cfg.assets.keys()))

            # 2. Проверяем сигналы
            signals = check_all_signals(self.cfg.assets)

            # 3. Открываем позиции по сигналам
            positions = load_positions()
            free_slots = self.cfg.max_simultaneous - len(positions)

            opened = []
            for sig in signals:
                if free_slots <= 0:
                    break
                if sig.asset not in positions:
                    self.open_position(sig.asset, sig.ratio)
                    positions = load_positions()  # обновляем
                    free_slots -= 1
                    opened.append(f"{sig.asset} ({sig.ratio:.2f}x)")

            # 4. Telegram отчёт
            sig_texts = [f"{s.asset}: {s.ratio:.2f}x" for s in signals]
            self.tg.signal_scan_result(
                signals=sig_texts,
                no_action_reason="" if free_slots > 0 else f"Лимит позиций достигнут"
            )

        except Exception as e:
            log.error(f"Ошибка в signal_loop: {e}")
            self.tg.error_alert(str(e))

    # ── Monitor loop (каждые N секунд) ───────────────────────────

    async def monitor_loop(self):
        log.info("Monitor loop запущен")
        while True:
            await self._check_positions()
            await asyncio.sleep(self.cfg.price_check_interval)

    async def _check_positions(self):
        positions = load_positions()
        if not positions:
            return

        for asset, pos in list(positions.items()):
            cfg = self.cfg.assets.get(asset)
            if not cfg:
                continue

            try:
                current_price = (
                    self.paper.get_current_price(pos.pair)
                    if self.cfg.paper_mode
                    else self.kraken.get_price(pos.pair)
                )

                # Обновляем peak
                update_peak(asset, current_price)
                pos = load_positions().get(asset)
                if not pos:
                    continue

                # Проверка trailing stop
                if current_price <= pos.stop_price:
                    log.info(f"{asset}: TRAILING STOP  "
                             f"цена=${current_price:.4f} <= стоп=${pos.stop_price:.4f}")
                    self.close_position(asset, "trailing_stop")
                    continue

                # Проверка max_hold
                if pos.days_held >= pos.max_hold_days:
                    log.info(f"{asset}: MAX HOLD {pos.days_held}d >= {pos.max_hold_days}d")
                    self.close_position(asset, "max_hold")
                    continue

                pnl_pct = (current_price - pos.entry_price) / pos.entry_price
                log.debug(
                    f"{asset}: цена=${current_price:.4f}  "
                    f"стоп=${pos.stop_price:.4f}  "
                    f"P&L={pnl_pct:+.2%}  "
                    f"дней={pos.days_held}/{pos.max_hold_days}"
                )

            except Exception as e:
                log.error(f"Ошибка мониторинга {asset}: {e}")

    # ── Ежедневный отчёт ─────────────────────────────────────────

    async def daily_report_loop(self):
        """Шлёт дневной отчёт в 09:00 UTC."""
        _last_report_date = None
        while True:
            now = datetime.now(timezone.utc)
            if now.hour == 9 and _last_report_date != now.date():
                self._send_daily_report()
                _last_report_date = now.date()
            await asyncio.sleep(30)

    def _send_daily_report(self):
        positions = load_positions()
        pos_data = []
        for asset, pos in positions.items():
            cfg = self.cfg.assets.get(asset)
            if not cfg:
                continue
            try:
                price = (
                    self.paper.get_current_price(pos.pair)
                    if self.cfg.paper_mode
                    else self.kraken.get_price(pos.pair)
                )
                pnl_pct = (price - pos.entry_price) / pos.entry_price
                pos_data.append({
                    "asset": asset,
                    "entry_price": pos.entry_price,
                    "current_price": price,
                    "stop_price": pos.stop_price,
                    "pnl_pct": pnl_pct,
                })
            except Exception:
                pass

        self.tg.daily_report(
            positions=pos_data,
            capital=self.capital.capital,
            target=self.cfg.target_capital,
            harvest_count=self.capital.harvest_count,
            total_harvested=self.capital.total_harvested,
        )

    # ── Запуск ────────────────────────────────────────────────────

    async def health_server(self):
        """HTTP сервер для UptimeRobot — отвечает 200 OK на /health."""
        from bot.storage import using_redis

        async def handle_health(request):
            positions = load_positions()
            return web.json_response({
                "status": "ok",
                "mode": "paper" if self.cfg.paper_mode else "live",
                "capital": round(self.capital.capital, 2),
                "target": self.cfg.target_capital,
                "positions": len(positions),
                "harvests": self.capital.harvest_count,
                "storage": "redis" if using_redis() else "file",
            })

        async def handle_root(request):
            return web.Response(text="QuietHarvestBot is running 🌾")

        app = web.Application()
        app.router.add_get("/", handle_root)
        app.router.add_get("/health", handle_health)

        port = int(os.getenv("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        log.info(f"Health server: http://0.0.0.0:{port}/health")

        while True:
            await asyncio.sleep(3600)

    async def run(self):
        mode = "PAPER TRADE" if self.cfg.paper_mode else "⚠️  LIVE TRADE"
        log.info(f"=== Бот запускается ({mode}) ===")
        log.info(f"Портфель: {self.cfg.portfolio}")
        log.info(f"Max позиций: {self.cfg.max_simultaneous}")
        log.info(f"Капитал: ${self.capital.capital:.2f} / ${self.cfg.target_capital:.2f}")
        log.info(f"Снято: {self.capital.harvest_count}× = ${self.capital.total_harvested:.2f}")

        positions = load_positions()
        log.info(f"Открытых позиций: {len(positions)}")

        if self.cfg.paper_mode:
            log.info("Paper mode: API ключи не требуются — используем публичные цены")
        elif not self.kraken.has_credentials():
            log.error("Live mode требует KRAKEN_API_KEY и KRAKEN_API_SECRET в .env")
            sys.exit(1)

        self.tg.bot_started(self.capital.capital, len(positions))

        # Пять параллельных циклов
        await asyncio.gather(
            self.health_server(),
            self.signal_loop(),
            self.monitor_loop(),
            self.daily_report_loop(),
            self.cmd.polling_loop(),
        )


def main():
    """Точка входа."""
    import os
    from pathlib import Path

    # Bootstrap: загружаем историю из TSV если в хранилище (Redis/файл) мало данных
    from bot.storage import load_onchain_raw
    _history = load_onchain_raw()
    _needs_bootstrap = not _history or not all(
        len(_history.get(a, {})) >= 30
        for a in ["ETH", "BCH", "DASH", "ZEC"]
    )
    if _needs_bootstrap:
        log.info("История пустая или неполная — загружаем из TSV файлов...")
        bootstrap_all_from_tsv()

    bot = TradingBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Бот остановлен (Ctrl+C)")


if __name__ == "__main__":
    main()
