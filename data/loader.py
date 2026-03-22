"""
Загрузка on-chain объёмов из TSV и OHLCV из yfinance.
Выравнивает данные по дате и возвращает готовый DataFrame.
"""

import os
import pandas as pd
import numpy as np
import yfinance as yf
import yaml
import logging

log = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_onchain_volume(tsv_path: str, unit: float = 1.0) -> pd.Series:
    """
    Читает TSV с on-chain объёмом.
    Возвращает Series с DatetimeIndex (UTC, дневной) и объёмом в монетах.
    """
    df = pd.read_csv(tsv_path, sep="\t", header=0, names=["date", "volume"])
    df["date"] = pd.to_datetime(df["date"], format="%d.%m.%Y", utc=False)
    df = df.set_index("date").sort_index()
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    df = df.dropna()
    volume = df["volume"] / unit
    volume.name = "onchain_volume"
    return volume


def fetch_ohlcv(ticker: str, start: str, end: str = None) -> pd.DataFrame:
    """
    Тянет дневные OHLCV из Yahoo Finance.
    Возвращает DataFrame с колонками: open, high, low, close, volume.
    """
    log.info(f"Fetching OHLCV for {ticker} from {start}")
    raw = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if raw.empty:
        raise ValueError(f"No OHLCV data for {ticker}")
    raw.columns = [c.lower() for c in raw.columns.get_level_values(0)]
    raw.index = pd.to_datetime(raw.index)
    raw.index.name = "date"
    return raw[["open", "high", "low", "close", "volume"]]


def build_asset_data(
    asset_name: str,
    tsv_path: str,
    ticker: str,
    unit: float = 1e8,
    cache_dir: str = "data/cache",
) -> pd.DataFrame:
    """
    Собирает единый DataFrame для актива:
      date | open | high | low | close | volume_exchange | onchain_volume

    Кэширует OHLCV в CSV чтобы не дёргать Yahoo каждый раз.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"{asset_name.lower()}_ohlcv.csv")

    # On-chain объём
    onchain = load_onchain_volume(tsv_path, unit)
    start_date = onchain.index.min().strftime("%Y-%m-%d")

    # OHLCV — из кэша или загружаем
    if os.path.exists(cache_file):
        ohlcv = pd.read_csv(cache_file, index_col="date", parse_dates=True)
        log.info(f"{asset_name}: loaded OHLCV from cache ({len(ohlcv)} rows)")
    else:
        ohlcv = fetch_ohlcv(ticker, start=start_date)
        ohlcv.to_csv(cache_file)
        log.info(f"{asset_name}: fetched {len(ohlcv)} rows, saved to {cache_file}")

    # Выравнивание по дате
    df = ohlcv.join(onchain, how="inner")
    df = df.rename(columns={"volume": "volume_exchange"})
    df = df.dropna(subset=["close", "onchain_volume"])
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    log.info(
        f"{asset_name}: {len(df)} days aligned | "
        f"{df.index[0].date()} → {df.index[-1].date()}"
    )
    return df


def load_all_assets(config_path: str = "config.yaml") -> dict[str, pd.DataFrame]:
    """
    Загружает все активы из конфига.
    Возвращает dict: {asset_name: DataFrame}
    """
    cfg = load_config(config_path)
    assets = {}
    for name, params in cfg["assets"].items():
        try:
            df = build_asset_data(
                asset_name=name,
                tsv_path=params["tsv"],
                ticker=params["ticker"],
                unit=float(params["unit"]),
            )
            assets[name] = df
        except Exception as e:
            log.warning(f"Skipping {name}: {e}")
    return assets


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    assets = load_all_assets()
    print("\n=== Загруженные активы ===")
    for name, df in assets.items():
        print(
            f"  {name:6s}: {len(df):5d} дней | "
            f"{df.index[0].date()} → {df.index[-1].date()} | "
            f"close последний: {df['close'].iloc[-1]:.4f}"
        )
