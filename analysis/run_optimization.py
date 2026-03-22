"""
Главный скрипт оптимизации стратегии.

Запуск:
    python -m analysis.run_optimization

Что делает:
1. Загружает данные (on-chain TSV + OHLCV из Yahoo)
2. Grid search по всем параметрам для каждого актива
3. Walk-forward validation
4. Корреляционный анализ сигналов
5. Ранжирование активов
6. Сохраняет результаты в results/
"""

import logging
import os
import sys

import pandas as pd
import yaml

# Путь к корню проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.loader import load_all_assets
from backtest.optimizer import run_full_optimization
from analysis.correlation import signal_correlation_report, asset_ranking, portfolio_correlation_check
from analysis.visualize import generate_all_charts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

RESULTS_DIR = "results"
os.makedirs(RESULTS_DIR, exist_ok=True)


def main():
    # --- Загрузка конфига ---
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    grid = cfg["grid"]
    initial_capital = cfg["initial_capital"]
    wf = cfg["walk_forward"]

    log.info("=== Загрузка данных ===")
    assets = load_all_assets()

    if not assets:
        log.error("Нет данных — проверь TSV файлы и интернет-соединение")
        return

    log.info(f"Загружено активов: {list(assets.keys())}")

    # --- Корреляционный анализ сигналов ---
    log.info("\n=== Корреляционный анализ ===")
    corr = signal_correlation_report(
        assets,
        ma_period=7,
        threshold=0.15,
        save_path=f"{RESULTS_DIR}/signal_correlation.png",
    )
    corr.to_csv(f"{RESULTS_DIR}/signal_correlation.csv")

    # --- Grid search + Walk-forward ---
    log.info("\n=== Оптимизация ===")
    opt = run_full_optimization(
        assets=assets,
        grid=grid,
        initial_capital=initial_capital,
        wf_train_months=wf["train_months"],
        wf_test_months=wf["test_months"],
    )

    # --- Сохранение результатов ---
    log.info("\n=== Сохранение результатов ===")

    all_top = []
    for asset, gs_df in opt["grid_results"].items():
        if gs_df.empty:
            continue
        path = f"{RESULTS_DIR}/grid_{asset.lower()}.csv"
        gs_df.to_csv(path, index=False)
        log.info(f"  {asset}: grid → {path}")
        all_top.append(gs_df.head(10))

    for asset, wf_df in opt["wf_results"].items():
        if wf_df.empty:
            continue
        path = f"{RESULTS_DIR}/walkforward_{asset.lower()}.csv"
        wf_df.to_csv(path, index=False)
        log.info(f"  {asset}: walk-forward → {path}")

    # --- Ранжирование активов ---
    ranking = asset_ranking(opt["grid_results"], top_n=3)
    ranking.to_csv(f"{RESULTS_DIR}/asset_ranking.csv", index=False)

    # --- Финальный отчёт ---
    print("\n" + "=" * 60)
    print("ФИНАЛЬНЫЙ ОТЧЁТ")
    print("=" * 60)

    print("\n### Топ-5 параметров по каждому активу (по Sharpe):\n")
    for asset, gs_df in opt["grid_results"].items():
        if gs_df.empty:
            continue
        print(f"\n{asset}:")
        cols = ["ma_period", "threshold", "trailing_stop_pct", "max_hold",
                "position_size", "n_trades", "total_return", "sharpe",
                "win_rate", "max_drawdown"]
        cols = [c for c in cols if c in gs_df.columns]
        print(gs_df.head(5)[cols].to_string(index=False))

    print("\n### Walk-forward сводка:\n")
    for asset, wf_df in opt["wf_results"].items():
        if wf_df.empty:
            continue
        print(f"  {asset}: avg_sharpe={wf_df['sharpe'].mean():.2f}, "
              f"avg_return={wf_df['total_return'].mean():.1%}, "
              f"окон={len(wf_df)}, "
              f"прибыльных={( wf_df['total_return'] > 0).sum()}/{len(wf_df)}")

    print("\n### Оптимальные параметры:\n")
    for asset, params in opt["top_params"].items():
        print(f"  {asset}: {params}")

    # Проверка корреляции для портфельного подбора
    top3_assets = ranking.head(3)["asset"].tolist()
    print(f"\n### Проверка корреляции топ-3 активов {top3_assets}:\n")
    final_assets = portfolio_correlation_check(assets, top3_assets)
    print(f"  Рекомендуемый портфель: {final_assets}")

    # --- Визуализация ---
    generate_all_charts(
        assets=assets,
        grid_results=opt["grid_results"],
        wf_results=opt["wf_results"],
        top_params=opt["top_params"],
        initial_capital=initial_capital,
    )

    print(f"\nВсе результаты в папке: {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
