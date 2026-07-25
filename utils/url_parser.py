"""Classify an incoming message into an Instagram content type."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ContentType(str, Enum):
    REEL = "reel"          # public → yt-dlp
    POST = "post"          # /p/ — may be video / photo / carousel
    IGTV = "igtv"          # public → yt-dlp
    STORY = "story"        # login → instaloader
    HIGHLIGHT = "highlight"  # login → instaloader
    PROFILE_PIC = "profile_pic"  # login → instaloader
    USER_STORIES = "user_stories"  # login → instaloader
    UNKNOWN = "unknown"


@dataclass
class ParsedRequest:
    content_type: ContentType
    url: str | None = None          # original URL if any
    username: str | None = None     # for pfp / user stories
    shortcode: str | None = None    # instagram shortcode for /p/ /reel/ /tv/
    story_target: str | None = None  # username inside a /stories/<user>/ link


_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/\S+", re.IGNORECASE)
_SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
_STORIES_RE = re.compile(r"instagram\.com/stories/([A-Za-z0-9_.]+)", re.IGNORECASE)
_HIGHLIGHT_RE = re.compile(r"instagram\.com/stories/highlights/(\d+)", re.IGNORECASE)


def _extract_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0).strip() if match else None


def parse(text: str) -> ParsedRequest:
    """Turn raw user text into a ParsedRequest.

    Supports:
      - reel / post / igtv / story / highlight Instagram links
      - "@username pfp"  or  "@username profile"  -> HD profile picture
      - "@username stories"                        -> that user's stories
    """
    text = (text or "").strip()
    lower = text.lower()

    # --- Non-URL keyword commands: "@username pfp" / "@username stories" ---
    m = re.match(
        r"@?([A-Za-z0-9_.]+)\s+(pfp|profile|avatar|stories?|highlights?)\b",
        text,
        re.IGNORECASE,
    )
    if m and "instagram.com" not in lower:
        username = m.group(1)
        kind = m.group(2).lower()
        if kind in ("pfp", "profile", "avatar"):
            return ParsedRequest(ContentType.PROFILE_PIC, username=username)
        if kind.startswith("highlight"):
            return ParsedRequest(ContentType.HIGHLIGHT, username=username)
        return ParsedRequest(ContentType.USER_STORIES, username=username)

    url = _extract_url(text)
    if not url:
        return ParsedRequest(ContentType.UNKNOWN)

    # Order matters: highlight is a more specific stories URL.
    hl = _HIGHLIGHT_RE.search(url)
    if hl:
        return ParsedRequest(ContentType.HIGHLIGHT, url=url, shortcode=hl.group(1))

    st = _STORIES_RE.search(url)
    if st:
        return ParsedRequest(ContentType.STORY, url=url, story_target=st.group(1))

    sc = _SHORTCODE_RE.search(url)
    if sc:
        shortcode = sc.group(1)
        if "/reel" in url.lower() or "/reels" in url.lower():
            ctype = ContentType.REEL
        elif "/tv/" in url.lower():
            ctype = ContentType.IGTV
        else:
            ctype = ContentType.POST
        return ParsedRequest(ctype, url=url, shortcode=shortcode)

    # A bare instagram.com/<username>/ profile link → treat as profile picture request.
    prof = re.search(r"instagram\.com/([A-Za-z0-9_.]+)/?$", url, re.IGNORECASE)
    if prof and prof.group(1).lower() not in ("p", "reel", "reels", "tv", "stories"):
        return ParsedRequest(ContentType.PROFILE_PIC, url=url, username=prof.group(1))

    return ParsedRequest(ContentType.UNKNOWN, url=url)
