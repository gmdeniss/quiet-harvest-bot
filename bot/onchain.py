"""
Получение и хранение on-chain объёма.

Источник: Blockchair API (бесплатно, без ключа для базовых запросов)
Сохраняем историю локально в data/onchain_history.json
чтобы не терять данные между перезапусками.
"""

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BLOCKCHAIR_CHAINS = {
    "ETH":  "ethereum",
    "BCH":  "bitcoin-cash",
    "DASH": "dash",
    "ZEC":  "zcash",
    "BTC":  "bitcoin",
    "LTC":  "litecoin",
}

UNITS = {
    "ETH":  1e18,
    "BCH":  1e8,
    "DASH": 1e8,
    "ZEC":  1e8,
    "BTC":  1e8,
    "LTC":  1e8,
}

HISTORY_FILE = Path("data/onchain_history.json")


def _load_history() -> dict[str, dict[str, float]]:
    from bot.storage import load_onchain_raw
    return load_onchain_raw()


def _save_history(history: dict):
    from bot.storage import save_onchain_raw
    save_onchain_raw(history)


def fetch_daily_volume(asset: str) -> float | None:
    """
    Получает вчерашний on-chain объём для актива через Blockchair.
    Возвращает объём в монетах (не в сатошах/wei).
    """
    chain = BLOCKCHAIR_CHAINS.get(asset)
    if not chain:
        log.warning(f"Нет конфига Blockchair для {asset}")
        return None

    url = f"https://api.blockchair.com/{chain}/stats"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]

        # Blockchair возвращает volume_24h в сатошах/wei
        # ETH использует volume_24h_approximate
        raw_volume = (data.get("volume_24h")
                      or data.get("volume_24h_approximate")
                      or data.get("transactions_volume_24h"))
        if raw_volume is None:
            log.warning(f"{asset}: поле volume_24h не найдено, ключи: {list(data.keys())}")
            return None

        volume = float(raw_volume) / UNITS[asset]
        log.info(f"{asset}: on-chain volume = {volume:,.2f}")
        return volume

    except Exception as e:
        log.error(f"{asset}: ошибка получения on-chain volume: {e}")
        return None


def update_history(assets: list[str]) -> dict[str, dict[str, float]]:
    """
    Обновляет локальную историю on-chain объёмов.
    Вызывается раз в день после закрытия дневной свечи.
    Возвращает полную историю.
    """
    history = _load_history()
    today_str = date.today().isoformat()

    for asset in assets:
        if asset not in history:
            history[asset] = {}

        # Не запрашиваем повторно если уже есть за сегодня
        if today_str in history[asset]:
            log.info(f"{asset}: volume за {today_str} уже есть ({history[asset][today_str]:,.2f})")
            continue

        volume = fetch_daily_volume(asset)
        if volume is not None:
            history[asset][today_str] = volume
            log.info(f"{asset}: записан volume {volume:,.2f} за {today_str}")

        time.sleep(1)  # rate limit Blockchair

    _save_history(history)
    return history


def get_volume_series(asset: str, days: int = 20) -> list[float] | None:
    """
    Возвращает последние N дней on-chain volume для актива.
    Если данных не хватает — возвращает None.
    """
    history = _load_history()
    if asset not in history:
        return None

    asset_data = history[asset]
    # Сортируем по дате
    sorted_dates = sorted(asset_data.keys())
    recent = sorted_dates[-days:]

    if len(recent) < days:
        log.warning(f"{asset}: только {len(recent)} дней истории (нужно {days})")
        if len(recent) < 3:
            return None

    return [asset_data[d] for d in recent]


def bootstrap_history_from_tsv(
    asset: str,
    tsv_path: str,
    unit: float = 1e8,
):
    """
    Загружает историю из TSV файла (для первого запуска бота).
    Так не надо ждать пока накопится история через Blockchair.
    """
    import pandas as pd

    history = _load_history()
    if asset not in history:
        history[asset] = {}

    df = pd.read_csv(tsv_path, sep="\t", header=0, names=["date", "volume"])
    df["date"] = pd.to_datetime(df["date"], format="%d.%m.%Y")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce") / unit
    df = df.dropna()

    count = 0
    for _, row in df.iterrows():
        date_str = row["date"].date().isoformat()
        if date_str not in history[asset]:
            history[asset][date_str] = float(row["volume"])
            count += 1

    _save_history(history)
    log.info(f"{asset}: загружено {count} новых записей из {tsv_path}")


def bootstrap_all_from_tsv(config_path: str = "config.yaml"):
    """
    При первом запуске — загружает всю историю из TSV в локальный кэш.
    """
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    for asset, params in cfg["assets"].items():
        bootstrap_history_from_tsv(
            asset=asset,
            tsv_path=params["tsv"],
            unit=float(params["unit"]),
        )
    log.info("Bootstrap завершён — история загружена из TSV")
