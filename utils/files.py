"""Per-request temp directory management and helpers."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Media extensions we care about, split by kind.
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


@contextmanager
def temp_workdir(prefix: str = "igbot_") -> Iterator[Path]:
    """Create a unique temp directory and always clean it up afterwards."""
    path = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def collect_media(folder: Path) -> list[Path]:
    """Return media files in a folder, videos first, sorted by name (carousel order)."""
    files = [
        p
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.suffix.lower() in (VIDEO_EXTS | IMAGE_EXTS)
    ]
    return files


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def size_mb(path: Path) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0
