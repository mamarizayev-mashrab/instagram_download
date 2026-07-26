"""URL classification tests — no network, pure parsing."""

import pytest

from utils.url_parser import ContentType, parse


@pytest.mark.parametrize(
    "text,vid",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=30", "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/abc123DEF45", "abc123DEF45"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=RD", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("watch this https://youtu.be/dQw4w9WgXcQ cool", "dQw4w9WgXcQ"),
    ],
)
def test_youtube(text, vid):
    req = parse(text)
    assert req.content_type is ContentType.YOUTUBE
    assert req.shortcode == vid


def test_youtube_channel_without_id_is_unknown():
    req = parse("https://www.youtube.com/@somechannel")
    assert req.content_type is ContentType.UNKNOWN


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@user/video/1234567890",
        "https://vm.tiktok.com/ABCDE/",
        "https://twitter.com/user/status/123",
        "https://x.com/user/status/123",
        "https://www.facebook.com/watch?v=123",
        "https://fb.watch/abc/",
        "https://www.reddit.com/r/x/comments/abc/title/",
        "https://www.pinterest.com/pin/123/",
    ],
)
def test_generic_hosts(url):
    assert parse(url).content_type is ContentType.GENERIC


@pytest.mark.parametrize(
    "url,ctype,code",
    [
        ("https://instagram.com/reel/CxYz123/", ContentType.REEL, "CxYz123"),
        ("https://instagram.com/p/CxYz123/", ContentType.POST, "CxYz123"),
        ("https://instagram.com/tv/CxYz123/", ContentType.IGTV, "CxYz123"),
    ],
)
def test_instagram_shortcodes(url, ctype, code):
    req = parse(url)
    assert req.content_type is ctype
    assert req.shortcode == code


def test_instagram_stories_and_highlight():
    assert parse("https://instagram.com/stories/someone/").content_type is ContentType.STORY
    assert (
        parse("https://instagram.com/stories/highlights/123/").content_type
        is ContentType.HIGHLIGHT
    )


@pytest.mark.parametrize(
    "text,ctype",
    [
        ("@someone pfp", ContentType.PROFILE_PIC),
        ("@someone stories", ContentType.USER_STORIES),
        ("@someone highlights", ContentType.HIGHLIGHT),
    ],
)
def test_username_commands(text, ctype):
    assert parse(text).content_type is ctype


def test_unknown():
    assert parse("hello there").content_type is ContentType.UNKNOWN
    assert parse("").content_type is ContentType.UNKNOWN
