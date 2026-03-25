"""
Telegram уведомления.
"""

import logging
import requests

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, paper_mode: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.paper_mode = paper_mode
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._prefix = "📄 *[PAPER]* " if paper_mode else "🔴 *[LIVE]* "

    def send(self, text: str, silent: bool = False):
        if not self.token or not self.chat_id:
            log.warning(f"Telegram не настроен. Сообщение: {text}")
            return
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_notification": silent,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            log.error(f"Telegram ошибка: {e}")

    # ── Шаблоны сообщений ─────────────────────────────────────────

    def position_opened(self, asset: str, price: float, volume: float,
                        capital_used: float, signal_ratio: float, capital_total: float):
        emoji = "🟢"
        msg = (
            f"{self._prefix}\n"
            f"{emoji} *Вход в позицию*\n\n"
            f"Актив: `{asset}`\n"
            f"Цена входа: `${price:,.4f}`\n"
            f"Объём: `{volume:.6f} {asset}`\n"
            f"Вложено: `${capital_used:.2f}`\n"
            f"Сигнал: объём `{signal_ratio:.2f}x` от MA\n\n"
            f"💰 Капитал: `${capital_total:.2f}`"
        )
        self.send(msg)

    def position_closed(self, asset: str, entry_price: float, exit_price: float,
                        pnl_pct: float, pnl_usd: float, reason: str,
                        days_held: int, capital_total: float):
        if pnl_pct >= 0:
            emoji = "✅"
            result = "Прибыль"
        else:
            emoji = "❌"
            result = "Убыток"

        reason_map = {
            "trailing_stop": "Трейлинг стоп",
            "max_hold": "Время вышло (max hold)",
            "harvest": "Снятие прибыли",
            "manual": "Ручное закрытие",
        }

        msg = (
            f"{self._prefix}\n"
            f"{emoji} *Выход из позиции* — {result}\n\n"
            f"Актив: `{asset}`\n"
            f"Вход: `${entry_price:,.4f}`\n"
            f"Выход: `${exit_price:,.4f}`\n"
            f"P&L: `{pnl_pct:+.2%}` (`{pnl_usd:+.2f}$`)\n"
            f"Причина: {reason_map.get(reason, reason)}\n"
            f"Держали: `{days_held}` дн.\n\n"
            f"💰 Капитал: `${capital_total:.2f}`"
        )
        self.send(msg)

    def harvest_notification(self, profit: float, count: int, total_harvested: float):
        msg = (
            f"{self._prefix}\n"
            f"🎉 *СНЯТИЕ СЛИВОК #{count}!*\n\n"
            f"Прибыль этого цикла: `${profit:,.2f}`\n"
            f"Итого снято: `${total_harvested:,.2f}`\n"
            f"Перезапуск с: `$1,000.00`\n\n"
            f"Стратегия продолжает работу 🚀"
        )
        self.send(msg)

    def daily_report(self, positions: list[dict], capital: float,
                     target: float, harvest_count: int, total_harvested: float):
        if positions:
            pos_lines = []
            for p in positions:
                pnl_str = f"{p['pnl_pct']:+.2%}" if "pnl_pct" in p else "—"
                pos_lines.append(
                    f"  • `{p['asset']}` вход `${p['entry_price']:,.2f}` "
                    f"| сейчас `${p['current_price']:,.2f}` "
                    f"| P&L `{pnl_str}` "
                    f"| стоп `${p['stop_price']:,.2f}`"
                )
            pos_text = "\n".join(pos_lines)
        else:
            pos_text = "  Нет открытых позиций"

        progress_pct = (capital - 1000) / (target - 1000) * 100 if target > 1000 else 100
        progress_bar = "█" * int(progress_pct / 10) + "░" * (10 - int(progress_pct / 10))

        msg = (
            f"{self._prefix}\n"
            f"📊 *Ежедневный отчёт*\n\n"
            f"💰 Капитал: `${capital:.2f}` / `${target:.2f}`\n"
            f"[{progress_bar}] `{progress_pct:.0f}%`\n\n"
            f"📈 *Открытые позиции:*\n{pos_text}\n\n"
            f"🏆 Снятий: `{harvest_count}×` = `${total_harvested:,.2f}`"
        )
        self.send(msg, silent=True)

    def signal_scan_result(self, signals: list[str], no_action_reason: str = ""):
        if signals:
            sig_text = "\n".join(f"  • `{s}`" for s in signals)
            msg = (
                f"{self._prefix}\n"
                f"🔍 *Сканирование сигналов*\n\n"
                f"Найдено:\n{sig_text}"
            )
        else:
            msg = (
                f"{self._prefix}\n"
                f"🔍 Сигналов нет  _(ежедневное сканирование)_"
                + (f"\n{no_action_reason}" if no_action_reason else "")
            )
        self.send(msg, silent=True)

    def error_alert(self, error: str):
        msg = (
            f"{self._prefix}\n"
            f"⚠️ *Ошибка бота*\n\n"
            f"```\n{error[:500]}\n```"
        )
        self.send(msg)

    def bot_started(self, capital: float, positions_count: int):
        mode = "PAPER TRADE" if self.paper_mode else "LIVE TRADE"
        msg = (
            f"🤖 *Бот запущен* — {mode}\n\n"
            f"Портфель: ETH, BCH, DASH, ZEC\n"
            f"Капитал: `${capital:.2f}`\n"
            f"Открытых позиций: `{positions_count}`\n"
            f"Стратегия: on-chain volume breakout\n"
            f"Цель: `$2,000` → снятие прибыли"
        )
        self.send(msg)
