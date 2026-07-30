"""Instagram media downloader Telegram bot (aiogram 3.x).

Flow:
    user message → classify URL/command → route to the right downloader
    → send media back (video / photo / album) → clean up temp files.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import secrets
import time
from urllib.parse import urlsplit, urlunsplit

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedAudio,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    Message,
)
from dotenv import load_dotenv

from downloaders import insta_downloader, ytdlp_downloader
from downloaders.insta_downloader import InstaAuthError, InstaClient, InstaDownloadError
from downloaders.ytdlp_downloader import DownloadError
from utils import cache
from utils import i18n
from utils import users
from utils.compress import compress_video
from utils.files import collect_media, is_video, size_mb, temp_workdir
from utils.media_meta import probe_with_thumb
from utils.url_parser import ContentType, ParsedRequest, parse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("igbot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
IG_USERNAME = os.getenv("IG_USERNAME", "").strip()
IG_PASSWORD = os.getenv("IG_PASSWORD", "").strip()
FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION", "").strip() or None

# Point the bot at a self-hosted Telegram Bot API server to lift the upload
# limit from 50 MB (public api.telegram.org) up to 2 GB. Leave unset to use
# Telegram's public API. See docker-compose.yml / README for how to run one.
TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "").strip()
# When a local server is configured the ceiling is 2 GB; otherwise 50 MB.
_default_limit = "2000" if TELEGRAM_API_URL else "50"
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", _default_limit))

# Cap concurrent downloads so a burst of requests can't exhaust the (small)
# free-tier RAM/CPU with parallel yt-dlp + ffmpeg + Deno work.
MAX_CONCURRENT_DOWNLOADS = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")))
_download_sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# One in-flight download per user, so a single user can't queue a flood of links
# and monopolise the (global) download slots.
_active_users: set[int] = set()


def _acquire_user(uid: int) -> bool:
    """Reserve the per-user download slot. False if the user already has one running."""
    if uid in _active_users:
        return False
    _active_users.add(uid)
    return True


def _release_user(uid: int) -> None:
    _active_users.discard(uid)

# A valid YouTube video id (used to sanity-check callback data before building a URL).
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Where to send operational alerts (cookie expiry, unexpected errors). Optional.
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
_alert_cooldown: dict[str, float] = {}

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

# instaloader client is optional. It works with EITHER an imported session file
# (see setup_session.py) or a username+password. Enable it whenever a username is set.
insta_client: InstaClient | None = (
    InstaClient(IG_USERNAME, IG_PASSWORD) if IG_USERNAME else None
)

dp = Dispatcher()


@dp.update.outer_middleware()
async def _track_user(handler, event, data):
    """Record every user who interacts with the bot (for /stats)."""
    user = data.get("event_from_user")
    if user is not None:
        try:
            users.track(user.id)
        except Exception:
            log.debug("User tracking failed", exc_info=True)
    return await handler(event, data)


def _lang_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard with the four language options."""
    buttons = [
        InlineKeyboardButton(text=name, callback_data=f"setlang:{code}")
        for code, name in i18n.LANG_NAMES.items()
    ]
    # Two per row.
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _sz(sizes: dict | None, key: str) -> str:
    """' · ~45 MB' suffix for a quality button, or '' when size is unknown."""
    if sizes and sizes.get(key):
        return f" · ~{sizes[key]} MB"
    return ""


