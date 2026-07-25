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
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from dotenv import load_dotenv

from downloaders import insta_downloader, ytdlp_downloader
from downloaders.insta_downloader import InstaAuthError, InstaClient, InstaDownloadError
from downloaders.ytdlp_downloader import DownloadError
from utils import cache
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
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "50"))
FFMPEG_LOCATION = os.getenv("FFMPEG_LOCATION", "").strip() or None

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

# instaloader client is optional. It works with EITHER an imported session file
# (see setup_session.py) or a username+password. Enable it whenever a username is set.
insta_client: InstaClient | None = (
    InstaClient(IG_USERNAME, IG_PASSWORD) if IG_USERNAME else None
)

dp = Dispatcher()

WELCOME = (
    "👋 <b>Instagram Downloader Bot</b>\n\n"
    "Send me an Instagram link and I'll fetch the media for you.\n\n"
    "<b>Supported:</b>\n"
    "• Reels & feed videos\n"
    "• Posts (photo / video / carousel)\n"
    "• IGTV\n"
    "• Stories — <code>@username stories</code> or a /stories/ link\n"
    "• Highlights\n"
    "• HD profile picture — <code>@username pfp</code>\n\n"
    "Just paste a link to begin. Type /help for examples."
)

HELP = (
    "<b>How to use</b>\n\n"
    "1️⃣ Public video/reel:\n<code>https://www.instagram.com/reel/XXXX/</code>\n\n"
    "2️⃣ Post (photo/carousel):\n<code>https://www.instagram.com/p/XXXX/</code>\n\n"
    "3️⃣ Stories:\n<code>@username stories</code>\n\n"
    "4️⃣ HD profile picture:\n<code>@username pfp</code>\n\n"
    "5️⃣ Highlights:\n<code>@username highlights</code>\n\n"
    "ℹ️ Stories, highlights and profile pictures need the bot's Instagram login "
    "to be configured by the owner."
)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(WELCOME)


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


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


async def _dispatch_with_retry(req: ParsedRequest, workdir, status: Message) -> list:
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
                    await status.edit_text(f"⏳ Instagram is busy — retrying in {wait}s...")
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


async def _fit_limit(message: Message, path):
    """Return a path that fits under the limit, compressing an oversized video if needed."""
    if size_mb(path) <= MAX_UPLOAD_MB:
        return path
    if not is_video(path):
        return None  # can't shrink a photo meaningfully
    await message.answer("📦 Video is large — compressing to fit Telegram's limit...")
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


async def _send_media(message: Message, files: list) -> list[dict]:
    """Send media, return [{'kind','file_id'}] for caching. Compress oversized videos."""
    sendable = []
    too_big = 0
    for f in files:
        fitted = await _fit_limit(message, f)
        if fitted is not None:
            sendable.append(fitted)
        else:
            too_big += 1

    if not sendable:
        await message.answer(
            "⚠️ The media is larger than "
            f"{MAX_UPLOAD_MB:.0f} MB even after compression — Telegram bots can't upload it.\n"
            "Tip: the owner can run a local Bot API server to raise this limit to ~2 GB."
        )
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
        await message.answer(
            f"⚠️ Skipped {too_big} file(s) still over {MAX_UPLOAD_MB:.0f} MB after compression."
        )
    return sent_ids


async def _send_from_cache(message: Message, items: list[dict]) -> bool:
    """Resend previously uploaded media by file_id. Returns False if it fails."""
    try:
        if len(items) == 1:
            it = items[0]
            if it["kind"] == "video":
                await message.answer_video(it["file_id"])
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
    req = parse(message.text)

    if req.content_type == ContentType.UNKNOWN:
        await message.answer(
            "🤔 I couldn't recognize that. Send an Instagram link, "
            "or <code>@username stories</code> / <code>@username pfp</code>.\nType /help for examples."
        )
        return

    if _needs_login(req) and insta_client is None:
        await message.answer(
            "🔒 Stories, highlights and profile pictures need the bot's Instagram login, "
            "which the owner hasn't configured yet."
        )
        return

    # Instant path: resend from cache if we've uploaded this exact content before.
    key = _cache_key(req)
    if key:
        cached = cache.get(key)
        if cached and await _send_from_cache(message, cached):
            log.info("Cache hit for %s", key)
            return

    status = await message.answer("⏳ Downloading...")
    try:
        with temp_workdir() as workdir:
            files = await _dispatch_with_retry(req, workdir, status)
            files = collect_media(workdir) or files
            sent = await _send_media(message, files)
            if key and sent:
                cache.put(key, sent)  # remember for instant future resends
        await status.delete()
    except (InstaAuthError,) as exc:
        log.warning("Auth error: %s", exc)
        await status.edit_text("🔑 Instagram login problem. The owner should re-check credentials.")
    except (DownloadError, InstaDownloadError) as exc:
        log.info("Download error: %s", exc)
        await status.edit_text(f"❌ Couldn't download that.\n<i>{exc}</i>")
    except Exception as exc:  # never let one bad request kill the bot
        log.exception("Unexpected error")
        await status.edit_text("💥 Something went wrong. Please try again later.")


def _make_bot() -> Bot:
    return Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


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
    webhook_url = f"{base_url}{path}"
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
