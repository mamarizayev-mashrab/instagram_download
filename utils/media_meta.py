"""Probe video dimensions/duration and generate a thumbnail with ffmpeg/ffprobe.

Passing width/height/duration/thumbnail to Telegram makes videos show a proper
streamable preview instead of a generic file card.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoMeta:
    width: int = 0
    height: int = 0
    duration: int = 0
    thumbnail: Path | None = None


def _tool(ffmpeg_location: str | None, name: str) -> str:
    if ffmpeg_location:
        p = Path(ffmpeg_location)
        if p.is_dir():
            cand = p / (name + (".exe" if os.name == "nt" else ""))
            if cand.exists():
                return str(cand)
        elif p.exists():
            sib = p.parent / (name + (".exe" if os.name == "nt" else ""))
            if sib.exists():
                return str(sib)
    return name  # PATH lookup


def probe(path: Path, ffmpeg_location: str | None) -> VideoMeta:
    """Return width/height/duration via ffprobe (best-effort; zeros on failure)."""
    ffprobe = _tool(ffmpeg_location, "ffprobe")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(out.stdout or "{}")
    except Exception:
        return VideoMeta()

    meta = VideoMeta()
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            meta.width = int(stream.get("width", 0) or 0)
            meta.height = int(stream.get("height", 0) or 0)
            break
    try:
        meta.duration = int(float(data.get("format", {}).get("duration", 0)))
    except (TypeError, ValueError):
        meta.duration = 0
    return meta


def make_thumbnail(path: Path, ffmpeg_location: str | None) -> Path | None:
    """Grab a single frame as a JPEG thumbnail (<=320px, Telegram-friendly)."""
    ffmpeg = _tool(ffmpeg_location, "ffmpeg")
    thumb = path.with_name(path.stem + "_thumb.jpg")
    # Seek ~1s in; fall back to frame 0 for very short clips.
    cmd = [
        ffmpeg, "-y", "-ss", "1", "-i", str(path),
        "-frames:v", "1", "-vf", "scale=320:-2",
        str(thumb),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if proc.returncode != 0 or not thumb.exists():
        # Retry from the very first frame (clip shorter than 1s).
        cmd[cmd.index("1")] = "0"
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception:
            return None
    if thumb.exists() and thumb.stat().st_size > 0:
        # Telegram thumbnails must stay under 200 KB.
        if thumb.stat().st_size <= 200 * 1024:
            return thumb
    return None


def probe_with_thumb(path: Path, ffmpeg_location: str | None) -> VideoMeta:
    meta = probe(path, ffmpeg_location)
    meta.thumbnail = make_thumbnail(path, ffmpeg_location)
    return meta