def _yt_keyboard(vid: str, sizes: dict | None = None) -> InlineKeyboardMarkup:
    """Quality picker shown for a YouTube link. The video id rides in callback_data
    so the handler stays stateless (no per-user pending store needed). When known,
    each button shows the approximate file size."""
    rows = [
        [
            InlineKeyboardButton(text=f"🎬 360p{_sz(sizes, '360')}", callback_data=f"yt:360:{vid}"),
            InlineKeyboardButton(text=f"🎬 720p{_sz(sizes, '720')}", callback_data=f"yt:720:{vid}"),
        ],
        [
            InlineKeyboardButton(text=f"🎬 1080p{_sz(sizes, '1080')}", callback_data=f"yt:1080:{vid}"),
            InlineKeyboardButton(text=f"🎵 MP3{_sz(sizes, 'audio')}", callback_data=f"yt:audio:{vid}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    uid = message.from_user.id
    # Deep link from the inline "open bot to download" button → fetch it straight away.
    arg = (command.args or "").strip()
    if arg:
        link = _payload_to_text(arg)
        if link:
            await _process_link(message, link)
            return
    await message.answer(i18n.t(uid, "welcome"))
    # First-time users: also show the language picker.
    if not i18n.has_lang(uid):
        await message.answer(i18n.t(uid, "lang_choose"), reply_markup=_lang_keyboard())


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(i18n.t(message.from_user.id, "help"))


@dp.message(Command("id"))
async def cmd_id(message: Message) -> None:
    # Handy for setting ADMIN_CHAT_ID: reply with this chat's numeric id.
    await message.answer(f"<code>{message.chat.id}</code>")


def _is_admin(message: Message) -> bool:
    return bool(ADMIN_CHAT_ID) and str(message.chat.id) == ADMIN_CHAT_ID


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    # Admin-only: total users, still-active (reachable), and blocked counts.
    if not _is_admin(message):
        return
    await message.answer(
        "📊 <b>Statistika</b>\n"
        f"👥 Jami foydalanuvchilar: <b>{users.count()}</b>\n"
        f"✅ Faol (xabar yetadi): <b>{users.active_count()}</b>\n"
        f"🚫 Bloklaganlar: <b>{users.blocked_count()}</b>"
    )


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    """Admin-only: send a message to every known user.

    Two ways to use it:
      • Reply to any message (text/photo/video/…) with /broadcast — that exact
        message is copied to everyone.
      • /broadcast <text> — sends the text (HTML formatting supported).
    """
    if not _is_admin(message):
        return

    reply = message.reply_to_message
    text = ""
    parts = (message.text or message.caption or "").split(maxsplit=1)
    if len(parts) > 1:
        text = parts[1].strip()

    if reply is None and not text:
        await message.answer(
            "📣 <b>Broadcast</b>\n\n"
            "Xabar yuboring: <code>/broadcast matn</code>\n"
            "yoki istalgan xabarga <b>reply</b> qilib <code>/broadcast</code> deb yozing "
            "(rasm/video ham yuboriladi)."
        )
        return

    uids = users.active_ids()   # skip users already known to have blocked the bot
    total = len(uids)
    if total == 0:
        await message.answer("Hali foydalanuvchilar yo'q.")
        return

    progress = await message.answer(f"📤 Yuborilyapti… 0/{total}")
    sent = blocked = failed = 0
    # Telegram tolerates ~30 msgs/sec; stay comfortably under it.
    delay = float(os.getenv("BROADCAST_DELAY", "0.05"))

    for i, uid in enumerate(uids, 1):
        try:
            if reply is not None:
                await message.bot.copy_message(uid, reply.chat.id, reply.message_id)
            else:
                await message.bot.send_message(uid, text)
            sent += 1
        except TelegramRetryAfter as exc:
            # Hit the flood limit — wait it out, then retry this same user once.
            await asyncio.sleep(exc.retry_after + 1)
            try:
                if reply is not None:
                    await message.bot.copy_message(uid, reply.chat.id, reply.message_id)
                else:
                    await message.bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            # User blocked the bot or deactivated → flag them (shows up in /stats).
            blocked += 1
            users.mark_blocked(uid)
        except Exception as exc:
            failed += 1
            log.info("Broadcast to %s failed: %s", uid, exc)

        if i % 25 == 0 or i == total:
            try:
                await progress.edit_text(f"📤 Yuborilyapti… {i}/{total}")
            except Exception:
                pass
        await asyncio.sleep(delay)

    await progress.edit_text(
        f"✅ Broadcast tugadi.\n"
        f"Yuborildi: <b>{sent}</b>\n"
        f"Bloklagan/o'chirilgan: <b>{blocked}</b>\n"
        f"Xato: <b>{failed}</b>"
    )


@dp.message(Command("language", "lang"))
async def cmd_language(message: Message) -> None:
    uid = message.from_user.id
    # Show the welcome blurb first, then the language picker.
    await message.answer(i18n.t(uid, "welcome"))
    await message.answer(i18n.t(uid, "lang_choose"), reply_markup=_lang_keyboard())


@dp.callback_query(F.data.startswith("setlang:"))
async def on_set_language(call: CallbackQuery) -> None:
    code = call.data.split(":", 1)[1]
    i18n.set_lang(call.from_user.id, code)
    await call.answer()
    # Confirm + re-show the welcome in the newly chosen language.
    try:
        await call.message.edit_text(i18n.t(call.from_user.id, "lang_set"))
    except Exception:
        await call.message.answer(i18n.t(call.from_user.id, "lang_set"))
    await call.message.answer(i18n.t(call.from_user.id, "welcome"))


@dp.callback_query(F.data.startswith("yt:"))
async def on_youtube_quality(call: CallbackQuery) -> None:
    """Download the YouTube video/audio at the quality the user tapped."""
    uid = call.from_user.id
    try:
        _, quality, vid = call.data.split(":", 2)
    except ValueError:
        await call.answer()
        return

    # Guard: only ever build a URL from a well-formed video id.
    if quality not in ("360", "720", "1080", "audio") or not _YT_ID.match(vid):
        await call.answer()
        return

    url = f"https://www.youtube.com/watch?v={vid}"
    await call.answer()
    # Collapse the picker so it can't be tapped twice.
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Instant resend if we've already produced this exact video/quality before.
    key = f"youtube:{vid}:{quality}"
    cached = cache.get(key)
    if cached and await _send_from_cache(call.message, cached):
        log.info("Cache hit for %s", key)
        return

    if not _acquire_user(uid):
        await call.message.answer(i18n.t(uid, "busy"))
        return
    status = await call.message.answer(i18n.t(uid, "downloading"))
    try:
        async with _download_sem:
            with temp_workdir() as workdir:
                files = await _download_with_progress(
                    lambda h: ytdlp_downloader.download_youtube(
                        url, workdir, quality, MAX_UPLOAD_MB, progress_hook=h
                    ),
                    status, uid,
                )
                files = collect_media(workdir) or files
                if quality == "audio":
                    sent = await _send_audio(call.message, files, uid)
                else:
                    sent = await _send_media(call.message, files, uid)
                if sent:
                    cache.put(key, sent)
        await status.delete()
    except DownloadError as exc:
        log.info("YouTube download error: %s", exc)
        await status.edit_text(_err_text(uid, exc))
        if _looks_like_auth_issue(exc):
            await _alert_admin(
                call.bot, "yt_auth",
                f"YouTube download failing — cookies may have expired.\n{exc}",
            )
    except Exception as exc:
        log.exception("Unexpected YouTube error")
        await status.edit_text(i18n.t(uid, "unexpected"))
        await _alert_admin(call.bot, "yt_unexpected", f"Unexpected YouTube error: {exc}")
    finally:
        _release_user(uid)


async def _alert_admin(bot: Bot, key: str, text: str, cooldown: int = 600) -> None:
    """Send an operational alert to the admin chat, de-duplicated per `key`."""
    if not ADMIN_CHAT_ID:
        return
    now = time.monotonic()
    if now - _alert_cooldown.get(key, 0) < cooldown:
        return
    _alert_cooldown[key] = now
    try:
        await bot.send_message(int(ADMIN_CHAT_ID), f"⚠️ {text}"[:4000])
    except Exception as exc:
        log.warning("Admin alert failed: %s", exc)


def _looks_like_auth_issue(exc: Exception) -> bool:
    """Heuristic: does this failure smell like expired cookies / a login wall?"""
    m = str(exc).lower()
    return any(
        s in m for s in (
            "sign in", "not a bot", "confirm you", "cookies", "login required",
            "unauthorized", "age-restricted", "private video", "members-only",
        )
    )


def _make_progress(state: dict):
    """Build a yt-dlp progress hook that records progress into `state`.
    Runs in the download worker thread; only does cheap dict writes."""
    def hook(d: dict) -> None:
        st = d.get("status")
        if st == "downloading":
            state["started"] = True
            done = d.get("downloaded_bytes") or 0
            state["mb"] = done / 1048576
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                state["pct"] = max(state.get("pct", 0), int(done / total * 100))
                state["has_total"] = True
        elif st == "finished":
            state["stage"] = "merge"
    return hook


async def _download_with_progress(factory, status: Message, uid: int) -> list:
    """Run a download coroutine (built by `factory(hook)`) while live-editing
    `status` with the current progress every couple of seconds."""
    state: dict = {"pct": 0, "mb": 0.0, "stage": "download", "started": False,
                   "has_total": False}
    task = asyncio.create_task(factory(_make_progress(state)))
    last = ""
    while not task.done():
        done, _ = await asyncio.wait({task}, timeout=2)
        if done:
            break
        if state["stage"] == "merge":
            txt = i18n.t(uid, "merging")
        elif not state["started"]:
            # Extraction phase (solving YouTube's JS challenge) — no bytes yet.
            txt = i18n.t(uid, "preparing")
        elif state["has_total"]:
            txt = i18n.t(uid, "downloading_pct", pct=state["pct"])
        else:
            txt = i18n.t(uid, "downloading_mb", mb=round(state["mb"], 1))
        if txt != last:
            try:
                await status.edit_text(txt)
            except Exception:
                pass
            last = txt
    return await task


def _needs_login(req: ParsedRequest) -> bool:
    return req.content_type in (
        ContentType.STORY,
        ContentType.USER_STORIES,
        ContentType.HIGHLIGHT,
        ContentType.PROFILE_PIC,
    )


# Only immutable content is cacheable — stories/highlights/pfp change or expire.
_CACHEABLE = (ContentType.REEL, ContentType.POST, ContentType.IGTV)


def _cache_key(req: ParsedRequest) -> str | None:
    if req.content_type in _CACHEABLE and req.shortcode:
        return f"{req.content_type.value}:{req.shortcode}"
    return None


def _generic_cache_key(url: str | None) -> str | None:
    """Cache key for TikTok/X/Facebook/Reddit URLs. Query is kept (some hosts,
    e.g. Facebook watch?v=, need it) and only the fragment is dropped, so a hit
    always means the same content — never a false match."""
    if not url:
        return None
    try:
        s = urlsplit(url.strip())
        norm = urlunsplit((s.scheme.lower(), s.netloc.lower(), s.path.rstrip("/"), s.query, ""))
    except Exception:
        norm = url.strip()
    return f"generic:{norm}"


# Message substrings → a specific, translated error key (falls back to a generic one).
_ERROR_SIGNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("err_age", ("confirm your age", "age-restricted", "age restricted", "inappropriate for some users")),
    ("err_members", ("members-only", "members only", "join this channel", "join to watch")),
    ("err_geo", ("not available in your country", "geo restrict", "geoblock", "blocked in your country")),
    ("err_private", ("is private", "private and not followed", "login required", "log in to", "requested content is not available")),
    ("err_deleted", ("not exist", "unavailable", "has been removed", "no longer available", "not found", "404", "deleted", "removed by")),
)


def _error_key(exc: Exception) -> str:
    m = str(exc).lower()
    for key, signs in _ERROR_SIGNS:
        if any(s in m for s in signs):
            return key
    return "download_error"


def _err_text(uid: int, exc: Exception) -> str:
    """Translated, user-friendly text for a download failure."""
    key = _error_key(exc)
    if key == "download_error":
        return i18n.t(uid, "download_error", err=exc)
    return i18n.t(uid, key)


def _looks_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    signs = (
        "wait a few minutes", "429", "rate", "too many", "please wait",
        # instaloader transient failures on datacenter IPs — Instagram returns a
        # 401/400/JSON error that surfaces as one of these. A short backoff often
        # clears it, so treat them as retryable rather than a hard failure.
        "fetching post metadata failed", "metadata failed",
        "json query", "bad request", "please wait a few minutes",
        "checkpoint", "temporarily", "connection",
    )
    return any(s in msg for s in signs)


async def _dispatch_with_retry(req: ParsedRequest, workdir, status: Message, uid: int) -> list:
    """Run the download, retrying with backoff when Instagram rate-limits us."""
    delays = [8, 20, 40]  # seconds between attempts
    last_exc: Exception | None = None
    for attempt in range(len(delays) + 1):
        try:
            return await _dispatch_download(req, workdir)
        except (DownloadError, InstaDownloadError, InstaAuthError) as exc:
            last_exc = exc
            if attempt < len(delays) and _looks_rate_limited(exc):
                wait = delays[attempt]
                log.info("Rate-limited, retrying in %ss (attempt %s)", wait, attempt + 1)
                try:
                    await status.edit_text(i18n.t(uid, "retrying", sec=wait))
                except Exception:
                    pass
                await asyncio.sleep(wait)
                continue
            raise
    raise last_exc  # pragma: no cover


async def _dispatch_download(req: ParsedRequest, workdir) -> list:
    """Route a parsed request to the correct downloader and return media paths."""
    ct = req.content_type

    # Public video content → yt-dlp (no login).
    if ct in (ContentType.REEL, ContentType.IGTV):
        return await ytdlp_downloader.download(req.url, workdir)

    # Posts can be video OR photo/carousel. Try yt-dlp first (fast for videos);
    # fall back to instaloader for photos/carousels it can't handle.
    if ct == ContentType.POST:
        try:
            return await ytdlp_downloader.download(req.url, workdir)
        except DownloadError:
            if insta_client and req.shortcode:
                return await insta_client.download_post(req.shortcode, workdir)
            raise

    # Everything below requires a logged-in instaloader session.
    if insta_client is None:
        raise InstaAuthError(
            "This content type needs the bot's Instagram login, which isn't configured."
        )

    if ct == ContentType.HIGHLIGHT:
        # Highlight links reference a user; instaloader pulls all their highlights.
        target = req.username or req.story_target
        if not target:
            raise InstaDownloadError(
                "Highlight links don't include the username. "
                "Send <code>@username highlights</code> instead."
            )
        return await insta_client.download_highlights(target, workdir)

    if ct in (ContentType.STORY, ContentType.USER_STORIES):
        target = req.story_target or req.username
        if not target:
            raise InstaDownloadError("Couldn't determine whose stories to fetch.")
        return await insta_client.download_stories(target, workdir)

    if ct == ContentType.PROFILE_PIC:
        if not req.username:
            raise InstaDownloadError("Couldn't determine the username for the profile picture.")
        return await insta_client.download_profile_pic(req.username, workdir)

    raise InstaDownloadError("Unsupported content type.")


async def _fit_limit(message: Message, path, uid: int):
    """Return a path that fits under the limit, compressing an oversized video if needed."""
    if size_mb(path) <= MAX_UPLOAD_MB:
        return path
    if not is_video(path):
        return None  # can't shrink a photo meaningfully
    await message.answer(i18n.t(uid, "compressing"))
    smaller = await asyncio.to_thread(compress_video, path, MAX_UPLOAD_MB, FFMPEG_LOCATION)
    if smaller and size_mb(smaller) <= MAX_UPLOAD_MB:
        return smaller
    return None


def _video_kwargs(path):
    """Build width/height/duration/thumbnail kwargs so Telegram streams the video."""
    meta = probe_with_thumb(path, FFMPEG_LOCATION)
    kwargs = {"supports_streaming": True}
    if meta.width and meta.height:
        kwargs["width"] = meta.width
        kwargs["height"] = meta.height
    if meta.duration:
        kwargs["duration"] = meta.duration
    if meta.thumbnail:
        kwargs["thumbnail"] = FSInputFile(meta.thumbnail)
    return kwargs


async def _send_audio(message: Message, files: list, uid: int) -> list[dict]:
    """Send audio files, returning [{'kind':'audio','file_id'}] for caching."""
    sent_ids: list[dict] = []
    too_big = 0
    for f in files:
        if size_mb(f) > MAX_UPLOAD_MB:
            too_big += 1
            continue
        msg = await message.answer_audio(FSInputFile(f))
        if msg.audio:
            sent_ids.append({"kind": "audio", "file_id": msg.audio.file_id})
    if not sent_ids:
        await message.answer(i18n.t(uid, "too_large", mb=MAX_UPLOAD_MB))
        return []
    if too_big:
        await message.answer(i18n.t(uid, "too_big", n=too_big, mb=MAX_UPLOAD_MB))
    return sent_ids


async def _send_media(message: Message, files: list, uid: int) -> list[dict]:
    """Send media, return [{'kind','file_id'}] for caching. Compress oversized videos."""
    sendable = []
    too_big = 0
    for f in files:
        fitted = await _fit_limit(message, f, uid)
        if fitted is not None:
            sendable.append(fitted)
        else:
            too_big += 1

    if not sendable:
        await message.answer(i18n.t(uid, "too_large", mb=MAX_UPLOAD_MB))
        return []

    sent_ids: list[dict] = []

    # Single item → richer video preview via metadata/thumbnail.
    if len(sendable) == 1:
        f = sendable[0]
        if is_video(f):
            msg = await message.answer_video(
                FSInputFile(f), **await asyncio.to_thread(_video_kwargs, f)
            )
            if msg.video:
                sent_ids.append({"kind": "video", "file_id": msg.video.file_id})
        else:
            msg = await message.answer_photo(FSInputFile(f))
            if msg.photo:
                sent_ids.append({"kind": "photo", "file_id": msg.photo[-1].file_id})
    else:
        # Album — up to 10 items per media group; chunk if needed.
        for i in range(0, len(sendable), 10):
            chunk = sendable[i : i + 10]
            media = []
            for f in chunk:
                if is_video(f):
                    media.append(InputMediaVideo(media=FSInputFile(f)))
                else:
                    media.append(InputMediaPhoto(media=FSInputFile(f)))
            msgs = await message.answer_media_group(media)
            for m in msgs:
                if m.video:
                    sent_ids.append({"kind": "video", "file_id": m.video.file_id})
                elif m.photo:
                    sent_ids.append({"kind": "photo", "file_id": m.photo[-1].file_id})

    if too_big:
        await message.answer(i18n.t(uid, "too_big", n=too_big, mb=MAX_UPLOAD_MB))
    return sent_ids


async def _send_from_cache(message: Message, items: list[dict]) -> bool:
    """Resend previously uploaded media by file_id. Returns False if it fails."""
    try:
        if len(items) == 1:
            it = items[0]
            if it["kind"] == "video":
                await message.answer_video(it["file_id"])
            elif it["kind"] == "audio":
                await message.answer_audio(it["file_id"])
            else:
                await message.answer_photo(it["file_id"])
        else:
            media = []
            for it in items:
                if it["kind"] == "video":
                    media.append(InputMediaVideo(media=it["file_id"]))
                elif it["kind"] == "photo":
                    media.append(InputMediaPhoto(media=it["file_id"]))
                else:
                    # Audio can't share a photo/video media group — resend on its own.
                    await message.answer_audio(it["file_id"])
            if media:
                await message.answer_media_group(media)
        return True
    except Exception as exc:  # stale file_id → fall back to a fresh download
        log.info("Cache resend failed (%s); will re-download.", exc)
        return False


# ---- inline mode (@botname <link> in any chat) --------------------------------

_bot_username: str | None = None


async def _bot_user(bot: Bot) -> str:
    """The bot's @username (cached), needed for deep links in inline results."""
    global _bot_username
    if _bot_username is None:
        me = await bot.me()
        _bot_username = me.username or ""
    return _bot_username


def _deeplink_payload(req: ParsedRequest) -> str | None:
    """A short, url-safe /start payload that re-identifies this content (<=64 chars)."""
    if req.content_type == ContentType.YOUTUBE and req.shortcode:
        return f"yt_{req.shortcode}"
    prefix = {
        ContentType.REEL: "igr_", ContentType.POST: "igp_", ContentType.IGTV: "igt_",
    }.get(req.content_type)
    if prefix and req.shortcode:
        return f"{prefix}{req.shortcode}"
    return None


def _payload_to_text(payload: str) -> str | None:
    """Reverse of _deeplink_payload: rebuild a link the parser understands."""
    payload = (payload or "").strip()
    if payload.startswith("yt_"):
        return f"https://www.youtube.com/watch?v={payload[3:]}"
    if payload.startswith("igr_"):
        return f"https://www.instagram.com/reel/{payload[4:]}/"
    if payload.startswith("igp_"):
        return f"https://www.instagram.com/p/{payload[4:]}/"
    if payload.startswith("igt_"):
        return f"https://www.instagram.com/tv/{payload[4:]}/"
    return None


def _inline_from_cache(items: list[dict]) -> list:
    """Turn cached file_ids into inline results Telegram can serve instantly."""
    out: list = []
    for i, it in enumerate(items[:10]):
        fid = it.get("file_id")
        if not fid:
            continue
        kind = it.get("kind")
        if kind == "video":
            out.append(InlineQueryResultCachedVideo(id=str(i), video_file_id=fid, title="Video"))
        elif kind == "audio":
            out.append(InlineQueryResultCachedAudio(id=str(i), audio_file_id=fid))
        else:
            out.append(InlineQueryResultCachedPhoto(id=str(i), photo_file_id=fid))
    return out


@dp.inline_query()
async def on_inline_query(query: InlineQuery) -> None:
    """Serve already-cached media instantly in any chat; otherwise offer a button
    that opens the bot and downloads it."""
    uid = query.from_user.id
    text = (query.query or "").strip()
    if not text:
        await query.answer(
            [InlineQueryResultArticle(
                id="hint",
                title="🔗 Havola yuboring / Send a link",
                description="Instagram · YouTube · TikTok · X · Facebook",
                input_message_content=InputTextMessageContent(message_text=i18n.t(uid, "help")),
            )],
            cache_time=10, is_personal=True,
        )
        return

    req = parse(text)
    if req.content_type == ContentType.YOUTUBE and req.shortcode:
        key = f"youtube:{req.shortcode}:720"   # inline serves the default quality
    elif req.content_type == ContentType.GENERIC:
        key = _generic_cache_key(req.url)
    else:
        key = _cache_key(req)

    cached = cache.get(key) if key else None
    if cached:
        results = _inline_from_cache(cached)
        if results:
            await query.answer(results, cache_time=300, is_personal=False)
            return

    # Not cached (or not cacheable) → a button that opens the bot to fetch it.
    username = await _bot_user(query.bot)
    payload = _deeplink_payload(req)
    if username and payload:
        deep = f"https://t.me/{username}?start={payload}"
    elif username:
        deep = f"https://t.me/{username}"
    else:
        deep = None
    kb = None
    if deep:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Botda yuklab olish", url=deep)]
        ])
    await query.answer(
        [InlineQueryResultArticle(
            id="download",
            title="⚡ Yuklab olish uchun bosing",
            description="Botni ochib shu havolani yuklab oling.",
            input_message_content=InputTextMessageContent(message_text=text),
            reply_markup=kb,
        )],
        cache_time=5, is_personal=True,
    )


