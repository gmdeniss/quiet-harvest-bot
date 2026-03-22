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


def _get_redis():
    if not REDIS_URL:
        return None
    try:
        import redis
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        return r
    except Exception as e:
        log.warning(f"Redis недоступен: {e} — используем файлы")
        return None


def _read(key: str, file_path: Path) -> dict | list | None:
    r = _get_redis()
    if r:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    if file_path.exists():
        with open(file_path) as f:
            return json.load(f)
    return None


def _write(key: str, file_path: Path, data: dict | list):
    r = _get_redis()
    if r:
        r.set(key, json.dumps(data))
    else:
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

def using_redis() -> bool:
    return bool(REDIS_URL and _get_redis())
