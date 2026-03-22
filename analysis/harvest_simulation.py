"""
Симуляция стратегии "снятия сливок":
  - Стартуем с $1000
  - Как только капитал достигает $2000 (x2) → фиксируем $1000 прибыли, рестарт с $1000
  - Считаем: сколько раз сняли, в какие годы, итоговая прибыль

Портфель: ETH+BCH+DASH+ZEC, max_simultaneous=2
"""

import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.loader import load_all_assets
from backtest.portfolio import generate_signal_queue, _OpenPosition, PortfolioTrade

plt.rcParams.update({
    "figure.facecolor": "#0f1117", "axes.facecolor": "#1a1d27",
    "axes.edgecolor": "#3a3d4a", "axes.labelcolor": "#c8ccd8",
    "xtick.color": "#8a8d9a", "ytick.color": "#8a8d9a",
    "text.color": "#c8ccd8", "grid.color": "#2a2d3a",
    "grid.linestyle": "--", "grid.alpha": 0.5, "font.size": 10,
})

RESULTS_DIR = "results"


def load_best_params() -> dict:
    params = {}
    for asset in ["ETH", "BCH", "DASH", "ZEC"]:
        path = f"{RESULTS_DIR}/grid_{asset.lower()}.csv"
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
    return params


def run_harvest_simulation(
    assets_data: dict,
    asset_params: dict,
    portfolio_assets: list,
    max_simultaneous: int = 2,
    position_size_pct: float = 0.15,
    initial_capital: float = 1_000.0,
    target_multiplier: float = 2.0,
):
    target = initial_capital * target_multiplier  # 2000

    # Генерируем все потенциальные сделки
    all_signals: list[PortfolioTrade] = []
    for asset in portfolio_assets:
        sigs = generate_signal_queue(assets_data[asset], asset, asset_params[asset])
        all_signals.extend(sigs)

    # Группируем по дате входа, сортируем по силе сигнала
    signals_by_date: dict = defaultdict(list)
    for ps in all_signals:
        signals_by_date[ps.trade.entry_date].append(ps)
    for date in signals_by_date:
        signals_by_date[date].sort(key=lambda s: -s.signal_ratio)

    all_dates = sorted(set(
        list(signals_by_date.keys()) +
        [ps.trade.exit_date for ps in all_signals]
    ))

    # ── Симуляция ──
    capital = initial_capital
    open_positions: list[_OpenPosition] = []
    blocked_assets: set = set()

    harvests = []          # список {date, capital_before, cycle_n}
    cycle = 1
    equity_log = []        # (date, capital, cycle)
    total_harvested = 0.0
    cycle_start_date = all_dates[0]
    cycle_trades = 0
    all_trades_log = []    # все закрытые сделки с pnl

    for current_date in all_dates:
        # 1. Закрываем позиции
        still_open = []
        for op in open_positions:
            if op.ps.trade.exit_date <= current_date:
                pnl = op.ps.trade.pnl_pct * op.deployed_capital
                capital += pnl
                blocked_assets.discard(op.ps.asset)
                cycle_trades += 1
                all_trades_log.append({
                    "date": op.ps.trade.exit_date,
                    "asset": op.ps.asset,
                    "pnl_pct": op.ps.trade.pnl_pct,
                    "pnl_abs": pnl,
                    "capital_after": capital,
                    "cycle": cycle,
                })
                equity_log.append((op.ps.trade.exit_date, capital, cycle))

                # ── Проверяем достижение цели ──
                if capital >= target:
                    profit_taken = capital - initial_capital
                    total_harvested += profit_taken
                    days_in_cycle = (current_date - cycle_start_date).days
                    harvests.append({
                        "cycle": cycle,
                        "harvest_date": current_date,
                        "year": current_date.year,
                        "month": current_date.month,
                        "capital_at_harvest": round(capital, 2),
                        "profit_taken": round(profit_taken, 2),
                        "days_to_double": days_in_cycle,
                        "trades_in_cycle": cycle_trades,
                    })
                    print(f"  Цикл {cycle:3d}: {current_date.date()}  "
                          f"капитал=${capital:.0f}  "
                          f"снято=${profit_taken:.0f}  "
                          f"дней={days_in_cycle}  сделок={cycle_trades}")
                    # Рестарт
                    capital = initial_capital
                    cycle += 1
                    cycle_start_date = current_date
                    cycle_trades = 0
                    equity_log.append((current_date, capital, cycle))
            else:
                still_open.append(op)
        open_positions = still_open

        # 2. Открываем новые позиции
        if current_date in signals_by_date:
            for ps in signals_by_date[current_date]:
                if len(open_positions) >= max_simultaneous:
                    break
                if ps.asset in blocked_assets:
                    continue
                deployed = capital * position_size_pct
                open_positions.append(_OpenPosition(ps=ps, deployed_capital=deployed))
                blocked_assets.add(ps.asset)

    # Закрываем остатки
    for op in open_positions:
        pnl = (op.ps.trade.pnl_pct or 0.0) * op.deployed_capital
        capital += pnl
        equity_log.append((op.ps.trade.exit_date, capital, cycle))

    equity_df = (
        pd.DataFrame(equity_log, columns=["date", "capital", "cycle"])
        .drop_duplicates("date")
        .set_index("date")
        .sort_index()
    )
    trades_df = pd.DataFrame(all_trades_log)
    harvests_df = pd.DataFrame(harvests)

    return {
        "harvests": harvests_df,
        "equity": equity_df,
        "trades": trades_df,
        "final_capital": capital,
        "total_harvested": total_harvested,
        "n_cycles_completed": len(harvests),
        "last_cycle": cycle,
    }


