"""
Визуализация результатов оптимизации.

Генерирует:
  1. Equity curves + drawdown по каждому активу
  2. Heatmap: Sharpe vs (MA period × threshold)
  3. Heatmap: Sharpe vs (trailing_stop × max_hold)
  4. Сравнение активов: Sharpe, win rate, return
  5. Walk-forward stability (Sharpe по окнам)
"""

from __future__ import annotations
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns

from backtest.engine import run_backtest

plt.rcParams.update({
    "figure.facecolor": "#0f1117",
    "axes.facecolor": "#1a1d27",
    "axes.edgecolor": "#3a3d4a",
    "axes.labelcolor": "#c8ccd8",
    "xtick.color": "#8a8d9a",
    "ytick.color": "#8a8d9a",
    "text.color": "#c8ccd8",
    "grid.color": "#2a2d3a",
    "grid.linestyle": "--",
    "grid.alpha": 0.5,
    "lines.linewidth": 1.5,
    "font.size": 10,
})

RESULTS_DIR = "results"


# ─── 1. Equity + Drawdown ────────────────────────────────────────────────────

def plot_equity_curves(
    assets: dict[str, pd.DataFrame],
    top_params: dict[str, dict],
    initial_capital: float = 10_000.0,
    save_path: str = f"{RESULTS_DIR}/equity_curves.png",
):
    n = len(top_params)
    fig, axes = plt.subplots(n, 2, figsize=(18, 4 * n))
    if n == 1:
        axes = [axes]

    fig.suptitle("Equity Curves & Drawdown — оптимальные параметры", fontsize=14, y=1.01)

    colors = ["#00d4ff", "#00ff88", "#ff6b6b", "#ffd93d", "#c77dff", "#ff9a3c", "#4ecdc4"]

    for idx, (asset, params) in enumerate(top_params.items()):
        if asset not in assets:
            continue
        df = assets[asset]
        color = colors[idx % len(colors)]

        result = run_backtest(df, asset, initial_capital=initial_capital, **params)
        equity = result["equity_curve"].ffill()

        # Equity
        ax_eq = axes[idx][0]
        ax_eq.plot(equity.index, equity.values, color=color, linewidth=1.5)
        ax_eq.axhline(initial_capital, color="#555", linestyle=":", linewidth=1)
        ax_eq.set_title(f"{asset} — Equity  (Sharpe ≈ указан в grid)", color=color)
        ax_eq.set_ylabel("Капитал ($)")
        ax_eq.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax_eq.grid(True)

        # Метка итогового результата
        final = equity.iloc[-1]
        ret = (final - initial_capital) / initial_capital
        ax_eq.text(0.02, 0.95, f"Return: {ret:+.1%}  |  {result['trades'].__len__()} сделок",
                   transform=ax_eq.transAxes, fontsize=9,
                   verticalalignment="top", color=color)

        # Drawdown
        ax_dd = axes[idx][1]
        roll_max = equity.cummax()
        drawdown = (equity - roll_max) / roll_max * 100
        ax_dd.fill_between(drawdown.index, drawdown.values, 0,
                           color=color, alpha=0.3)
        ax_dd.plot(drawdown.index, drawdown.values, color=color, linewidth=1)
        ax_dd.set_title(f"{asset} — Drawdown", color=color)
        ax_dd.set_ylabel("Drawdown (%)")
        ax_dd.grid(True)

        max_dd = drawdown.min()
        ax_dd.text(0.02, 0.05, f"Max DD: {max_dd:.2f}%",
                   transform=ax_dd.transAxes, fontsize=9,
                   verticalalignment="bottom", color="#ff6b6b")

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    plt.close()
    print(f"  Сохранено: {save_path}")


# ─── 2. Heatmap: Sharpe vs параметры ────────────────────────────────────────

