"""
Grid search + Walk-forward оптимизация.

Grid search: перебирает все комбинации параметров, находит лучшие по Sharpe.
Walk-forward: делит историю на train/test окна, проверяет нет ли overfitting.
"""

from __future__ import annotations
import inspect
import itertools
import logging
from typing import Any

import pandas as pd
from tqdm import tqdm

from backtest.engine import run_backtest
from backtest.metrics import compute_metrics

_BACKTEST_PARAMS = set(inspect.signature(run_backtest).parameters.keys()) - {"df", "asset_name", "initial_capital"}

log = logging.getLogger(__name__)


def grid_search(
    df: pd.DataFrame,
    asset_name: str,
    grid: dict,
    initial_capital: float = 10_000.0,
    sort_by: str = "sharpe",
    min_trades: int = 5,
) -> pd.DataFrame:
    """
    Перебирает все комбинации параметров из grid.
    Возвращает DataFrame с метриками, отсортированный по sort_by.

    grid пример:
        {
            "ma_period": [3, 7, 14],
            "threshold": [0.10, 0.15, 0.20],
            "trailing_stop_pct": [0.01, 0.02, 0.03],
            "max_hold": [7, 14, 30],
            "position_size": [0.15],
            "direction": ["long"],
        }
    """
    keys = list(grid.keys())
    values = list(grid.values())
    combos = list(itertools.product(*values))

    log.info(f"{asset_name}: grid search {len(combos)} комбинаций")

    rows = []
    for combo in tqdm(combos, desc=asset_name, leave=False):
        params = dict(zip(keys, combo))
        backtest_params = {k: v for k, v in params.items() if k in _BACKTEST_PARAMS}
        try:
            result = run_backtest(
                df=df,
                asset_name=asset_name,
                initial_capital=initial_capital,
                **backtest_params,
            )
            m = compute_metrics(result)
            if m["n_trades"] >= min_trades:
                rows.append(m)
        except Exception as e:
            log.debug(f"Skipping combo {params}: {e}")

    if not rows:
        log.warning(f"{asset_name}: no valid results")
        return pd.DataFrame()

    results_df = pd.DataFrame(rows)
    results_df = results_df.sort_values(sort_by, ascending=False).reset_index(drop=True)
    return results_df


def walk_forward(
    df: pd.DataFrame,
    asset_name: str,
    best_params: dict,
    train_months: int = 24,
    test_months: int = 6,
    initial_capital: float = 10_000.0,
) -> pd.DataFrame:
    """
    Walk-forward validation.
    Берёт лучшие параметры из grid search и проверяет их на out-of-sample данных.

    Окна: train → test → сдвиг на test_months → следующее окно.
    """
    df = df.copy()
    results = []

    step = pd.DateOffset(months=test_months)
    train_len = pd.DateOffset(months=train_months)

    start = df.index[0]
    end = df.index[-1]

    window_start = start
    window_num = 0

    while True:
        train_end = window_start + train_len
        test_end = train_end + step

        if test_end > end:
            break

        test_df = df[(df.index >= train_end) & (df.index < test_end)].copy()
        if len(test_df) < 30:
            window_start += step
            continue

        try:
            result = run_backtest(
                df=test_df,
                asset_name=asset_name,
                initial_capital=initial_capital,
                **best_params,
            )
            m = compute_metrics(result)
            m["window"] = window_num
            m["test_start"] = train_end.date()
            m["test_end"] = test_end.date()
            results.append(m)
        except Exception as e:
            log.debug(f"WF window {window_num} failed: {e}")

        window_start += step
        window_num += 1

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


def run_full_optimization(
    assets: dict[str, pd.DataFrame],
    grid: dict,
    initial_capital: float = 10_000.0,
    wf_train_months: int = 24,
    wf_test_months: int = 6,
    top_n: int = 5,
) -> dict[str, Any]:
    """
    Запускает grid search + walk-forward для всех активов.
    Возвращает:
        - top_params: лучшие параметры по активу
        - grid_results: полные таблицы grid search
        - wf_results: walk-forward результаты
    """
    grid_results = {}
    wf_results = {}
    top_params = {}

    for asset_name, df in assets.items():
        log.info(f"\n{'='*50}")
        log.info(f"Оптимизация: {asset_name}")

        gs = grid_search(df, asset_name, grid, initial_capital)
        grid_results[asset_name] = gs

        if gs.empty:
            continue

        # Берём лучшие параметры
        best = gs.iloc[0]
        params = {
            "direction": best["direction"],
            "ma_period": int(best["ma_period"]),
            "threshold": float(best["threshold"]),
            "trailing_stop_pct": float(best["trailing_stop_pct"]),
            "max_hold": int(best["max_hold"]),
            "position_size": float(best["position_size"]),
        }
        top_params[asset_name] = params

        log.info(f"{asset_name} лучшие параметры: {params}")
        log.info(f"  Sharpe={best['sharpe']:.2f}, Return={best['total_return']:.1%}, "
                 f"WinRate={best['win_rate']:.1%}, Trades={int(best['n_trades'])}")

        # Walk-forward на лучших параметрах
        wf = walk_forward(
            df, asset_name, params,
            train_months=wf_train_months,
            test_months=wf_test_months,
            initial_capital=initial_capital,
        )
        wf_results[asset_name] = wf

        if not wf.empty:
            avg_sharpe = wf["sharpe"].mean()
            avg_return = wf["total_return"].mean()
            log.info(f"{asset_name} walk-forward: avg_sharpe={avg_sharpe:.2f}, "
                     f"avg_return={avg_return:.1%} ({len(wf)} окон)")

    return {
        "grid_results": grid_results,
        "wf_results": wf_results,
        "top_params": top_params,
    }