@dp.message(F.text | F.caption)
async def handle_link(message: Message) -> None:
    # Also catch links sent as a media caption, not just plain-text messages.
    await _process_link(message, message.text or message.caption or "")


async def _process_link(message: Message, text: str) -> None:
    """Classify `text` and route it to the right downloader. Shared by the text
    handler and the /start deep-link (inline "open bot to download") flow."""
    if message.from_user is None:
        return  # channel/anonymous posts carry no user — nothing to attribute
    uid = message.from_user.id
    req = parse(text)

    # In groups the bot must stay quiet unless there's a real, downloadable link:
    # never reply to ordinary chatter, and ignore bare "@user pfp/stories" keyword
    # commands (a private-chat convenience) since they'd fire on normal mentions.
    in_private = message.chat.type == ChatType.PRIVATE
    if not in_private and (req.content_type == ContentType.UNKNOWN or not req.url):
        return

    if req.content_type == ContentType.UNKNOWN:
        await message.answer(i18n.t(uid, "unknown"))
        return

    # YouTube → probe metadata, then let the user pick a quality (or audio).
    if req.content_type == ContentType.YOUTUBE:
        vid = req.shortcode
        probing = await message.answer(i18n.t(uid, "probing"))
        # Probe spawns a yt-dlp worker thread; keep it under the same global cap
        # as downloads so a burst of YouTube links can't spawn N parallel threads.
        async with _download_sem:
            meta = await ytdlp_downloader.probe_youtube(
                f"https://www.youtube.com/watch?v={vid}"
            )
        text = i18n.t(uid, "yt_choose")
        if meta and meta.get("title"):
            head = f"🎬 <b>{html.escape(meta['title'])[:120]}</b>"
            dur = _fmt_duration(meta.get("duration"))
            if dur:
                head += f"  ·  ⏱ {dur}"
            text = f"{head}\n\n{text}"
        kb = _yt_keyboard(vid, meta.get("sizes") if meta else None)
        try:
            await probing.edit_text(text, reply_markup=kb)
        except Exception:
            await message.answer(text, reply_markup=kb)
        return

    # Other public video hosts (TikTok / X / Facebook / …) → direct best-fit.
    if req.content_type == ContentType.GENERIC:
        # Instant resend if we've already fetched this exact URL before.
        gkey = _generic_cache_key(req.url)
        if gkey:
            cached = cache.get(gkey)
            if cached and await _send_from_cache(message, cached):
                log.info("Cache hit for %s", gkey)
                return
        if not _acquire_user(uid):
            await message.answer(i18n.t(uid, "busy"))
            return
        status = await message.answer(i18n.t(uid, "downloading"))
        try:
            async with _download_sem:
                with temp_workdir() as workdir:
                    files = await _download_with_progress(
                        lambda h: ytdlp_downloader.download_generic(
                            req.url, workdir, MAX_UPLOAD_MB, progress_hook=h
                        ),
                        status, uid,
                    )
                    files = collect_media(workdir) or files
                    sent = await _send_media(message, files, uid)
                    if gkey and sent:
                        cache.put(gkey, sent)  # remember for instant future resends
            await status.delete()
        except DownloadError as exc:
            log.info("Generic download error: %s", exc)
            await status.edit_text(_err_text(uid, exc))
        except Exception as exc:
            log.exception("Unexpected generic error")
            await status.edit_text(i18n.t(uid, "unexpected"))
            await _alert_admin(message.bot, "generic_unexpected", f"Generic error: {exc}")
        finally:
            _release_user(uid)
        return

    if _needs_login(req) and insta_client is None:
        await message.answer(i18n.t(uid, "needs_login"))
        return

    # Instant path: resend from cache if we've uploaded this exact content before.
    key = _cache_key(req)
    if key:
        cached = cache.get(key)
        if cached and await _send_from_cache(message, cached):
            log.info("Cache hit for %s", key)
            return

    if not _acquire_user(uid):
        await message.answer(i18n.t(uid, "busy"))
        return
    status = await message.answer(i18n.t(uid, "downloading"))
    try:
        async with _download_sem:
            with temp_workdir() as workdir:
                files = await _dispatch_with_retry(req, workdir, status, uid)
                files = collect_media(workdir) or files
                sent = await _send_media(message, files, uid)
                if key and sent:
                    cache.put(key, sent)  # remember for instant future resends
        await status.delete()
    except (InstaAuthError,) as exc:
        log.warning("Auth error: %s", exc)
        await status.edit_text(i18n.t(uid, "auth_error"))
        await _alert_admin(message.bot, "ig_auth", f"Instagram auth/session problem:\n{exc}")
    except (DownloadError, InstaDownloadError) as exc:
        log.info("Download error: %s", exc)
        await status.edit_text(_err_text(uid, exc))
    except Exception as exc:  # never let one bad request kill the bot
        log.exception("Unexpected error")
        await status.edit_text(i18n.t(uid, "unexpected"))
        await _alert_admin(message.bot, "ig_unexpected", f"Unexpected error: {exc}")
    finally:
        _release_user(uid)


