"""Instagram media downloader Telegram bot (aiogram 3.x).

Flow:
    user message → classify URL/command → route to the right downloader
    → send media back (video / photo / album) → clean up temp files.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from dotenv import load_dotenv

from downloaders import insta_downloader, ytdlp_downloader
from downloaders.insta_downloader import InstaAuthError, InstaClient, InstaDownloadError
from downloaders.ytdlp_downloader import DownloadError
from utils import cache
from utils import i18n
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
# Telegram's public API. See start.sh / README for how to run the server.
TELEGRAM_API_URL = os.getenv("TELEGRAM_API_URL", "").strip()
# When a local server is configured the ceiling is 2 GB; otherwise 50 MB.
_default_limit = "2000" if TELEGRAM_API_URL else "50"
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", _default_limit))

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

# instaloader client is optional. It works with EITHER an imported session file
# (see setup_session.py) or a username+password. Enable it whenever a username is set.
insta_client: InstaClient | None = (
    InstaClient(IG_USERNAME, IG_PASSWORD) if IG_USERNAME else None
)

dp = Dispatcher()


def _lang_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard with the four language options."""
    buttons = [
        InlineKeyboardButton(text=name, callback_data=f"setlang:{code}")
        for code, name in i18n.LANG_NAMES.items()
    ]
    # Two per row.
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _yt_keyboard(vid: str) -> InlineKeyboardMarkup:
    """Quality picker shown for a YouTube link. The video id rides in callback_data
    so the handler stays stateless (no per-user pending store needed)."""
    rows = [
        [
            InlineKeyboardButton(text="🎬 360p", callback_data=f"yt:360:{vid}"),
            InlineKeyboardButton(text="🎬 720p", callback_data=f"yt:720:{vid}"),
        ],
        [
            InlineKeyboardButton(text="🎬 1080p", callback_data=f"yt:1080:{vid}"),
            InlineKeyboardButton(text="🎵 MP3", callback_data=f"yt:audio:{vid}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id
    await message.answer(i18n.t(uid, "welcome"))
    # First-time users: also show the language picker.
    if not i18n.has_lang(uid):
        await message.answer(i18n.t(uid, "lang_choose"), reply_markup=_lang_keyboard())


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(i18n.t(message.from_user.id, "help"))


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

    status = await call.message.answer(i18n.t(uid, "downloading"))
    try:
        with temp_workdir() as workdir:
            files = await ytdlp_downloader.download_youtube(
                url, workdir, quality, MAX_UPLOAD_MB
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
        await status.edit_text(i18n.t(uid, "download_error", err=exc))
    except Exception:
        log.exception("Unexpected YouTube error")
        await status.edit_text(i18n.t(uid, "unexpected"))


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


def _looks_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in ("wait a few minutes", "429", "rate", "too many", "please wait"))


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
                else:
                    media.append(InputMediaPhoto(media=it["file_id"]))
            await message.answer_media_group(media)
        return True
    except Exception as exc:  # stale file_id → fall back to a fresh download
        log.info("Cache resend failed (%s); will re-download.", exc)
        return False


@dp.message(F.text)
async def handle_link(message: Message) -> None:
    uid = message.from_user.id
    req = parse(message.text)

    if req.content_type == ContentType.UNKNOWN:
        await message.answer(i18n.t(uid, "unknown"))
        return

    # YouTube → let the user pick a quality (or audio) before we download.
    if req.content_type == ContentType.YOUTUBE:
        await message.answer(
            i18n.t(uid, "yt_choose"), reply_markup=_yt_keyboard(req.shortcode)
        )
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

    status = await message.answer(i18n.t(uid, "downloading"))
    try:
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
    except (DownloadError, InstaDownloadError) as exc:
        log.info("Download error: %s", exc)
        await status.edit_text(i18n.t(uid, "download_error", err=exc))
    except Exception as exc:  # never let one bad request kill the bot
        log.exception("Unexpected error")
        await status.edit_text(i18n.t(uid, "unexpected"))


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
    secret = os.getenv("WEBHOOK_SECRET", "").strip() or "igbot-secret"
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
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path=path)
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