def plot_param_heatmaps(
    grid_results: dict[str, pd.DataFrame],
    save_path: str = f"{RESULTS_DIR}/param_heatmaps.png",
):
    assets = [a for a, df in grid_results.items() if not df.empty]
    if not assets:
        return

    n = len(assets)
    fig, axes = plt.subplots(n, 2, figsize=(16, 5 * n))
    if n == 1:
        axes = [axes]

    fig.suptitle("Sharpe по параметрам (усреднено)", fontsize=14)

    for idx, asset in enumerate(assets):
        df = grid_results[asset]

        # Heatmap 1: MA period × Threshold
        pivot1 = (
            df.groupby(["ma_period", "threshold"])["sharpe"]
            .mean()
            .unstack("threshold")
        )
        sns.heatmap(
            pivot1,
            annot=True, fmt=".2f",
            cmap="RdYlGn",
            ax=axes[idx][0],
            linewidths=0.5,
            cbar_kws={"shrink": 0.8},
        )
        axes[idx][0].set_title(f"{asset}: Sharpe vs MA period × Threshold")
        axes[idx][0].set_xlabel("Threshold")
        axes[idx][0].set_ylabel("MA Period (дней)")

        # Heatmap 2: Trailing Stop × Max Hold
        pivot2 = (
            df.groupby(["trailing_stop_pct", "max_hold"])["sharpe"]
            .mean()
            .unstack("max_hold")
        )
        sns.heatmap(
            pivot2,
            annot=True, fmt=".2f",
            cmap="RdYlGn",
            ax=axes[idx][1],
            linewidths=0.5,
            cbar_kws={"shrink": 0.8},
        )
        axes[idx][1].set_title(f"{asset}: Sharpe vs Trailing Stop × Max Hold")
        axes[idx][1].set_xlabel("Max Hold (дней)")
        axes[idx][1].set_ylabel("Trailing Stop (%)")
        axes[idx][1].set_yticklabels([f"{float(t.get_text()):.1%}"
                                       for t in axes[idx][1].get_yticklabels()])

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Сохранено: {save_path}")


# ─── 3. Сравнение активов ────────────────────────────────────────────────────

def plot_asset_comparison(
    grid_results: dict[str, pd.DataFrame],
    save_path: str = f"{RESULTS_DIR}/asset_comparison.png",
):
    rows = []
    for asset, df in grid_results.items():
        if df.empty:
            continue
        best = df.iloc[0]
        rows.append({
            "asset": asset,
            "sharpe": best["sharpe"],
            "win_rate": best["win_rate"] * 100,
            "total_return": best["total_return"] * 100,
            "max_drawdown": abs(best["max_drawdown"]) * 100,
            "n_trades": best["n_trades"],
        })

    if not rows:
        return

    summary = pd.DataFrame(rows).set_index("asset")
    summary = summary.sort_values("sharpe", ascending=True)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Сравнение активов — лучшие параметры", fontsize=14)

    metrics = [
        ("sharpe", "Sharpe Ratio", "#00d4ff"),
        ("win_rate", "Win Rate (%)", "#00ff88"),
        ("total_return", "Total Return (%)", "#ffd93d"),
        ("max_drawdown", "Max Drawdown (%)", "#ff6b6b"),
    ]

    for ax, (col, label, color) in zip(axes.flat, metrics):
        bars = ax.barh(summary.index, summary[col], color=color, alpha=0.8)
        ax.set_title(label)
        ax.set_xlabel(label)
        ax.grid(True, axis="x")
        for bar, val in zip(bars, summary[col]):
            ax.text(val + summary[col].max() * 0.01, bar.get_y() + bar.get_height() / 2,
                    f"{val:.2f}", va="center", fontsize=9)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    plt.close()
    print(f"  Сохранено: {save_path}")


# ─── 4. Walk-forward stability ───────────────────────────────────────────────

