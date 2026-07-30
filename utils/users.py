"""User tracking for the /stats command.

Two sets are kept:
  • `igbot:users`   — every user id ever seen (all-time).
  • `igbot:blocked` — users who have blocked the bot / deactivated (detected
                      during a broadcast). Active users = all users − blocked.

Backed by Redis when REDIS_URL is configured so the counts survive restarts;
otherwise a local `users.json` file (ephemeral on Render's free tier).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from utils.redis_store import client as _redis

_STORE_FILE = Path("users.json")
_REDIS_SET = "igbot:users"
_REDIS_BLOCKED = "igbot:blocked"
_lock = threading.Lock()
_ids: set[str] = set()
_blocked: set[str] = set()
_loaded = False


def _load() -> None:
    global _ids, _blocked, _loaded
    if _loaded:
        return
    r = _redis()
    if r is not None:
        try:
            _ids = set(r.smembers(_REDIS_SET) or [])
            _blocked = set(r.smembers(_REDIS_BLOCKED) or [])
            _loaded = True
            return
        except Exception:
            pass  # fall back to the file store
    try:
        data = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _ids = set(map(str, data.get("users", [])))
            _blocked = set(map(str, data.get("blocked", [])))
        elif isinstance(data, list):  # legacy format: a bare list of user ids
            _ids = set(map(str, data))
            _blocked = set()
    except Exception:
        _ids, _blocked = set(), set()
    _loaded = True


def _save() -> None:
    try:
        _STORE_FILE.write_text(
            json.dumps({"users": sorted(_ids), "blocked": sorted(_blocked)}),
            encoding="utf-8",
        )
    except Exception:
        pass


def track(user_id: int) -> None:
    """Record a user id on every interaction. Interacting also clears a stale
    'blocked' mark (the user has clearly unblocked the bot)."""
    uid = str(user_id)
    with _lock:
        _load()
        if uid in _ids and uid not in _blocked:
            return  # already known and active — nothing to update
        _ids.add(uid)
        was_blocked = uid in _blocked
        if was_blocked:
            _blocked.discard(uid)
        r = _redis()
        if r is not None:
            try:
                r.sadd(_REDIS_SET, uid)
                if was_blocked:
                    r.srem(_REDIS_BLOCKED, uid)
                return
            except Exception:
                pass  # fall back to the file store
        _save()


def mark_blocked(user_id: int) -> None:
    """Flag a user who blocked the bot / deactivated (found during a broadcast)."""
    uid = str(user_id)
    with _lock:
        _load()
        _ids.add(uid)
        if uid in _blocked:
            return
        _blocked.add(uid)
        r = _redis()
        if r is not None:
            try:
                r.sadd(_REDIS_SET, uid)
                r.sadd(_REDIS_BLOCKED, uid)
                return
            except Exception:
                pass  # fall back to the file store
        _save()


def count() -> int:
    """Total unique users ever seen."""
    with _lock:
        _load()
        return len(_ids)


def blocked_count() -> int:
    """Users who have blocked the bot / deactivated."""
    with _lock:
        _load()
        return len(_blocked)


def active_count() -> int:
    """Users who can still receive messages (all − blocked)."""
    with _lock:
        _load()
        return len(_ids - _blocked)


def active_ids() -> list[int]:
    """Reachable user ids (excludes blocked), as ints — used for broadcasting."""
    with _lock:
        _load()
        out = []
        for u in _ids - _blocked:
            try:
                out.append(int(u))
            except (TypeError, ValueError):
                pass
        return out
