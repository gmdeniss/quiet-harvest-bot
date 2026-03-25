"""
Управление позициями: открытие, трейлинг стоп, закрытие.
Состояние хранится в data/positions.json (переживает рестарты).
"""

import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime

from bot.storage import (
    load_positions_raw, save_positions_raw,
    load_capital_raw, save_capital_raw,
    load_tradelog_raw, save_tradelog_raw,
)

log = logging.getLogger(__name__)


@dataclass
class Position:
    asset: str
    pair: str                  # Kraken pair (XETHZUSD)
    direction: str             # long
    entry_date: str            # ISO date
    entry_price: float
    volume: float              # кол-во монет
    deployed_capital: float    # USD в позиции при входе
    peak_price: float          # максимум с момента входа
    trailing_stop_pct: float   # 0.01 = 1%
    max_hold_days: int
    order_id: str = ""         # Kraken order ID (пусто в paper mode)

    @property
    def stop_price(self) -> float:
        """Текущий уровень трейлинг стопа."""
        return self.peak_price * (1 - self.trailing_stop_pct)

    @property
    def days_held(self) -> int:
        entry = date.fromisoformat(self.entry_date)
        return (date.today() - entry).days

    @property
    def pnl_pct(self, current_price: float = None) -> float:
        """P&L % от entry_price (нужна текущая цена)."""
        return 0.0  # вычисляется снаружи с реальной ценой


def load_positions() -> dict[str, Position]:
    data = load_positions_raw()
    return {k: Position(**v) for k, v in data.items()}


def save_positions(positions: dict[str, Position]):
    save_positions_raw({k: asdict(v) for k, v in positions.items()})


def add_position(pos: Position):
    positions = load_positions()
    positions[pos.asset] = pos
    save_positions(positions)
    log.info(f"Позиция открыта: {pos.asset} @ ${pos.entry_price:.4f}  "
             f"volume={pos.volume:.6f}  capital=${pos.deployed_capital:.2f}")


def update_peak(asset: str, current_price: float):
    """Обновляет peak_price если цена выросла."""
    positions = load_positions()
    if asset not in positions:
        return
    pos = positions[asset]
    if current_price > pos.peak_price:
        pos.peak_price = current_price
        save_positions(positions)


def remove_position(asset: str) -> Position | None:
    positions = load_positions()
    pos = positions.pop(asset, None)
    save_positions(positions)
    return pos


def log_trade(pos: Position, exit_price: float, exit_reason: str, capital_after: float):
    """Записывает закрытую сделку в лог."""
    pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
    pnl_usd = pnl_pct * pos.deployed_capital

    trade = {
        "asset": pos.asset,
        "direction": pos.direction,
        "entry_date": pos.entry_date,
        "exit_date": date.today().isoformat(),
        "exit_time": datetime.utcnow().isoformat(),
        "entry_price": pos.entry_price,
        "exit_price": exit_price,
        "volume": pos.volume,
        "deployed_capital": pos.deployed_capital,
        "pnl_pct": round(pnl_pct, 6),
        "pnl_usd": round(pnl_usd, 4),
        "capital_after": round(capital_after, 4),
        "exit_reason": exit_reason,
        "days_held": pos.days_held,
        "peak_price": pos.peak_price,
    }

    trades = load_tradelog_raw()
    trades.append(trade)
    save_tradelog_raw(trades)

    log.info(
        f"Сделка закрыта: {pos.asset}  {exit_reason}  "
        f"P&L={pnl_pct:+.2%} (${pnl_usd:+.2f})  "
        f"капитал=${capital_after:.2f}"
    )
    return trade


class CapitalTracker:
    """
    Отслеживает текущий капитал и логику снятия прибыли.
    Хранится в Redis (qhb:capital) или data/capital.json локально.
    """

    def __init__(self, initial: float, target: float):
        self.initial = initial
        self.target = target
        self._load()

    def _load(self):
        data = load_capital_raw()
        if data:
            self.capital = data["capital"]
            self.total_harvested = data["total_harvested"]
            self.harvest_count = data["harvest_count"]
            self.harvest_log = data.get("harvest_log", [])
        else:
            self.capital = self.initial
            self.total_harvested = 0.0
            self.harvest_count = 0
            self.harvest_log = []
            self._save()

    def _save(self):
        save_capital_raw({
            "capital": round(self.capital, 4),
            "total_harvested": round(self.total_harvested, 4),
            "harvest_count": self.harvest_count,
            "harvest_log": self.harvest_log,
        })

    def apply_pnl(self, pnl_usd: float) -> bool:
        """
        Применяет P&L к капиталу.
        Возвращает True если достигнута цель (нужно снять прибыль).
        """
        self.capital += pnl_usd
        self._save()
        return self.capital >= self.target

    def harvest(self) -> float:
        """Снимает прибыль, возвращает капитал к initial."""
        profit = self.capital - self.initial
        self.total_harvested += profit
        self.harvest_count += 1
        self.harvest_log.append({
            "date": date.today().isoformat(),
            "capital_at_harvest": round(self.capital, 2),
            "profit_taken": round(profit, 2),
            "harvest_n": self.harvest_count,
        })
        self.capital = self.initial
        self._save()
        log.info(f"СНЯТИЕ СЛИВОК #{self.harvest_count}: "
                 f"прибыль ${profit:.2f}, итого снято ${self.total_harvested:.2f}")
        return profit

    @property
    def summary(self) -> str:
        return (f"Капитал: ${self.capital:.2f} / ${self.target:.2f}  "
                f"Снято: {self.harvest_count}× = ${self.total_harvested:.2f}")
