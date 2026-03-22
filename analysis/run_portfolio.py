"""
Портфельная оптимизация: все комбинации активов и количества позиций.

Запуск:
    python -m analysis.run_portfolio
"""

import logging
import os
import sys

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.loader import load_all_assets
from backtest.portfolio import run_all_portfolio_combinations, run_portfolio_backtest, portfolio_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

RESULTS_DIR = "results"
os.makedirs(RESULTS_DIR, exist_ok=True)

plt.rcParams.update({
    "figure.facecolor": "#0f1117", "axes.facecolor": "#1a1d27",
    "axes.edgecolor": "#3a3d4a", "axes.labelcolor": "#c8ccd8",
    "xtick.color": "#8a8d9a", "ytick.color": "#8a8d9a",
    "text.color": "#c8ccd8", "grid.color": "#2a2d3a",
    "grid.linestyle": "--", "grid.alpha": 0.5, "font.size": 10,
})


def load_best_params() -> dict[str, dict]:
    """Загружает лучшие параметры из grid search результатов."""
    params = {}
    for asset in ["BTC", "ETH", "LTC", "BCH", "DASH", "DOGE", "ZEC"]:
        path = f"{RESULTS_DIR}/grid_{asset.lower()}.csv"
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        best = df.iloc[0]
        params[asset] = {
            "direction": best.get("direction", "long"),
            "ma_period": int(best["ma_period"]),
            "threshold": float(best["threshold"]),
            "trailing_stop_pct": float(best["trailing_stop_pct"]),
            "max_hold": int(best["max_hold"]),
            "position_size": float(best["position_size"]),
        }
        log.info(f"  {asset}: MA={params[asset]['ma_period']}d "
                 f"thr={params[asset]['threshold']:.0%} "
                 f"ts={params[asset]['trailing_stop_pct']:.1%}")
    return params