def plot_harvest(result: dict, save_path: str = f"{RESULTS_DIR}/harvest_simulation.png"):
    harvests = result["harvests"]
    equity = result["equity"]

    fig, axes = plt.subplots(3, 2, figsize=(18, 16))
    fig.suptitle("Стратегия 'снятия сливок': $1000 → $2000 → сброс", fontsize=14, y=0.98)

    colors = plt.cm.plasma(np.linspace(0.2, 0.9, max(result["last_cycle"], 1)))

    # ── 1. Equity curve со сбросами ──
    ax = axes[0, :]
    ax = fig.add_subplot(3, 1, 1)
    for cycle_n, grp in equity.groupby("cycle"):
        c = colors[min(cycle_n - 1, len(colors) - 1)]
        ax.plot(grp.index, grp["capital"], color=c, linewidth=0.8, alpha=0.8)

    # Метки сбросов
    for _, h in harvests.iterrows():
        ax.axvline(h["harvest_date"], color="#ffd93d", alpha=0.3, linewidth=0.6)
        ax.annotate(f"#{int(h['cycle'])}",
                    xy=(h["harvest_date"], result["final_capital"] if False else 2000),
                    fontsize=5, color="#ffd93d", alpha=0.7,
                    rotation=90, va="top")

    ax.axhline(2000, color="#00ff88", linewidth=1, linestyle="--", label="Цель $2000")
    ax.axhline(1000, color="#ff6b6b", linewidth=0.8, linestyle=":", label="Старт $1000")
    ax.set_title("Equity по циклам (каждый цвет = новый цикл)")
    ax.set_ylabel("Капитал ($)")
    ax.legend(fontsize=9)
    ax.grid(True)

    # ── 2. Сколько снятий по годам ──
    ax2 = axes[1, 0]
    if not harvests.empty:
        by_year = harvests.groupby("year").size()
        bars = ax2.bar(by_year.index, by_year.values, color="#00d4ff", alpha=0.8)
        ax2.set_title("Количество снятий по годам")
        ax2.set_xlabel("Год")
        ax2.set_ylabel("Раз сняли $1000")
        for bar, val in zip(bars, by_year.values):
            ax2.text(bar.get_x() + bar.get_width() / 2, val + 0.1,
                     str(val), ha="center", fontsize=10, color="#ffd93d")
        ax2.grid(True, axis="y")

    # ── 3. Дней до удвоения по циклам ──
    ax3 = axes[1, 1]
    if not harvests.empty:
        ax3.bar(harvests["cycle"], harvests["days_to_double"],
                color="#c77dff", alpha=0.8)
        avg_days = harvests["days_to_double"].mean()
        ax3.axhline(avg_days, color="white", linewidth=1.5, linestyle="--",
                    label=f"Среднее: {avg_days:.0f} дней")
        ax3.set_title("Дней до удвоения по каждому циклу")
        ax3.set_xlabel("Цикл #")
        ax3.set_ylabel("Дней")
        ax3.legend(fontsize=9)
        ax3.grid(True, axis="y")

    # ── 4. Накопленная прибыль ──
    ax4 = axes[2, 0]
    if not harvests.empty:
        cumsum = harvests["profit_taken"].cumsum()
        ax4.fill_between(harvests["cycle"], cumsum, alpha=0.4, color="#00ff88")
        ax4.plot(harvests["cycle"], cumsum, color="#00ff88", linewidth=2)
        ax4.set_title(f"Накопленная прибыль (итого ${cumsum.iloc[-1]:,.0f})")
        ax4.set_xlabel("Цикл #")
        ax4.set_ylabel("Прибыль ($)")
        ax4.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax4.grid(True)

    # ── 5. Сделок в цикле ──
    ax5 = axes[2, 1]
    if not harvests.empty:
        ax5.bar(harvests["cycle"], harvests["trades_in_cycle"],
                color="#ff9a3c", alpha=0.8)
        avg_t = harvests["trades_in_cycle"].mean()
        ax5.axhline(avg_t, color="white", linewidth=1.5, linestyle="--",
                    label=f"Среднее: {avg_t:.0f} сделок")
        ax5.set_title("Сделок в каждом цикле")
        ax5.set_xlabel("Цикл #")
        ax5.set_ylabel("Сделок")
        ax5.legend(fontsize=9)
        ax5.grid(True, axis="y")

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="#0f1117")
    plt.close()
    print(f"\n  График: {save_path}")


