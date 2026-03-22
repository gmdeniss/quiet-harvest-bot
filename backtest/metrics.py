"""
Метрики качества стратегии.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from backtest.engine import Trade


def compute_metrics(result: dict) -> dict:
    trades: list[Trade] = result["trades"]
    equity: pd.Series = result["equity_curve"]
    initial = equity.iloc[0]
    final = result["final_capital"]

    if len(trades) == 0:
        return _empty_metrics(result)

    pnls = np.array([t.pnl_pct for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    # Доходность
    total_return = (final - initial) / initial

    # Дневные доходности для Sharpe
    daily_returns = equity.pct_change().dropna()
    sharpe = _sharpe(daily_returns)
    sortino = _sortino(daily_returns)

    # Просадка
    roll_max = equity.cummax()
    drawdown = (equity - roll_max) / roll_max
    max_dd = drawdown.min()

    # Статистика сделок
    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = losses.mean() if len(losses) > 0 else 0.0
    win_rate = len(wins) / len(trades)
    profit_factor = (
        (wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else np.inf
    )
    avg_hold = np.mean([t.hold_days for t in trades if t.hold_days is not None])

    # Причины выхода
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    return {
        "asset": result["asset"],
        **result["params"],
        "n_trades": len(trades),
        "total_return": round(total_return, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown": round(max_dd, 4),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(profit_factor, 3),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "avg_hold_days": round(avg_hold, 1),
        "exit_by_stop": reasons.get("trailing_stop", 0),
        "exit_by_hold": reasons.get("max_hold", 0),
        "final_capital": round(final, 2),
    }


def _sharpe(daily_returns: pd.Series, periods: int = 365) -> float:
    if daily_returns.std() == 0:
        return 0.0
    return float(daily_returns.mean() / daily_returns.std() * np.sqrt(periods))


def _sortino(daily_returns: pd.Series, periods: int = 365) -> float:
    downside = daily_returns[daily_returns < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(daily_returns.mean() / downside.std() * np.sqrt(periods))


def _empty_metrics(result: dict) -> dict:
    return {
        "asset": result["asset"],
        **result["params"],
        "n_trades": 0,
        "total_return": 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "max_drawdown": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "avg_win_pct": 0.0,
        "avg_loss_pct": 0.0,
        "avg_hold_days": 0.0,
        "exit_by_stop": 0,
        "exit_by_hold": 0,
        "final_capital": result["final_capital"],
    }
