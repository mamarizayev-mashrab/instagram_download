"""Download public Instagram video content (reels, feed videos, IGTV) with yt-dlp.

Runs the blocking yt-dlp call inside a thread so it never blocks the async event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from yt_dlp import YoutubeDL

from utils.files import collect_media

log = logging.getLogger("igbot.ytdlp")


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


# Quality label → max video height. "audio" is handled separately.
_YT_HEIGHTS = {"360": 360, "720": 720, "1080": 1080}


def _apply_youtube_auth(ydl_opts: dict, workdir: Path) -> None:
    """Help get past YouTube's "confirm you're not a bot" check on datacenter
    IPs (Render, etc.).

    - YOUTUBE_COOKIES_FILE: path to a Netscape cookies.txt exported from a
      browser logged in to YouTube. This is the reliable fix.
    - YOUTUBE_PLAYER_CLIENT: comma-separated yt-dlp player clients to try
      (e.g. "android,web_safari,tv"); some clients dodge the bot check.
    """
    cookiefile = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if cookiefile:
        src = Path(cookiefile)
        if src.exists():
            # yt-dlp rewrites the cookie jar after the request. The secret mount
            # (/etc/secrets) is read-only, so copy to the writable workdir first.
            dst = workdir / "cookies.txt"
            try:
                shutil.copyfile(src, dst)
                ydl_opts["cookiefile"] = str(dst)
                log.info("YouTube cookies loaded from %s", cookiefile)
            except OSError as exc:
                log.warning("Could not stage cookies file (%s); continuing without", exc)
        else:
            log.warning("YOUTUBE_COOKIES_FILE set but not found: %s", cookiefile)

    clients = os.getenv("YOUTUBE_PLAYER_CLIENT", "").strip()
    if clients:
        ydl_opts["extractor_args"] = {
            "youtube": {
                "player_client": [c.strip() for c in clients.split(",") if c.strip()]
            }
        }


def _blocking_download_youtube(url: str, workdir: Path, quality: str) -> list[Path]:
    outtmpl = str(workdir / "%(id)s.%(ext)s")
    ffmpeg = _ffmpeg_location()

    ydl_opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,     # a single video even if the link carries a playlist
        "retries": 3,
        "socket_timeout": 30,
        "nocheckcertificate": True,
    }
    if ffmpeg:
        ydl_opts["ffmpeg_location"] = ffmpeg
    _apply_youtube_auth(ydl_opts, workdir)

    if quality == "audio":
        if ffmpeg:
            # Grab best audio and transcode to a universally-playable MP3.
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        else:
            # No ffmpeg → hand back a single audio stream as-is (m4a preferred).
            ydl_opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        height = _YT_HEIGHTS.get(quality, 720)
        if ffmpeg:
            # Merge best video+audio up to the requested height into mp4.
            ydl_opts["format"] = (
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"best[height<={height}][ext=mp4]/best[height<={height}]/best"
            )
            ydl_opts["merge_output_format"] = "mp4"
        else:
            # No ffmpeg → a single progressive stream that already has audio+video.
            ydl_opts["format"] = (
                f"best[height<={height}][ext=mp4][acodec!=none][vcodec!=none]/"
                f"best[height<={height}][acodec!=none][vcodec!=none]/best"
            )

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    files = collect_media(workdir)
    if not files:
        raise DownloadError("yt-dlp produced no media files.")
    return files


async def download_youtube(url: str, workdir: Path, quality: str = "720") -> list[Path]:
    """Download a YouTube video at a given quality, or its audio as MP3.

    `quality` is one of "360", "720", "1080" (video) or "audio" (MP3/m4a).
    """
    try:
        return await asyncio.to_thread(_blocking_download_youtube, url, workdir, quality)
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"YouTube download failed: {exc}") from exc
