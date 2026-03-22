"""
Корреляционный анализ сигналов между активами.
Помогает понять: дают ли активы независимые сигналы или все коррелируют.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

from backtest.engine import compute_signal


def build_signal_matrix(
    assets: dict[str, pd.DataFrame],
    ma_period: int = 7,
    threshold: float = 0.15,
) -> pd.DataFrame:
    """
    Строит матрицу сигналов: строки = даты, колонки = активы.
    Значение = volume_ratio (насколько объём превышает MA).
    """
    ratios = {}
    for name, df in assets.items():
        _, ratio = compute_signal(df, ma_period, threshold)
        ratios[name] = ratio

    matrix = pd.DataFrame(ratios)
    matrix = matrix.dropna(how="all")
    return matrix


def signal_correlation_report(
    assets: dict[str, pd.DataFrame],
    ma_period: int = 7,
    threshold: float = 0.15,
    save_path: str = "results/signal_correlation.png",
) -> pd.DataFrame:
    """
    Считает корреляцию volume_ratio между активами.
    Строит heatmap и возвращает матрицу корреляций.
    """
    matrix = build_signal_matrix(assets, ma_period, threshold)

    # Только общий период где есть данные хотя бы у двух активов
    corr = matrix.corr(method="pearson")

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Heatmap корреляции
    sns.heatmap(
        corr,
        annot=True,
        fmt=".2f",
        cmap="RdYlGn",
        center=0,
        vmin=-1, vmax=1,
        ax=axes[0],
        square=True,
    )
    axes[0].set_title(
        f"Корреляция volume ratio сигналов\n(MA={ma_period}d, threshold={threshold:.0%})"
    )

    # Частота сигналов по активам
    signal_freq = (matrix > (1 + threshold)).mean()
    signal_freq.sort_values(ascending=True).plot(
        kind="barh", ax=axes[1], color="steelblue"
    )
    axes[1].set_title("Частота сигналов (% дней)")
    axes[1].set_xlabel("Доля дней с сигналом")
    for i, v in enumerate(signal_freq.sort_values(ascending=True)):
        axes[1].text(v + 0.001, i, f"{v:.1%}", va="center")

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Сохранено: {save_path}")

    return corr


def asset_ranking(
    grid_results: dict[str, pd.DataFrame],
    top_n: int = 3,
) -> pd.DataFrame:
    """
    Ранжирует активы по лучшему Sharpe из grid search.
    Помогает выбрать в какие активы торговать.
    """
    rows = []
    for asset, df in grid_results.items():
        if df.empty:
            continue
        best = df.iloc[0]
        rows.append({
            "asset": asset,
            "best_sharpe": best["sharpe"],
            "best_return": best["total_return"],
            "best_winrate": best["win_rate"],
            "n_trades": best["n_trades"],
            "max_drawdown": best["max_drawdown"],
        })

    if not rows:
        print("Нет результатов для ранжирования")
        return pd.DataFrame()
    ranking = pd.DataFrame(rows).sort_values("best_sharpe", ascending=False)
    ranking = ranking.reset_index(drop=True)
    ranking.index += 1  # нумерация с 1

    print("\n=== Ранжирование активов по Sharpe ===")
    print(ranking.to_string())
    print(f"\nРекомендуется торговать: {', '.join(ranking.head(top_n)['asset'].tolist())}")

    return ranking


def portfolio_correlation_check(
    assets: dict[str, pd.DataFrame],
    selected: list[str],
    ma_period: int = 7,
    threshold: float = 0.15,
    corr_limit: float = 0.7,
) -> list[str]:
    """
    Из списка выбранных активов убирает сильно коррелирующие (>corr_limit).
    Возвращает список активов с низкой взаимной корреляцией.
    """
    matrix = build_signal_matrix(
        {k: v for k, v in assets.items() if k in selected},
        ma_period, threshold
    )
    corr = matrix.corr()

    final = []
    for asset in selected:
        if asset not in corr.columns:
            continue
        too_correlated = False
        for already in final:
            if abs(corr.loc[asset, already]) > corr_limit:
                print(f"  {asset} исключён (корр. с {already}: {corr.loc[asset, already]:.2f})")
                too_correlated = True
                break
        if not too_correlated:
            final.append(asset)

    print(f"После фильтра корреляции: {final}")
    return final
