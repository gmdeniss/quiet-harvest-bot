"""
Хранилище состояния бота.

Если задан REDIS_URL — используем Upstash Redis (для Render).
Если нет — файлы на диске (для локальной разработки).

Upstash Redis: https://upstash.com (бесплатно, 10k req/day)
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "")

# Ключи в Redis
KEY_POSITIONS = "qhb:positions"
KEY_CAPITAL   = "qhb:capital"
KEY_ONCHAIN   = "qhb:onchain"

# Fallback файлы (локально)
DATA_DIR = Path("data")
FILE_POSITIONS = DATA_DIR / "positions.json"
FILE_CAPITAL   = DATA_DIR / "capital.json"
FILE_ONCHAIN   = DATA_DIR / "onchain_history.json"


_redis_client = None
_redis_fail_until = 0    # cooldown: не retry до этого timestamp

def _get_redis():
    global _redis_client, _redis_fail_until
    if not REDIS_URL:
        return None
    if _redis_client:
        return _redis_client
    import time
    if time.time() < _redis_fail_until:
        return None
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        _redis_client = r
        _redis_fail_until = 0
        log.info("Redis подключён")
        return r
    except Exception as e:
        log.warning(f"Redis недоступен: {e} — используем файлы (retry через 60с)")
        _redis_fail_until = time.time() + 60
        _redis_client = None
        return None

def _reset_redis():
    """Сбрасываем кэш подключения (вызывается при ошибке операции)."""
    global _redis_client
    _redis_client = None


def _read(key: str, file_path: Path) -> dict | list | None:
    r = _get_redis()
    if r:
        try:
            raw = r.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            log.warning(f"Redis read {key} failed: {e}, переподключаемся...")
            _reset_redis()
            r = _get_redis()
            if r:
                try:
                    raw = r.get(key)
                    return json.loads(raw) if raw else None
                except Exception:
                    log.error(f"Redis retry {key} failed, fallback на файл")
                    _reset_redis()
    if file_path.exists():
        with open(file_path) as f:
            return json.load(f)
    return None


def _write(key: str, file_path: Path, data: dict | list):
    r = _get_redis()
    if r:
        try:
            r.set(key, json.dumps(data))
            return
        except Exception as e:
            log.warning(f"Redis write {key} failed: {e}, переподключаемся...")
            _reset_redis()
            r = _get_redis()
            if r:
                try:
                    r.set(key, json.dumps(data))
                    return
                except Exception:
                    log.error(f"Redis retry {key} failed, fallback на файл")
                    _reset_redis()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)


# ── Public API ────────────────────────────────────────────────

def load_positions_raw() -> dict:
    return _read(KEY_POSITIONS, FILE_POSITIONS) or {}

def save_positions_raw(data: dict):
    _write(KEY_POSITIONS, FILE_POSITIONS, data)

def load_capital_raw() -> dict | None:
    return _read(KEY_CAPITAL, FILE_CAPITAL)

def save_capital_raw(data: dict):
    _write(KEY_CAPITAL, FILE_CAPITAL, data)

def load_onchain_raw() -> dict:
    return _read(KEY_ONCHAIN, FILE_ONCHAIN) or {}

def save_onchain_raw(data: dict):
    _write(KEY_ONCHAIN, FILE_ONCHAIN, data)


KEY_TRADELOG     = "qhb:tradelog"
KEY_TRADED_TODAY = "qhb:traded_today"

FILE_TRADELOG     = DATA_DIR / "trade_log.json"
FILE_TRADED_TODAY = DATA_DIR / "traded_today.json"

def load_tradelog_raw() -> list:
    return _read(KEY_TRADELOG, FILE_TRADELOG) or []

def save_tradelog_raw(data: list):
    _write(KEY_TRADELOG, FILE_TRADELOG, data)

def load_traded_today_raw() -> dict:
    return _read(KEY_TRADED_TODAY, FILE_TRADED_TODAY) or {}

def save_traded_today_raw(data: dict):
    _write(KEY_TRADED_TODAY, FILE_TRADED_TODAY, data)


def using_redis() -> bool:
    return bool(REDIS_URL and _get_redis())