def plot_portfolio_results(df: pd.DataFrame):
    """5 графиков для анализа портфельных результатов."""

    fig = plt.figure(figsize=(20, 24))
    gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.4, wspace=0.3)
    fig.suptitle("Портфельная оптимизация — все комбинации активов", fontsize=15, y=0.98)

    colors = ["#00d4ff", "#00ff88", "#ff6b6b", "#ffd93d", "#c77dff", "#ff9a3c", "#4ecdc4"]

    # ── 1. Sharpe по количеству активов в портфеле ──
    ax1 = fig.add_subplot(gs[0, 0])
    grouped = df.groupby("n_assets")["sharpe"]
    bp_data = [grouped.get_group(n).values for n in sorted(grouped.groups)]
    bp = ax1.boxplot(bp_data, patch_artist=True, medianprops={"color": "white", "linewidth": 2})
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax1.set_xticks(range(1, len(bp_data) + 1))
    ax1.set_xticklabels([f"{n} актив{'а' if n in [2,3,4] else 'ов' if n > 4 else ''}"
                         for n in sorted(grouped.groups)])
    ax1.set_title("Sharpe vs количество активов")
    ax1.set_ylabel("Sharpe Ratio")
    ax1.grid(True, axis="y")

    # ── 2. Sharpe по max_simultaneous позиций ──
    ax2 = fig.add_subplot(gs[0, 1])
    grouped2 = df.groupby("max_simultaneous")["sharpe"]
    bp_data2 = [grouped2.get_group(n).values for n in sorted(grouped2.groups)]
    bp2 = ax2.boxplot(bp_data2, patch_artist=True, medianprops={"color": "white", "linewidth": 2})
    for patch, color in zip(bp2["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax2.set_xticks(range(1, len(bp_data2) + 1))
    ax2.set_xticklabels([f"max={n}" for n in sorted(grouped2.groups)])
    ax2.set_title("Sharpe vs макс. одновременных позиций")
    ax2.set_ylabel("Sharpe Ratio")
    ax2.grid(True, axis="y")

    # ── 3. Heatmap: avg Sharpe по (n_assets × max_simultaneous) ──
    ax3 = fig.add_subplot(gs[1, :])
    pivot = df.groupby(["n_assets", "max_simultaneous"])["sharpe"].mean().unstack("max_simultaneous")
    sns.heatmap(
        pivot, annot=True, fmt=".2f", cmap="RdYlGn",
        ax=ax3, linewidths=0.5, cbar_kws={"shrink": 0.6},
        annot_kws={"size": 11},
    )
    ax3.set_title("Средний Sharpe: кол-во активов × одновременных позиций", fontsize=12)
    ax3.set_xlabel("Макс. одновременных позиций")
    ax3.set_ylabel("Активов в портфеле")

    # ── 4. Топ-20 портфелей по Sharpe ──
    ax4 = fig.add_subplot(gs[2, 0])
    top20 = df.head(20).copy()
    top20["label"] = top20["portfolio"] + f"\n(sim={top20['max_simultaneous'].astype(str)})"
    bars = ax4.barh(range(len(top20)), top20["sharpe"].values, color="#00d4ff", alpha=0.8)
    ax4.set_yticks(range(len(top20)))
    ax4.set_yticklabels(top20["portfolio"] + " [" + top20["max_simultaneous"].astype(str) + "]",
                        fontsize=7)
    ax4.invert_yaxis()
    ax4.set_title("Топ-20 портфелей по Sharpe")
    ax4.set_xlabel("Sharpe Ratio")
    ax4.grid(True, axis="x")
    for i, (sh, ret) in enumerate(zip(top20["sharpe"], top20["total_return"])):
        ax4.text(sh + 0.01, i, f"  ret={ret:.0%}", va="center", fontsize=7, color="#ffd93d")

    # ── 5. Return vs Sharpe (scatter) с раскраской по n_assets ──
    ax5 = fig.add_subplot(gs[2, 1])
    for n in sorted(df["n_assets"].unique()):
        sub = df[df["n_assets"] == n]
        ax5.scatter(sub["sharpe"], sub["total_return"] * 100,
                    alpha=0.5, s=20, color=colors[n - 1], label=f"{n} актив(ов)")
    # Топ-5 подписываем
    for _, row in df.head(5).iterrows():
        ax5.annotate(row["portfolio"][:20],
                     (row["sharpe"], row["total_return"] * 100),
                     fontsize=6, color="white", alpha=0.9)
    ax5.set_xlabel("Sharpe Ratio")
    ax5.set_ylabel("Total Return (%)")
    ax5.set_title("Return vs Sharpe (все комбинации)")
    ax5.legend(fontsize=8, loc="upper left")
    ax5.grid(True)

    plt.savefig(f"{RESULTS_DIR}/portfolio_analysis.png", dpi=150,
                bbox_inches="tight", facecolor="#0f1117")
    plt.close()
    print(f"  График: {RESULTS_DIR}/portfolio_analysis.png")


def main():
    log.info("=== Загрузка данных ===")
    assets = load_all_assets()

    log.info("=== Загрузка лучших параметров ===")
    best_params = load_best_params()

    if not best_params:
        log.error("Нет grid_*.csv в results/ — сначала запусти run_optimization.py")
        return

    log.info(f"=== Портфельная оптимизация: {len(best_params)} активов ===")
    log.info("Перебираем все комбинации (127 портфелей × max_simultaneous)...")

    results_df = run_all_portfolio_combinations(
        assets_data=assets,
        asset_params=best_params,
        position_size_pct=0.15,
        initial_capital=10_000.0,
    )

    # Сохранение
    results_df.to_csv(f"{RESULTS_DIR}/portfolio_all.csv", index=True)
    log.info(f"Сохранено: {RESULTS_DIR}/portfolio_all.csv ({len(results_df)} строк)")

    # Визуализация
    plot_portfolio_results(results_df)

    # ── Финальный отчёт ──
    print("\n" + "=" * 70)
    print("ПОРТФЕЛЬНАЯ ОПТИМИЗАЦИЯ — РЕЗУЛЬТАТЫ")
    print("=" * 70)

    print("\n### ТОП-10 ЛУЧШИХ ПОРТФЕЛЕЙ (по Sharpe):\n")
    cols = ["portfolio", "n_assets", "max_simultaneous", "n_trades",
            "total_return", "sharpe", "win_rate", "max_drawdown"]
    top10 = results_df.head(10)[cols].copy()
    top10["total_return"] = top10["total_return"].map("{:.0%}".format)
    top10["win_rate"] = top10["win_rate"].map("{:.1%}".format)
    top10["max_drawdown"] = top10["max_drawdown"].map("{:.2%}".format)
    top10["sharpe"] = top10["sharpe"].map("{:.3f}".format)
    print(top10.to_string(index=True))

    print("\n### АНАЛИЗ ПО КОЛИЧЕСТВУ АКТИВОВ:\n")
    by_n = results_df.groupby("n_assets").agg(
        avg_sharpe=("sharpe", "mean"),
        max_sharpe=("sharpe", "max"),
        avg_return=("total_return", "mean"),
        n_combos=("portfolio", "count"),
    ).round(3)
    print(by_n.to_string())

    print("\n### АНАЛИЗ ПО КОЛИЧЕСТВУ ОДНОВРЕМЕННЫХ ПОЗИЦИЙ:\n")
    by_sim = results_df.groupby("max_simultaneous").agg(
        avg_sharpe=("sharpe", "mean"),
        max_sharpe=("sharpe", "max"),
        avg_return=("total_return", "mean"),
        n_combos=("portfolio", "count"),
    ).round(3)
    print(by_sim.to_string())

    print("\n### ЛУЧШИЙ ПОРТФЕЛЬ ДЕТАЛЬНО:\n")
    best = results_df.iloc[0]
    print(f"  Состав: {best['portfolio']}")
    print(f"  Активов: {best['n_assets']}")
    print(f"  Макс. одновременных позиций: {best['max_simultaneous']}")
    print(f"  Sharpe: {best['sharpe']:.3f}")
    print(f"  Return: {best['total_return']:.1%}")
    print(f"  Win Rate: {best['win_rate']:.1%}")
    print(f"  Max Drawdown: {best['max_drawdown']:.2%}")
    print(f"  Сделок: {best['n_trades']}")

    print(f"\nВсе результаты: {RESULTS_DIR}/portfolio_all.csv")


if __name__ == "__main__":
    main()
