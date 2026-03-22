"""
Портфельная симуляция.

Логика:
- Для каждого актива в портфеле генерируем все потенциальные сделки
  по его оптимальным параметрам
- Объединяем сигналы в единую таймлайн
- При одновременных сигналах — приоритет по силе сигнала (volume_ratio)
- Ограничение: max_simultaneous позиций одновременно
- Размер позиции: position_size_pct * текущий капитал (per slot)
- Один актив не может быть в двух позициях одновременно
"""

from __future__ import annotations
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from backtest.engine import compute_signal, simulate_trade, Trade


@dataclass
class PortfolioTrade:
    asset: str
    trade: Trade
    signal_ratio: float  # сила сигнала — для приоритизации


def generate_signal_queue(
    df: pd.DataFrame,
    asset_name: str,
    params: dict,
) -> list[PortfolioTrade]:
    """
    Генерирует список всех потенциальных сигналов для актива.
    Каждый сигнал = дата + сила (volume_ratio).
    """
    df = df.copy()
    df.attrs["asset"] = asset_name
    signal, ratio = compute_signal(df, params["ma_period"], params["threshold"])

    signals = []
    for i in range(len(df) - 1):
        if signal.iloc[i]:
            entry_idx = i + 1
            trade = simulate_trade(
                df, entry_idx,
                direction=params.get("direction", "long"),
                trailing_stop_pct=params["trailing_stop_pct"],
                max_hold=params["max_hold"],
            )
            if trade is not None:
                signals.append(PortfolioTrade(
                    asset=asset_name,
                    trade=trade,
                    signal_ratio=float(ratio.iloc[i]),
                ))
    return signals


@dataclass
class _OpenPosition:
    ps: "PortfolioTrade"
    deployed_capital: float   # капитал зафиксированный при входе


def run_portfolio_backtest(
    assets_data: dict[str, pd.DataFrame],
    asset_params: dict[str, dict],
    portfolio_assets: list[str],
    max_simultaneous: int = 2,
    position_size_pct: float = 0.15,
    initial_capital: float = 10_000.0,
) -> dict:
    """
    Симулирует портфель из нескольких активов.

    portfolio_assets:  список активов в портфеле
    max_simultaneous:  максимум одновременных позиций (по всем активам)
    position_size_pct: размер позиции от ТЕКУЩЕГО капитала при входе

    Ключевое: deployed_capital фиксируется при открытии позиции,
    P&L считается от него — никакого раздутия через общий капитал.
    """
    # Генерируем все потенциальные сделки
    all_signals: list[PortfolioTrade] = []
    for asset in portfolio_assets:
        if asset not in assets_data or asset not in asset_params:
            continue
        sigs = generate_signal_queue(assets_data[asset], asset, asset_params[asset])
        all_signals.extend(sigs)

    if not all_signals:
        return _empty_portfolio_result(portfolio_assets, max_simultaneous, initial_capital)

    # Группируем сигналы по дате входа: {entry_date: [сигналы sorted by strength desc]}
    from collections import defaultdict
    signals_by_date: dict[pd.Timestamp, list[PortfolioTrade]] = defaultdict(list)
    for ps in all_signals:
        signals_by_date[ps.trade.entry_date].append(ps)
    for date in signals_by_date:
        signals_by_date[date].sort(key=lambda s: -s.signal_ratio)

    # Все уникальные даты событий (входы + выходы)
    all_dates = sorted(set(
        list(signals_by_date.keys()) +
        [ps.trade.exit_date for ps in all_signals]
    ))

    capital = initial_capital
    open_positions: list[_OpenPosition] = []
    closed_trades: list[Trade] = []
    blocked_assets: set[str] = set()
    equity_log: list[tuple] = [(all_dates[0], initial_capital)]

    for current_date in all_dates:
        # 1. Закрываем позиции с exit_date <= current_date
        still_open = []
        for op in open_positions:
            if op.ps.trade.exit_date <= current_date:
                pnl = op.ps.trade.pnl_pct * op.deployed_capital
                capital += pnl
                closed_trades.append(op.ps.trade)
                blocked_assets.discard(op.ps.asset)
                equity_log.append((op.ps.trade.exit_date, capital))
            else:
                still_open.append(op)
        open_positions = still_open

        # 2. Открываем новые позиции на эту дату (если есть сигналы)
        if current_date in signals_by_date:
            for ps in signals_by_date[current_date]:
                if len(open_positions) >= max_simultaneous:
                    break
                if ps.asset in blocked_assets:
                    continue
                # Фиксируем капитал при открытии
                deployed = capital * position_size_pct
                open_positions.append(_OpenPosition(ps=ps, deployed_capital=deployed))
                blocked_assets.add(ps.asset)

    # Закрываем оставшиеся позиции
    for op in open_positions:
        pnl = (op.ps.trade.pnl_pct or 0.0) * op.deployed_capital
        capital += pnl
        closed_trades.append(op.ps.trade)

    # Equity curve
    equity_df = (
        pd.DataFrame(equity_log, columns=["date", "equity"])
        .drop_duplicates("date")
        .set_index("date")
        .sort_index()
    )

    return {
        "portfolio": portfolio_assets,
        "max_simultaneous": max_simultaneous,
        "position_size_pct": position_size_pct,
        "initial_capital": initial_capital,
        "final_capital": capital,
        "closed_trades": closed_trades,
        "equity": equity_df["equity"],
    }


