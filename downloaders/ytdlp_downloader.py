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

from utils.files import collect_media, temp_workdir

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


def _blocking_download_youtube(
    url: str, workdir: Path, quality: str, max_mb: float, progress_hook=None
) -> list[Path]:
    outtmpl = str(workdir / "%(id)s.%(ext)s")
    ffmpeg = _ffmpeg_location()

    ydl_opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,     # our own progress goes to Telegram, not the log
        "noplaylist": True,     # a single video even if the link carries a playlist
        "retries": 3,
        "socket_timeout": 30,
        "nocheckcertificate": True,
    }
    if ffmpeg:
        ydl_opts["ffmpeg_location"] = ffmpeg
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]
    _apply_youtube_auth(ydl_opts, workdir)

    # Size budget so the result fits Telegram's limit WITHOUT re-encoding — the
    # free tier is too slow to compress. Reserve a little headroom + audio room.
    budget = max(10, int(max_mb * 0.96))          # whole-file ceiling (MB)
    vbudget = max(8, budget - 10)                  # video part, leaving audio room

    if quality == "audio":
        if ffmpeg:
            # Grab best audio and transcode to a universally-playable MP3.
            ydl_opts["format"] = f"bestaudio[filesize_approx<{budget}M]/bestaudio/best"
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
            # Prefer the best rendition (up to the chosen height) that already
            # FITS the size budget, so no compression is needed. Only if nothing
            # is known to fit do we fall back to best-at-height (may need
            # compression) and finally best.
            ydl_opts["format"] = (
                f"bestvideo[height<={height}][filesize_approx<{vbudget}M]+bestaudio/"
                f"best[height<={height}][filesize_approx<{budget}M]/"
                f"best[filesize_approx<{budget}M]/"
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]/best"
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


async def download_youtube(
    url: str, workdir: Path, quality: str = "720", max_mb: float = 50.0,
    progress_hook=None,
) -> list[Path]:
    """Download a YouTube video at a given quality, or its audio as MP3.

    `quality` is one of "360", "720", "1080" (video) or "audio" (MP3/m4a).
    `max_mb` biases format selection toward a rendition that fits the upload
    limit, so no re-encoding is needed. `progress_hook` is a yt-dlp progress
    callback (invoked from the worker thread).
    """
    try:
        return await asyncio.to_thread(
            _blocking_download_youtube, url, workdir, quality, max_mb, progress_hook
        )
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"YouTube download failed: {exc}") from exc


# ---- metadata probe (for the quality picker: title + per-quality sizes) -------

def _fmt_size(f: dict) -> int:
    return int(f.get("filesize") or f.get("filesize_approx") or 0)


def _estimate_sizes(info: dict, duration: int) -> dict:
    """Rough MB estimate for each quality button, from yt-dlp's format list."""
    formats = info.get("formats") or []

    # Only formats with a KNOWN size are usable for an estimate (some HLS/premium
    # renditions report no filesize).
    audio_only = [
        f for f in formats
        if f.get("acodec") not in (None, "none")
        and f.get("vcodec") in (None, "none")
        and _fmt_size(f) > 0
    ]
    best_audio = max(audio_only, key=lambda f: (f.get("abr") or 0), default=None)
    audio_sz = _fmt_size(best_audio) if best_audio else 0

    sizes: dict[str, int | None] = {}
    for label, h in (("360", 360), ("720", 720), ("1080", 1080)):
        vids = [
            f for f in formats
            if f.get("vcodec") not in (None, "none")
            and 0 < (f.get("height") or 0) <= h
            and _fmt_size(f) > 0
        ]
        total = 0
        if vids:
            best_v = max(vids, key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)))
            total = _fmt_size(best_v) + audio_sz
        if not total:
            # progressive muxed stream fallback
            prog = [
                f for f in formats
                if f.get("vcodec") not in (None, "none")
                and f.get("acodec") not in (None, "none")
                and 0 < (f.get("height") or 0) <= h
                and _fmt_size(f) > 0
            ]
            if prog:
                total = _fmt_size(max(prog, key=lambda f: (f.get("height") or 0)))
        sizes[label] = round(total / 1048576) if total else None

    # MP3 at 192 kbps ≈ duration-based; fall back to source audio size.
    if duration:
        sizes["audio"] = round(192 * duration / 8 / 1024)
    elif audio_sz:
        sizes["audio"] = round(audio_sz / 1048576)
    else:
        sizes["audio"] = None
    return sizes


def _blocking_probe_youtube(url: str) -> dict:
    ffmpeg = _ffmpeg_location()
    with temp_workdir("ytprobe_") as wd:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "socket_timeout": 30,
            "nocheckcertificate": True,
        }
        if ffmpeg:
            ydl_opts["ffmpeg_location"] = ffmpeg
        _apply_youtube_auth(ydl_opts, wd)
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    duration = int(info.get("duration") or 0)
    return {
        "title": info.get("title") or "",
        "duration": duration,
        "sizes": _estimate_sizes(info, duration),
    }


async def probe_youtube(url: str) -> dict | None:
    """Fetch title/duration/per-quality sizes for the picker. None on failure."""
    try:
        return await asyncio.to_thread(_blocking_probe_youtube, url)
    except Exception as exc:
        log.info("YouTube probe failed: %s", exc)
        return None


# ---- generic hosts (TikTok / X / Facebook / …) -------------------------------

def _blocking_download_generic(
    url: str, workdir: Path, max_mb: float, progress_hook=None
) -> list[Path]:
    outtmpl = str(workdir / "%(id)s.%(ext)s")
    ffmpeg = _ffmpeg_location()
    budget = max(10, int(max_mb * 0.96))
    vbudget = max(8, budget - 10)

    ydl_opts = {
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
        "socket_timeout": 30,
        "nocheckcertificate": True,
    }
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]
    if ffmpeg:
        ydl_opts["ffmpeg_location"] = ffmpeg
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["format"] = (
            f"best[filesize_approx<{budget}M]/"
            f"bestvideo[filesize_approx<{vbudget}M]+bestaudio/"
            f"best"
        )
    else:
        ydl_opts["format"] = (
            f"best[filesize_approx<{budget}M][acodec!=none][vcodec!=none]/best"
        )

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    files = collect_media(workdir)
    if not files:
        raise DownloadError("yt-dlp produced no media files.")
    return files


async def download_generic(
    url: str, workdir: Path, max_mb: float = 50.0, progress_hook=None
) -> list[Path]:
    """Download a video from any yt-dlp-supported host (TikTok/X/Facebook/…),
    picking the best rendition that fits the upload limit."""
    try:
        return await asyncio.to_thread(
            _blocking_download_generic, url, workdir, max_mb, progress_hook
        )
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"Download failed: {exc}") from exc
