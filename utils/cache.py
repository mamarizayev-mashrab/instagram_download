"""Tiny JSON-backed cache that maps a content key -> Telegram file_ids.

Once a piece of media is uploaded, Telegram lets us resend it by file_id with no
re-download. We only cache immutable content (posts/reels/igtv by shortcode); stories,
highlights and profile pictures change or expire, so they are never cached.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

_CACHE_FILE = Path("cache.json")
_lock = threading.Lock()
_data: dict[str, list[dict]] = {}
_loaded = False


def _load() -> None:
    global _data, _loaded
    if _loaded:
        return
    try:
        _data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(_data, dict):
            _data = {}
    except Exception:
        _data = {}
    _loaded = True


def _save() -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(_data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # cache is best-effort; never crash on write failure


def get(key: str) -> list[dict] | None:
    """Return cached [{'kind': 'video'|'photo', 'file_id': ...}, ...] or None."""
    with _lock:
        _load()
        entry = _data.get(key)
        return list(entry) if entry else None


def put(key: str, items: list[dict]) -> None:
    if not key or not items:
        return
    with _lock:
        _load()
        _data[key] = items
        _save()
