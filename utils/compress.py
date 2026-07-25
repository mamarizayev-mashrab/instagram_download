"""Compress an oversized video with ffmpeg so it fits under Telegram's upload limit.

Strategy: read the duration with ffprobe, compute a target video bitrate from the
size budget (minus an audio-bitrate allowance), then re-encode. Downscale resolution
when the required bitrate would be too low to look acceptable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _tool(ffmpeg_location: str | None, name: str) -> str:
    """Resolve ffmpeg/ffprobe executable from a dir, a full path, or PATH."""
    if ffmpeg_location:
        p = Path(ffmpeg_location)
        if p.is_dir():
            cand = p / (name + (".exe" if os.name == "nt" else ""))
            if cand.exists():
                return str(cand)
        elif p.exists():
            # Full path to ffmpeg given; ffprobe usually sits next to it.
            sibling = p.parent / (name + (".exe" if os.name == "nt" else ""))
            if sibling.exists():
                return str(sibling)
    return name  # fall back to PATH lookup


def _duration_seconds(ffprobe: str, src: Path) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def compress_video(
    src: Path,
    target_mb: float,
    ffmpeg_location: str | None,
) -> Path | None:
    """Re-encode `src` to fit under `target_mb`. Returns the new path or None on failure."""
    ffmpeg = _tool(ffmpeg_location, "ffmpeg")
    ffprobe = _tool(ffmpeg_location, "ffprobe")

    duration = _duration_seconds(ffprobe, src)
    if duration <= 0:
        return None

    # Budget in kilobits; leave 6% headroom for container overhead.
    audio_kbps = 128
    total_kbps = (target_mb * 8 * 1024) / duration * 0.94
    video_kbps = int(total_kbps - audio_kbps)
    if video_kbps < 150:
        # Too little room at full res → we'll also downscale, keep a usable floor.
        video_kbps = 150

    dst = src.with_name(src.stem + "_small.mp4")

    # Cap height to 720 to help the bitrate go further; -2 keeps aspect ratio & even dims.
    cmd = [
        ffmpeg, "-y", "-i", str(src),
        "-vf", "scale=-2:'min(720,ih)'",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps*1.5)}k", "-bufsize", f"{video_kbps*2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None

    if proc.returncode != 0 or not dst.exists():
        return None

    # If it's still too big (very long clip), give up gracefully.
    if dst.stat().st_size / (1024 * 1024) > target_mb:
        return None
    return dst
