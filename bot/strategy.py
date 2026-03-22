"""
Генерация торговых сигналов — та же логика что в бэктесте.

Сигнал = onchain_volume > MA(onchain_volume, period) * (1 + threshold)
"""

import logging
import numpy as np
from dataclasses import dataclass
from datetime import date

from bot.config import AssetConfig
from bot.onchain import get_volume_series

log = logging.getLogger(__name__)


@dataclass
class Signal:
    asset: str
    date: date
    volume: float
    ma_volume: float
    ratio: float          # volume / ma_volume
    threshold: float      # порог сигнала
    triggered: bool
    direction: str = "long"


def check_signal(asset: str, cfg: AssetConfig) -> Signal | None:
    """
    Проверяет сигнал для одного актива на сегодня.
    Возвращает Signal или None если данных недостаточно.
    """
    # Нужно period + 1 значений для расчёта MA
    needed = cfg.ma_period + 5
    volumes = get_volume_series(asset, days=needed)

    if volumes is None or len(volumes) < cfg.ma_period + 1:
        log.warning(f"{asset}: недостаточно данных для сигнала "
                    f"(есть {len(volumes) if volumes else 0}, нужно {cfg.ma_period + 1})")
        return None

    today_vol = volumes[-1]
    ma_vols = volumes[-(cfg.ma_period + 1):-1]  # предыдущие N дней для MA
    ma = float(np.mean(ma_vols))

    if ma <= 0:
        log.warning(f"{asset}: MA = 0, пропускаем")
        return None

    ratio = today_vol / ma
    triggered = ratio > (1 + cfg.threshold)

    signal = Signal(
        asset=asset,
        date=date.today(),
        volume=today_vol,
        ma_volume=ma,
        ratio=ratio,
        threshold=cfg.threshold,
        triggered=triggered,
    )

    log.info(
        f"{asset}: volume={today_vol:,.0f}  MA={ma:,.0f}  "
        f"ratio={ratio:.3f}  threshold={1 + cfg.threshold:.2f}  "
        f"→ {'СИГНАЛ ✓' if triggered else 'нет сигнала'}"
    )
    return signal


def check_all_signals(assets: dict[str, AssetConfig]) -> list[Signal]:
    """
    Проверяет сигналы по всем активам.
    Возвращает список активных сигналов, отсортированных по силе (ratio desc).
    """
    signals = []
    for asset, cfg in assets.items():
        sig = check_signal(asset, cfg)
        if sig and sig.triggered:
            signals.append(sig)

    signals.sort(key=lambda s: -s.ratio)

    if signals:
        log.info(f"Активных сигналов: {len(signals)} — "
                 f"{', '.join(f'{s.asset}({s.ratio:.2f}x)' for s in signals)}")
    else:
        log.info("Сигналов нет")

    return signals
