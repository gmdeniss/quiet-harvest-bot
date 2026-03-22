"""
Конфигурация бота — загружается из .env файла.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env из корня проекта
load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass
class AssetConfig:
    symbol: str          # ETH
    kraken_pair: str     # XETHZUSD
    blockchair_chain: str  # ethereum
    unit: float          # 1e18 для ETH, 1e8 для остальных
    ma_period: int
    threshold: float     # 0.05 = 5%
    trailing_stop: float # 0.01 = 1%
    max_hold: int        # дней


# Оптимальные параметры из бэктеста
ASSET_CONFIGS = {
    "ETH": AssetConfig("ETH", "XETHZUSD", "ethereum",  1e18, 7,  0.05, 0.01, 3),
    "BCH": AssetConfig("BCH", "BCHUSD",   "bitcoin-cash", 1e8, 5, 0.05, 0.01, 14),
    "DASH": AssetConfig("DASH", "DASHUSD", "dash",     1e8,  5, 0.05, 0.01, 60),
    "ZEC": AssetConfig("ZEC",  "ZECUSD",  "zcash",     1e8,  3, 0.05, 0.01, 3),
}


@dataclass
class BotConfig:
    paper_mode: bool
    kraken_api_key: str
    kraken_api_secret: str
    telegram_token: str
    telegram_chat_id: str
    initial_capital: float
    target_capital: float
    position_size: float
    max_simultaneous: int
    portfolio: list[str]
    price_check_interval: int
    signal_check_hour: int
    signal_check_minute: int
    assets: dict[str, AssetConfig] = field(default_factory=dict)

    def __post_init__(self):
        self.assets = {k: v for k, v in ASSET_CONFIGS.items() if k in self.portfolio}


def load_config() -> BotConfig:
    portfolio = os.getenv("PORTFOLIO", "ETH,BCH,DASH,ZEC").split(",")
    portfolio = [p.strip().upper() for p in portfolio]

    cfg = BotConfig(
        paper_mode=os.getenv("PAPER_MODE", "true").lower() == "true",
        kraken_api_key=os.getenv("KRAKEN_API_KEY", ""),
        kraken_api_secret=os.getenv("KRAKEN_API_SECRET", ""),
        telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        initial_capital=float(os.getenv("INITIAL_CAPITAL", "1000")),
        target_capital=float(os.getenv("TARGET_CAPITAL", "2000")),
        position_size=float(os.getenv("POSITION_SIZE", "0.15")),
        max_simultaneous=int(os.getenv("MAX_SIMULTANEOUS", "2")),
        portfolio=portfolio,
        price_check_interval=int(os.getenv("PRICE_CHECK_INTERVAL", "60")),
        signal_check_hour=int(os.getenv("SIGNAL_CHECK_HOUR", "0")),
        signal_check_minute=int(os.getenv("SIGNAL_CHECK_MINUTE", "5")),
    )
    return cfg
