"""Download public Instagram video content (reels, feed videos, IGTV) with yt-dlp.

Runs the blocking yt-dlp call inside a thread so it never blocks the async event loop.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from yt_dlp import YoutubeDL

from utils.files import collect_media


class DownloadError(Exception):
    """Raised when yt-dlp fails to fetch the content."""


def _ffmpeg_location() -> str | None:
    """Return a usable ffmpeg path: explicit env var, then PATH lookup."""
    env = os.getenv("FFMPEG_LOCATION", "").strip()
    if env and (Path(env).exists() or shutil.which(env)):
        return env
    found = shutil.which("ffmpeg")
    return found if found else None


def _blocking_download(url: str, workdir: Path) -> list[Path]:
    outtmpl = str(workdir / "%(id)s.%(ext)s")
    ffmpeg = _ffmpeg_location()

    if ffmpeg:
        # ffmpeg available → grab best video+audio and merge for top quality.
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    else:
        # No ffmpeg → pick a single progressive stream that already has audio+video,
        # so nothing needs merging. Instagram serves such an mp4 for reels/videos.
        fmt = "best[ext=mp4][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]/best"

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": fmt,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,   # a carousel post can expose multiple entries
        "retries": 3,
        "socket_timeout": 30,
        "nocheckcertificate": True,
    }
    if ffmpeg:
        ydl_opts["ffmpeg_location"] = ffmpeg
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    files = collect_media(workdir)
    if not files:
        raise DownloadError("yt-dlp produced no media files.")
    return files


async def download(url: str, workdir: Path) -> list[Path]:
    """Async wrapper around yt-dlp. Returns a list of downloaded media paths."""
    try:
        return await asyncio.to_thread(_blocking_download, url, workdir)
    except DownloadError:
        raise
    except Exception as exc:  # yt_dlp raises many concrete types
        raise DownloadError(f"Public download failed: {exc}") from exc
