"""
Бэктест движок для стратегии on-chain volume breakout.

Сигнал: onchain_volume / MA(onchain_volume, period) > (1 + threshold)
Вход:   следующий день, по цене open
Выход:  trailing stop ИЛИ max_hold дней
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field


@dataclass
class Trade:
    asset: str
    entry_date: pd.Timestamp
    entry_price: float
    direction: str          # 'long' | 'short'
    position_size: float    # доля капитала
    trailing_stop_pct: float
    max_hold: int

    exit_date: pd.Timestamp = None
    exit_price: float = None
    exit_reason: str = None  # 'trailing_stop' | 'max_hold' | 'end_of_data'

    @property
    def pnl_pct(self) -> float:
        if self.exit_price is None:
            return None
        if self.direction == "long":
            return (self.exit_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - self.exit_price) / self.entry_price

    @property
    def hold_days(self) -> int:
        if self.exit_date is None:
            return None
        return (self.exit_date - self.entry_date).days


def compute_signal(
    df: pd.DataFrame,
    ma_period: int,
    threshold: float,
) -> pd.Series:
    """
    Возвращает bool Series: True = сигнал на вход в следующий день.
    volume_ratio = onchain_volume / MA(onchain_volume, ma_period)
    signal = volume_ratio > (1 + threshold)
    """
    ma = df["onchain_volume"].rolling(ma_period, min_periods=ma_period).mean()
    ratio = df["onchain_volume"] / ma
    signal = ratio > (1 + threshold)
    return signal, ratio


def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    trailing_stop_pct: float,
    max_hold: int,
) -> Trade | None:
    """
    Симулирует одну сделку начиная с entry_idx (вход по open).
    Выходит по trailing stop или max_hold.
    """
    if entry_idx >= len(df):
        return None

    entry_row = df.iloc[entry_idx]
    entry_price = entry_row["open"]
    if entry_price <= 0 or np.isnan(entry_price):
        return None

    trade = Trade(
        asset=df.attrs.get("asset", ""),
        entry_date=entry_row.name,
        entry_price=entry_price,
        direction=direction,
        position_size=0.0,  # заполняется снаружи
        trailing_stop_pct=trailing_stop_pct,
        max_hold=max_hold,
    )

    # Трейлинг стоп: отслеживаем лучшую цену с момента входа
    best_price = entry_price

    for i in range(entry_idx, min(entry_idx + max_hold, len(df))):
        row = df.iloc[i]
        high = row["high"]
        low = row["low"]
        close = row["close"]

        if direction == "long":
            best_price = max(best_price, high)
            stop_level = best_price * (1 - trailing_stop_pct)
            hit_stop = low <= stop_level
        else:  # short
            best_price = min(best_price, low)
            stop_level = best_price * (1 + trailing_stop_pct)
            hit_stop = high >= stop_level

        if hit_stop:
            trade.exit_date = row.name
            trade.exit_price = stop_level
            trade.exit_reason = "trailing_stop"
            return trade

    # Вышли по max_hold
    last_idx = min(entry_idx + max_hold - 1, len(df) - 1)
    last_row = df.iloc[last_idx]
    trade.exit_date = last_row.name
    trade.exit_price = last_row["close"]
    trade.exit_reason = "max_hold"
    return trade


def run_backtest(
    df: pd.DataFrame,
    asset_name: str,
    direction: str = "long",
    ma_period: int = 3,
    threshold: float = 0.15,
    trailing_stop_pct: float = 0.01,
    max_hold: int = 30,
    position_size: float = 0.15,
    initial_capital: float = 10_000.0,
    allow_overlap: bool = False,
) -> dict:
    """
    Прогоняет стратегию на одном активе.
    allow_overlap=False: не входим если уже в позиции.

    Возвращает dict с результатами и списком сделок.
    """
    df = df.copy()
    df.attrs["asset"] = asset_name

    signal, ratio = compute_signal(df, ma_period, threshold)

    trades: list[Trade] = []
    capital = initial_capital
    equity_curve = pd.Series(index=df.index, dtype=float)
    equity_curve.iloc[0] = capital

    active_until: int = -1  # индекс до которого занята позиция

    for i in range(len(df) - 1):
        equity_curve.iloc[i] = capital

        # Сигнал сегодня → вход завтра
        if not signal.iloc[i]:
            continue
        if not allow_overlap and i <= active_until:
            continue

        entry_idx = i + 1
        trade = simulate_trade(df, entry_idx, direction, trailing_stop_pct, max_hold)
        if trade is None:
            continue

        trade.position_size = position_size
        trade_capital = capital * position_size
        pnl = trade.pnl_pct * trade_capital
        capital += pnl
        trades.append(trade)

        # Блокируем параллельные входы
        exit_pos = df.index.get_loc(trade.exit_date)
        active_until = exit_pos

    equity_curve.iloc[-1] = capital

    # Заполняем equity_curve между сделками
    equity_curve = equity_curve.ffill()

    return {
        "asset": asset_name,
        "params": {
            "direction": direction,
            "ma_period": ma_period,
            "threshold": threshold,
            "trailing_stop_pct": trailing_stop_pct,
            "max_hold": max_hold,
            "position_size": position_size,
        },
        "trades": trades,
        "equity_curve": equity_curve,
        "final_capital": capital,
    }
