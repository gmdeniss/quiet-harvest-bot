"""
Kraken REST API клиент.
Документация: https://docs.kraken.com/rest/
"""

import hashlib
import hmac
import time
import base64
import logging
import urllib.parse
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.kraken.com"


class KrakenClient:
    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "TradeBot/1.0"})

    # ── Public ────────────────────────────────────────────────────

    def get_ticker(self, pair: str) -> dict:
        """Текущая цена. pair = 'XETHZUSD'
        Kraken может вернуть ключ отличный от переданного (напр. ZECUSD → XZECZUSD).
        """
        data = self._public("Ticker", {"pair": pair})
        # Берём первый (и единственный) ключ из ответа
        info = data[list(data.keys())[0]]
        return {
            "bid": float(info["b"][0]),
            "ask": float(info["a"][0]),
            "last": float(info["c"][0]),
            "high_24h": float(info["h"][1]),
            "low_24h": float(info["l"][1]),
            "volume_24h": float(info["v"][1]),
        }

    def get_price(self, pair: str) -> float:
        """Быстро — только последняя цена."""
        return self.get_ticker(pair)["last"]

    def get_ohlcv(self, pair: str, interval: int = 1440, since: int = None) -> list[dict]:
        """
        OHLCV свечи.
        interval: минуты (1440 = дневные)
        Возвращает список dict: time, open, high, low, close, volume
        """
        params = {"pair": pair, "interval": interval}
        if since:
            params["since"] = since
        data = self._public("OHLC", params)
        key = list(data.keys())[0]
        rows = []
        for r in data[key]:
            rows.append({
                "time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[6]),
            })
        return rows

    def get_asset_pairs(self) -> dict:
        """Список всех торговых пар."""
        return self._public("AssetPairs")

    # ── Private ───────────────────────────────────────────────────

    def get_balance(self) -> dict[str, float]:
        """Баланс всех активов: {'ZUSD': 1000.0, 'XETH': 0.5, ...}"""
        raw = self._private("Balance")
        return {k: float(v) for k, v in raw.items()}

    def get_usd_balance(self) -> float:
        """USD баланс."""
        bal = self.get_balance()
        return bal.get("ZUSD", 0.0)

    def place_market_buy(self, pair: str, volume: float) -> dict:
        """Рыночный ордер на покупку."""
        return self._private("AddOrder", {
            "pair": pair,
            "type": "buy",
            "ordertype": "market",
            "volume": str(round(volume, 8)),
        })

    def place_market_sell(self, pair: str, volume: float) -> dict:
        """Рыночный ордер на продажу."""
        return self._private("AddOrder", {
            "pair": pair,
            "type": "sell",
            "ordertype": "market",
            "volume": str(round(volume, 8)),
        })

    def place_limit_buy(self, pair: str, volume: float, price: float) -> dict:
        return self._private("AddOrder", {
            "pair": pair,
            "type": "buy",
            "ordertype": "limit",
            "price": str(round(price, 4)),
            "volume": str(round(volume, 8)),
        })

    def place_limit_sell(self, pair: str, volume: float, price: float) -> dict:
        return self._private("AddOrder", {
            "pair": pair,
            "type": "sell",
            "ordertype": "limit",
            "price": str(round(price, 4)),
            "volume": str(round(volume, 8)),
        })

    def get_open_orders(self) -> dict:
        return self._private("OpenOrders")

    def cancel_order(self, txid: str) -> dict:
        return self._private("CancelOrder", {"txid": txid})

    def get_trade_history(self) -> dict:
        return self._private("TradesHistory")

    # ── HTTP helpers ──────────────────────────────────────────────

    def _public(self, method: str, params: dict = None) -> dict:
        url = f"{BASE_URL}/0/public/{method}"
        last_exc = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params or {}, timeout=10)
                resp.raise_for_status()
                result = resp.json()
                if result["error"]:
                    raise RuntimeError(f"Kraken API error: {result['error']}")
                return result["result"]
            except RuntimeError:
                raise
            except Exception as e:
                last_exc = e
                if attempt < 2:
                    log.warning(f"Kraken {method} attempt {attempt+1} failed: {e}, retrying...")
                    time.sleep(2)
        raise last_exc

    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _private(self, method: str, params: dict = None) -> dict:
        if not self.has_credentials():
            raise RuntimeError("API key/secret не настроены")
        params = params or {}
        url_path = f"/0/private/{method}"
        nonce = str(int(time.time() * 1000))
        params["nonce"] = nonce

        post_data = urllib.parse.urlencode(params)
        encoded = (nonce + post_data).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(self.api_secret)
        sig = hmac.new(secret, message, hashlib.sha512)
        signature = base64.b64encode(sig.digest()).decode()

        headers = {
            "API-Key": self.api_key,
            "API-Sign": signature,
        }
        resp = self.session.post(
            BASE_URL + url_path, data=params, headers=headers, timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
        if result["error"]:
            raise RuntimeError(f"Kraken API error: {result['error']}")
        return result["result"]