def main():
    print("=== Загрузка данных ===")
    assets = load_all_assets()

    print("=== Загрузка параметров ===")
    params = load_best_params()
    for a, p in params.items():
        print(f"  {a}: MA={p['ma_period']}d thr={p['threshold']:.0%} ts={p['trailing_stop_pct']:.1%} hold={p['max_hold']}d")

    portfolio = ["ETH", "BCH", "DASH", "ZEC"]
    print(f"\n=== Симуляция: {'+'.join(portfolio)}, max_sim=2, $1000→$2000 ===\n")

    result = run_harvest_simulation(
        assets_data=assets,
        asset_params=params,
        portfolio_assets=portfolio,
        max_simultaneous=2,
        position_size_pct=0.15,
        initial_capital=1_000.0,
        target_multiplier=2.0,
    )

    plot_harvest(result)

    h = result["harvests"]

    print("\n" + "=" * 65)
    print("ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 65)
    print(f"\n  Период:              {h['harvest_date'].min().date() if not h.empty else 'N/A'}"
          f" → {h['harvest_date'].max().date() if not h.empty else 'N/A'}")
    print(f"  Успешных удвоений:   {result['n_cycles_completed']}")
    print(f"  Суммарно снято:      ${result['total_harvested']:,.0f}")
    print(f"  Остаток в работе:    ${result['final_capital']:,.2f}")
    print(f"  Итого на руках:      ${result['total_harvested'] + result['final_capital']:,.0f}")

    if not h.empty:
        print(f"\n  Среднее время удвоения: {h['days_to_double'].mean():.0f} дней "
              f"({h['days_to_double'].mean()/30:.1f} мес)")
        print(f"  Минимум:             {h['days_to_double'].min()} дней")
        print(f"  Максимум:            {h['days_to_double'].max()} дней")

        print(f"\n  По годам:")
        by_year = h.groupby("year").agg(
            снятий=("cycle", "count"),
            суммарно=("profit_taken", "sum"),
            avg_days=("days_to_double", "mean"),
        )
        for year, row in by_year.iterrows():
            bar = "★" * int(row["снятий"])
            print(f"    {year}: {int(row['снятий']):2d}× снятий  "
                  f"${row['суммарно']:,.0f} прибыли  "
                  f"avg {row['avg_days']:.0f} дн/цикл  {bar}")

        print(f"\n  Топ-5 самых быстрых удвоений:")
        top5 = h.nsmallest(5, "days_to_double")[
            ["cycle", "harvest_date", "days_to_double", "trades_in_cycle"]
        ]
        for _, r in top5.iterrows():
            print(f"    Цикл #{int(r['cycle']):3d}  {r['harvest_date'].date()}  "
                  f"{int(r['days_to_double'])} дней  {int(r['trades_in_cycle'])} сделок")

        h.to_csv(f"{RESULTS_DIR}/harvest_log.csv", index=False)
        print(f"\n  Лог: {RESULTS_DIR}/harvest_log.csv")


if __name__ == "__main__":
    main()
