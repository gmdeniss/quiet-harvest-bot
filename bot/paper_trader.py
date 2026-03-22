"""
Paper Trading — симуляция ордеров без реальных денег.
Использует реальные цены с Kraken, но не отправляет реальные ордера.
"""

import logging
from bot.kraken_client import KrakenClient
from bot.config import AssetConfig

log = logging.getLogger(__name__)


class PaperTrader:
    """
    Полностью эмулирует торговлю:
    - Вход: по текущей цене ask (немного хуже mid)
    - Выход: по текущей цене bid (немного хуже mid)
    - Никаких реальных ордеров
    """

    def __init__(self, kraken: KrakenClient):
        self.kraken = kraken

    def get_entry_price(self, pair: str) -> float:
        """Цена входа = ask (реальная цена покупки)."""
        ticker = self.kraken.get_ticker(pair)
        return ticker["ask"]

    def get_exit_price(self, pair: str) -> float:
        """Цена выхода = bid (реальная цена продажи)."""
        ticker = self.kraken.get_ticker(pair)
        return ticker["bid"]

    def get_current_price(self, pair: str) -> float:
        """Текущая цена = last."""
        return self.kraken.get_price(pair)

    def buy(self, pair: str, capital_usd: float, asset: str) -> dict:
        """
        Симулирует покупку.
        Возвращает dict с деталями 'исполненного' ордера.
        """
        price = self.get_entry_price(pair)
        volume = capital_usd / price

        log.info(f"[PAPER] BUY {asset}: {volume:.6f} @ ${price:.4f} = ${capital_usd:.2f}")
        return {
            "asset": asset,
            "pair": pair,
            "type": "buy",
            "price": price,
            "volume": volume,
            "cost": capital_usd,
            "order_id": f"PAPER-{asset}-{int(__import__('time').time())}",
        }

    def sell(self, pair: str, volume: float, asset: str) -> dict:
        """
        Симулирует продажу.
        """
        price = self.get_exit_price(pair)
        proceeds = price * volume

        log.info(f"[PAPER] SELL {asset}: {volume:.6f} @ ${price:.4f} = ${proceeds:.2f}")
        return {
            "asset": asset,
            "pair": pair,
            "type": "sell",
            "price": price,
            "volume": volume,
            "proceeds": proceeds,
            "order_id": f"PAPER-SELL-{asset}-{int(__import__('time').time())}",
        }
