"""Optional Redis backend for cross-restart persistence.

Render's free tier has an ephemeral filesystem, so `cache.json` / `userlang.json`
are wiped on every redeploy. Set REDIS_URL (e.g. a free Upstash `rediss://…` URL)
and the cache + language store survive restarts. Without it, everything falls
back to the local JSON files — no Redis dependency is imported in that case.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("igbot.redis")

_client = None
_tried = False


def client():
    """Return a connected redis client, or None if not configured/unavailable.

    Connects lazily once; on any failure it logs and returns None so callers
    transparently fall back to their file-based store.
    """
    global _client, _tried
    if _tried:
        return _client
    _tried = True

    url = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_URL")
    if not url:
        return None
    try:
        import redis  # imported only when a URL is configured

        c = redis.from_url(
            url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5
        )
        c.ping()
        _client = c
        log.info("Redis connected — persistence enabled")
    except Exception as exc:
        log.warning("Redis unavailable (%s); using local files instead", exc)
        _client = None
    return _client
