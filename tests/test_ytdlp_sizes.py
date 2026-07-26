"""Unit tests for the YouTube size-estimation helpers (no network)."""

from downloaders.ytdlp_downloader import _estimate_sizes, _fmt_size


def test_fmt_size_prefers_filesize_then_approx():
    assert _fmt_size({"filesize": 100}) == 100
    assert _fmt_size({"filesize_approx": 200}) == 200
    assert _fmt_size({}) == 0


def _mb(n):
    return n * 1048576


def test_estimate_sizes_merges_video_plus_audio():
    info = {
        "formats": [
            {"vcodec": "avc1", "acodec": "none", "height": 360, "tbr": 500,
             "filesize_approx": _mb(20)},
            {"vcodec": "avc1", "acodec": "none", "height": 720, "tbr": 1500,
             "filesize_approx": _mb(60)},
            {"vcodec": "none", "acodec": "mp4a", "abr": 128,
             "filesize_approx": _mb(5)},
        ]
    }
    sizes = _estimate_sizes(info, duration=0)
    # 360p → 20 (video) + 5 (audio) = 25 MB
    assert sizes["360"] == 25
    # 720p → 60 + 5 = 65 MB
    assert sizes["720"] == 65
    # No 1080p stream present → best <=1080 is the 720p one
    assert sizes["1080"] == 65


def test_estimate_audio_from_duration():
    sizes = _estimate_sizes({"formats": []}, duration=600)  # 10 min
    # 192 kbps * 600s / 8 / 1024 ≈ 14 MB
    assert 13 <= sizes["audio"] <= 15


def test_estimate_handles_empty_formats():
    sizes = _estimate_sizes({"formats": []}, duration=0)
    assert sizes["360"] is None and sizes["audio"] is None