def plot_walkforward(
    wf_results: dict[str, pd.DataFrame],
    save_path: str = f"{RESULTS_DIR}/walkforward.png",
):
    valid = {a: df for a, df in wf_results.items() if not df.empty}
    if not valid:
        return

    n = len(valid)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 5), sharey=False)
    if n == 1:
        axes = [axes]

    fig.suptitle("Walk-forward: Sharpe по тестовым окнам", fontsize=14)

    colors = ["#00d4ff", "#00ff88", "#ff6b6b", "#ffd93d", "#c77dff", "#ff9a3c", "#4ecdc4"]

    for idx, (asset, wf) in enumerate(valid.items()):
        color = colors[idx % len(colors)]
        ax = axes[idx]
        x = range(len(wf))
        ax.bar(x, wf["sharpe"], color=color, alpha=0.7)
        ax.axhline(0, color="#888", linewidth=0.8)
        ax.axhline(wf["sharpe"].mean(), color=color, linewidth=1.5,
                   linestyle="--", label=f"avg {wf['sharpe'].mean():.2f}")
        ax.set_title(f"{asset}")
        ax.set_xlabel("Тестовое окно")
        ax.set_ylabel("Sharpe")
        ax.legend(fontsize=8)
        ax.grid(True, axis="y")

        # Подписи return
        for i, row in wf.iterrows():
            pos = list(x)[list(wf.index).index(i)]
            ax.text(pos, row["sharpe"] + 0.05,
                    f"{row['total_return']:.0%}", ha="center", fontsize=7)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    plt.close()
    print(f"  Сохранено: {save_path}")


# ─── 5. Return distribution сделок ──────────────────────────────────────────

def plot_trade_distribution(
    assets: dict[str, pd.DataFrame],
    top_params: dict[str, dict],
    initial_capital: float = 10_000.0,
    save_path: str = f"{RESULTS_DIR}/trade_distribution.png",
):
    valid_assets = [a for a in top_params if a in assets]
    n = len(valid_assets)
    if n == 0:
        return

    fig, axes = plt.subplots(1, n, figsize=(5 * n, 5))
    if n == 1:
        axes = [axes]

    fig.suptitle("Распределение доходности сделок", fontsize=14)
    colors = ["#00d4ff", "#00ff88", "#ff6b6b", "#ffd93d", "#c77dff", "#ff9a3c", "#4ecdc4"]

    for idx, asset in enumerate(valid_assets):
        df = assets[asset]
        params = top_params[asset]
        color = colors[idx % len(colors)]
        ax = axes[idx]

        result = run_backtest(df, asset, initial_capital=initial_capital, **params)
        pnls = [t.pnl_pct * 100 for t in result["trades"]]

        if not pnls:
            continue

        ax.hist(pnls, bins=40, color=color, alpha=0.7, edgecolor="none")
        ax.axvline(0, color="#ff6b6b", linewidth=1.5, linestyle="--")
        ax.axvline(np.mean(pnls), color=color, linewidth=1.5,
                   linestyle="-", label=f"avg {np.mean(pnls):.2f}%")
        ax.set_title(f"{asset}")
        ax.set_xlabel("PnL per trade (%)")
        ax.set_ylabel("Количество сделок")
        ax.legend(fontsize=8)
        ax.grid(True, axis="y")

        wins = sum(1 for p in pnls if p > 0)
        ax.text(0.97, 0.95, f"Win: {wins}/{len(pnls)}\n({wins/len(pnls):.0%})",
                transform=ax.transAxes, fontsize=8,
                verticalalignment="top", horizontalalignment="right",
                color=color)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    plt.close()
    print(f"  Сохранено: {save_path}")


# ─── Всё вместе ──────────────────────────────────────────────────────────────

def generate_all_charts(
    assets: dict[str, pd.DataFrame],
    grid_results: dict[str, pd.DataFrame],
    wf_results: dict[str, pd.DataFrame],
    top_params: dict[str, dict],
    initial_capital: float = 10_000.0,
):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("\n=== Генерация графиков ===")
    plot_equity_curves(assets, top_params, initial_capital)
    plot_param_heatmaps(grid_results)
    plot_asset_comparison(grid_results)
    plot_walkforward(wf_results)
    plot_trade_distribution(assets, top_params, initial_capital)
    print("Все графики готовы.")
