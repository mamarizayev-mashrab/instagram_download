"""Unique-user tracking for the /stats command.

Every user who interacts with the bot is recorded once. Backed by a Redis set
(`igbot:users`) when REDIS_URL is configured so the count survives restarts;
otherwise it falls back to a local `users.json` file (ephemeral on Render's
free tier, same trade-off as the language store in i18n.py).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from utils.redis_store import client as _redis

_STORE_FILE = Path("users.json")
_REDIS_SET = "igbot:users"
_lock = threading.Lock()
_ids: set[str] = set()
_loaded = False


def _load() -> None:
    global _ids, _loaded
    if _loaded:
        return
    r = _redis()
    if r is not None:
        try:
            _ids = set(r.smembers(_REDIS_SET) or [])
            _loaded = True
            return
        except Exception:
            pass  # fall back to the file store
    try:
        data = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
        _ids = set(map(str, data)) if isinstance(data, list) else set()
    except Exception:
        _ids = set()
    _loaded = True


def _save() -> None:
    try:
        _STORE_FILE.write_text(json.dumps(sorted(_ids)), encoding="utf-8")
    except Exception:
        pass


def track(user_id: int) -> None:
    """Record a user id (idempotent). Called on every interaction."""
    uid = str(user_id)
    with _lock:
        _load()
        if uid in _ids:
            return
        _ids.add(uid)
        r = _redis()
        if r is not None:
            try:
                r.sadd(_REDIS_SET, uid)
                return
            except Exception:
                pass  # fall back to the file store
        _save()


def count() -> int:
    """Total number of unique users seen so far."""
    with _lock:
        _load()
        return len(_ids)