def _empty_portfolio_result(assets, max_sim, capital):
    return {
        "portfolio": assets,
        "max_simultaneous": max_sim,
        "position_size_pct": 0.15,
        "initial_capital": capital,
        "final_capital": capital,
        "closed_trades": [],
        "equity": pd.Series(dtype=float),
    }


def portfolio_metrics(result: dict) -> dict:
    trades = result["closed_trades"]
    equity = result["equity"]
    initial = result["initial_capital"]
    final = result["final_capital"]

    if len(trades) == 0 or equity.empty:
        return {
            "portfolio": "+".join(result["portfolio"]),
            "n_assets": len(result["portfolio"]),
            "max_simultaneous": result["max_simultaneous"],
            "n_trades": 0,
            "total_return": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "final_capital": final,
        }

    pnls = np.array([t.pnl_pct for t in trades if t.pnl_pct is not None])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    total_return = (final - initial) / initial

    daily_ret = equity.resample("D").last().ffill().pct_change().dropna()
    sharpe = _sharpe(daily_ret)
    sortino = _sortino(daily_ret)

    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    max_dd = dd.min()

    win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0
    pf = (wins.sum() / abs(losses.sum())) if len(losses) > 0 and losses.sum() != 0 else np.inf

    return {
        "portfolio": "+".join(result["portfolio"]),
        "n_assets": len(result["portfolio"]),
        "max_simultaneous": result["max_simultaneous"],
        "n_trades": len(trades),
        "total_return": round(total_return, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown": round(max_dd, 4),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(min(pf, 99.0), 3),
        "final_capital": round(final, 2),
    }


def _sharpe(r: pd.Series, ann: int = 365) -> float:
    return float(r.mean() / r.std() * np.sqrt(ann)) if r.std() > 0 else 0.0


def _sortino(r: pd.Series, ann: int = 365) -> float:
    down = r[r < 0]
    return float(r.mean() / down.std() * np.sqrt(ann)) if len(down) > 0 and down.std() > 0 else 0.0


def run_all_portfolio_combinations(
    assets_data: dict[str, pd.DataFrame],
    asset_params: dict[str, dict],
    position_size_pct: float = 0.15,
    initial_capital: float = 10_000.0,
) -> pd.DataFrame:
    """
    Перебирает ВСЕ комбинации активов (1..7) × max_simultaneous (1..n_assets).
    Возвращает DataFrame с метриками, отсортированный по Sharpe.
    """
    available = [a for a in asset_params if a in assets_data]
    all_results = []

    total_combos = sum(
        len(list(combinations(available, k))) * k
        for k in range(1, len(available) + 1)
    )
    done = 0

    for n_assets in range(1, len(available) + 1):
        for asset_combo in combinations(available, n_assets):
            portfolio = list(asset_combo)
            for max_sim in range(1, n_assets + 1):
                result = run_portfolio_backtest(
                    assets_data=assets_data,
                    asset_params=asset_params,
                    portfolio_assets=portfolio,
                    max_simultaneous=max_sim,
                    position_size_pct=position_size_pct,
                    initial_capital=initial_capital,
                )
                metrics = portfolio_metrics(result)
                all_results.append(metrics)
                done += 1
                if done % 50 == 0:
                    print(f"  [{done}/{total_combos}] {metrics['portfolio']} "
                          f"max_sim={max_sim} Sharpe={metrics['sharpe']:.2f}")

    df = pd.DataFrame(all_results)
    df = df.sort_values("sharpe", ascending=False).reset_index(drop=True)
    df.index += 1
    return df