def _make_bot() -> Bot:
    kwargs = {"default": DefaultBotProperties(parse_mode=ParseMode.HTML)}
    if TELEGRAM_API_URL:
        # Route every request through our own Bot API server (2 GB uploads).
        from aiogram.client.session.aiohttp import AiohttpSession
        from aiogram.client.telegram import TelegramAPIServer

        server = TelegramAPIServer.from_base(TELEGRAM_API_URL, is_local=True)
        kwargs["session"] = AiohttpSession(api=server)
        log.info("Using local Bot API server at %s (2 GB uploads)", TELEGRAM_API_URL)
    return Bot(BOT_TOKEN, **kwargs)


async def main_polling() -> None:
    bot = _make_bot()
    log.info("Bot starting (long polling)...")
    if insta_client is None:
        log.warning("IG credentials not set — stories/highlights/pfp are disabled.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


async def _keep_alive(base_url: str, interval: int) -> None:
    """Ping our own public URL every `interval` seconds so Render's free tier
    doesn't spin the service down for inactivity. The request goes through Render's
    edge, so it counts as real traffic (a localhost ping would not)."""
    import aiohttp

    url = f"{base_url}/healthz"
    await asyncio.sleep(interval)  # let startup settle first
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    log.info("keep-alive ping -> %s", resp.status)
            except Exception as exc:
                log.info("keep-alive ping failed: %s", exc)
            await asyncio.sleep(interval)


def main_webhook() -> None:
    """Run as an aiohttp web server (for Render free Web Service)."""
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    base_url = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL", "")
    base_url = base_url.rstrip("/")
    port = int(os.getenv("PORT", "10000"))
    # A guessable default would let anyone POST fake updates; generate a strong
    # random secret when none is configured (stable for the life of the process).
    secret = os.getenv("WEBHOOK_SECRET", "").strip() or secrets.token_urlsafe(24)
    path = f"/webhook/{secret}"
    # With a local Bot API server the server itself delivers updates to the
    # webhook, so point it at our in-container app instead of the public edge.
    webhook_base = f"http://localhost:{port}" if TELEGRAM_API_URL else base_url
    webhook_url = f"{webhook_base}{path}"
    keepalive_on = os.getenv("KEEPALIVE", "1").strip() not in ("0", "false", "")
    keepalive_secs = int(os.getenv("KEEPALIVE_SECONDS", "300"))

    bot = _make_bot()

    async def on_startup(app: web.Application) -> None:
        await bot.set_webhook(
            webhook_url, secret_token=secret, drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
        log.info("Webhook set -> %s", webhook_url)
        if keepalive_on and base_url:
            asyncio.create_task(_keep_alive(base_url, keepalive_secs))
            log.info("Keep-alive enabled: pinging every %ss", keepalive_secs)
        if insta_client is None:
            log.warning("IG credentials not set — stories/highlights/pfp disabled.")

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", health)          # Render health check + keep-alive pings
    app.router.add_get("/healthz", health)
    # handle_in_background=True: process the update AFTER returning 200 to Telegram.
    # Downloads routinely take longer than Telegram's ~60s webhook timeout, so
    # processing inline would make Telegram re-deliver the update and we'd download
    # (and send) the same media two or three times.
    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=secret, handle_in_background=True
    ).register(app, path=path)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)

    log.info("Bot starting (webhook) on port %s", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    # Webhook mode when a public URL is provided (Render), else local polling.
    if os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL"):
        main_webhook()
    else:
        try:
            asyncio.run(main_polling())
        except (KeyboardInterrupt, SystemExit):
            log.info("Bot stopped.")
