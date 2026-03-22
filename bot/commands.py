"""
Telegram команды бота — polling + обработчики.
"""

import asyncio
import logging
import requests
from datetime import date

log = logging.getLogger(__name__)

COMMANDS = [
    ("status",    "💰 Капитал, позиции, снятия"),
    ("positions", "📈 Открытые позиции с P&L"),
    ("signal",    "🔍 Проверить сигналы сейчас"),
    ("history",   "📋 Последние 5 сделок"),
    ("stop",      "🛑 Закрыть все позиции"),
    ("help",      "❓ Список команд"),
]


class CommandHandler:
    def __init__(self, bot):
        self.bot = bot
        self.tg = bot.tg
        self.cfg = bot.cfg
        self._offset = 0
        self._base = f"https://api.telegram.org/bot{self.cfg.telegram_token}"

    # ── Регистрация команд и меню ─────────────────────────────────

    def register(self):
        """Регистрирует команды в Telegram (появляются в меню '/')."""
        commands = [{"command": c, "description": d} for c, d in COMMANDS]
        try:
            requests.post(f"{self._base}/setMyCommands",
                          json={"commands": commands}, timeout=10)
            # Кнопка меню слева от поля ввода
            requests.post(f"{self._base}/setChatMenuButton",
                          json={"menu_button": {"type": "commands"}}, timeout=10)
            log.info("Команды Telegram зарегистрированы")
        except Exception as e:
            log.warning(f"Не удалось зарегистрировать команды: {e}")

    # ── Polling loop ──────────────────────────────────────────────

    async def polling_loop(self):
        log.info("Telegram polling запущен")
        self.register()
        while True:
            try:
                await self._poll()
            except Exception as e:
                log.error(f"Polling ошибка: {e}")
            await asyncio.sleep(2)

    async def _poll(self):
        resp = requests.get(
            f"{self._base}/getUpdates",
            params={"offset": self._offset, "timeout": 20, "limit": 10},
            timeout=25,
        )
        data = resp.json()
        if not data.get("ok"):
            return

        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()

            # Только от авторизованного пользователя
            if chat_id != self.cfg.telegram_chat_id:
                continue
            if not text.startswith("/"):
                continue

            cmd = text.split()[0].lstrip("/").split("@")[0].lower()
            await self._handle(cmd)

    # ── Обработчики команд ────────────────────────────────────────

    async def _handle(self, cmd: str):
        handlers = {
            "start":     self._cmd_status,
            "status":    self._cmd_status,
            "positions": self._cmd_positions,
            "signal":    self._cmd_signal,
            "history":   self._cmd_history,
            "stop":      self._cmd_stop,
            "help":      self._cmd_help,
        }
        fn = handlers.get(cmd, self._cmd_unknown)
        await fn()

    async def _cmd_status(self):
        from bot.positions import load_positions
        positions = load_positions()
        cap = self.bot.capital

        # Прогресс-бар
        pct = (cap.capital - cap.initial) / (cap.target - cap.initial) * 100
        pct = max(0, min(100, pct))
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)

        pos_text = f"{len(positions)} открыта" if len(positions) == 1 else \
                   f"{len(positions)} открыто" if len(positions) > 0 else "нет"

        msg = (
            f"📄 *[PAPER]* 💰 *Статус*\n\n"
            f"Капитал: `${cap.capital:.2f}` / `${cap.target:.2f}`\n"
            f"[{bar}] `{pct:.0f}%` до цели\n\n"
            f"Позиций: {pos_text}\n"
            f"Снятий: `{cap.harvest_count}×` = `${cap.total_harvested:,.2f}`\n"
            f"Осталось до удвоения: `${cap.target - cap.capital:.2f}`"
        )
        self.tg.send(msg)

    async def _cmd_positions(self):
        from bot.positions import load_positions
        positions = load_positions()

        if not positions:
            self.tg.send("📄 *[PAPER]* 📈 *Позиций нет*\n\nВсё в кэше, ждём сигнала.")
            return

        lines = []
        for asset, pos in positions.items():
            cfg = self.cfg.assets.get(asset)
            if not cfg:
                continue
            try:
                price = self.bot.kraken.get_price(cfg.kraken_pair)
                pnl_pct = (price - pos.entry_price) / pos.entry_price
                pnl_usd = pnl_pct * pos.deployed_capital
                emoji = "📈" if pnl_pct >= 0 else "📉"
                lines.append(
                    f"{emoji} *{asset}*\n"
                    f"  Вход: `${pos.entry_price:,.4f}`\n"
                    f"  Сейчас: `${price:,.4f}`\n"
                    f"  P&L: `{pnl_pct:+.2%}` (`{pnl_usd:+.2f}$`)\n"
                    f"  Стоп: `${pos.stop_price:,.4f}`\n"
                    f"  Дней: `{pos.days_held}` / `{pos.max_hold_days}`"
                )
            except Exception:
                lines.append(f"• *{asset}* — ошибка получения цены")

        self.tg.send(f"📄 *[PAPER]* 📈 *Открытые позиции*\n\n" + "\n\n".join(lines))

    async def _cmd_signal(self):
        self.tg.send("📄 *[PAPER]* 🔍 Проверяю сигналы...")
        try:
            from bot.onchain import update_history
            from bot.strategy import check_all_signals
            update_history(list(self.cfg.assets.keys()))
            signals = check_all_signals(self.cfg.assets)

            if signals:
                lines = [f"• *{s.asset}*: `{s.ratio:.2f}x` от MA" for s in signals]
                self.tg.send(
                    f"📄 *[PAPER]* 🔍 *Сигналы найдены:*\n\n" + "\n".join(lines)
                )
                # Открываем позиции если есть слоты
                await self.bot._run_signal_check()
            else:
                self.tg.send("📄 *[PAPER]* 🔍 Сигналов нет — объём в норме.")
        except Exception as e:
            self.tg.send(f"📄 *[PAPER]* ⚠️ Ошибка: `{e}`")

    async def _cmd_history(self):
        import json
        from pathlib import Path
        log_path = Path("data/trade_log.json")

        trades = []
        if log_path.exists():
            with open(log_path) as f:
                trades = json.load(f)

        if not trades:
            self.tg.send("📄 *[PAPER]* 📋 *История пуста* — сделок ещё не было.")
            return

        last5 = trades[-5:][::-1]
        lines = []
        for t in last5:
            emoji = "✅" if t["pnl_pct"] >= 0 else "❌"
            lines.append(
                f"{emoji} *{t['asset']}* {t['exit_date']}\n"
                f"  {t['pnl_pct']:+.2%} | `{t['pnl_usd']:+.2f}$` | {t['exit_reason']}"
            )

        self.tg.send(
            f"📄 *[PAPER]* 📋 *Последние сделки*\n\n" + "\n\n".join(lines)
        )

    async def _cmd_stop(self):
        from bot.positions import load_positions
        positions = load_positions()

        if not positions:
            self.tg.send("📄 *[PAPER]* 🛑 Нет открытых позиций.")
            return

        self.tg.send(
            f"📄 *[PAPER]* 🛑 *Закрываю все позиции...*\n"
            f"Активов: {len(positions)}"
        )
        for asset in list(positions.keys()):
            self.bot.close_position(asset, "manual")

    async def _cmd_help(self):
        lines = [f"/{c} — {d}" for c, d in COMMANDS]
        self.tg.send(
            "📄 *[PAPER]* ❓ *Команды QuietHarvestBot*\n\n" + "\n".join(lines)
        )

    async def _cmd_unknown(self):
        self.tg.send("Не знаю такой команды. Напиши /help")
