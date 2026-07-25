# Prompt: Build an Instagram Media Downloader Telegram Bot

> Copy everything below the line into your AI code generator (Claude, ChatGPT, Cursor, etc.).
> It is self-contained and specifies the exact language, architecture, and algorithm to use.

---

## 1. Role & Goal

You are a senior Python backend engineer. Build a **production-ready, well-structured Telegram
bot** that downloads media from Instagram and sends it back to the user inside Telegram.

The bot must handle **all** of the following Instagram content types:
- Reels and feed videos
- IGTV / long videos
- Single-photo and **carousel (multi-media) posts**
- **Stories** (of any public/followed user)
- **Highlights**
- **HD profile picture**

The user simply pastes an Instagram URL (or a username for profile pic/stories) and the bot
replies with the downloaded file(s).

## 2. Mandatory Tech Stack

Use exactly this stack — it is the strongest, most maintainable choice for this task:

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | Richest ecosystem for both Instagram scraping and Telegram bots |
| Telegram framework | **aiogram 3.x** | Modern, fully async, first-class support for media groups & large files |
| Public video/reel download | **yt-dlp** | Most reliable, actively maintained, no login needed for public content |
| Login-required content | **instaloader** | Handles Stories, Highlights, HD profile pics, carousels via a logged-in session |
| HTTP | **aiohttp** | Async downloads of media files |
| Config/secrets | **python-dotenv** | Load tokens & credentials from `.env` |

Do **not** use `python-telegram-bot`, Selenium, or any headless browser — keep it lightweight.

## 3. Functional Requirements

Commands:
- `/start` — welcome message explaining what the bot does and how to use it.
- `/help` — list supported link types with examples.
- Any Instagram URL sent as a plain message → auto-detect type and download.
- A message like `@username stories` or `@username pfp` → download that user's stories / HD profile picture.

Behavior:
- Reply with a "⏳ Downloading..." status message, then edit/replace it with the result.
- Send videos as **video**, photos as **photo**, and carousels as a **media group (album)**.
- Give clear, friendly error messages (invalid link, private profile, content unavailable, rate-limited).

## 4. Core Algorithm (implement step-by-step)

```
1. Receive incoming message text.
2. Parse & classify with regex:
     - /reel/ or /reels/       -> REEL   (public → yt-dlp)
     - /p/                      -> POST   (may be video/photo/carousel)
     - /tv/                     -> IGTV   (public → yt-dlp)
     - /stories/                -> STORY  (login → instaloader)
     - highlight link / id      -> HIGHLIGHT (login → instaloader)
     - "@username pfp"          -> PROFILE_PIC (instaloader)
     - "@username stories"      -> STORY by user (instaloader)
     - else                     -> reject with helpful message
3. Route to the correct downloader module:
     - Public video content  -> yt-dlp downloader
     - Login-required content -> instaloader downloader (uses a shared logged-in session)
4. Download media into a per-request temp folder (use tempfile / uuid to avoid collisions).
5. Inspect the result:
     - single video  -> answer_video
     - single photo  -> answer_photo
     - multiple items -> answer_media_group (album)
6. On success: delete the temp files (cleanup in a finally block).
7. On failure: log the exception and reply with a user-friendly error.
```

## 5. Authentication (for Stories / Highlights / private-ish content)

- Log in with **instaloader** using credentials from `.env`.
- On first run, create and **persist an instaloader session file** so you don't log in on every request
  (re-login on every call gets the account flagged). Reuse the session; only re-login if it expires.
- Never hardcode credentials. Read from environment variables only.
- Add clear code comments warning: use a secondary/burner Instagram account, not a primary one.

## 6. Constraints & Edge Cases (handle all)

- **Telegram Bot API 50 MB upload limit**: if a downloaded file exceeds it, either
  (a) send the direct media URL as a fallback, or (b) inform the user the file is too large.
  Add a clearly-marked optional note in the README about using a **local Bot API server** to raise the limit.
- **Rate limiting / anti-bot**: wrap network calls with retry + exponential backoff and timeouts.
- **Private / unavailable content**: catch and return a clear message.
- **Invalid or non-Instagram URL**: reject gracefully.
- **Concurrency**: multiple users at once must not overwrite each other's temp files (use unique paths).

## 7. Project Structure

```
instagram-bot/
├── bot.py                  # aiogram entrypoint, handlers, routing
├── downloaders/
│   ├── __init__.py
│   ├── ytdlp_downloader.py   # public reels/videos/igtv via yt-dlp
│   └── insta_downloader.py   # stories/highlights/pfp/carousel via instaloader (session-based)
├── utils/
│   ├── __init__.py
│   ├── url_parser.py         # regex classification of the incoming link
│   └── files.py              # temp-dir management & cleanup
├── .env.example
├── requirements.txt
├── Dockerfile
└── README.md
```

## 8. Deployment (Free Hosting)

Target **Railway** or **Render** (free tier):
- Use **long polling** (not webhooks) — simplest and works out of the box on free hosting.
- Provide a `Dockerfile` (python:3.11-slim, install ffmpeg for yt-dlp muxing).
- Provide a `requirements.txt` with pinned versions.
- Provide `.env.example`:
  ```
  BOT_TOKEN=your_telegram_bot_token
  IG_USERNAME=your_secondary_instagram_username
  IG_PASSWORD=your_instagram_password
  ```
- README must include exact step-by-step deploy instructions for Railway/Render
  (connect repo → set the env vars above → deploy) and how to run locally with `python bot.py`.

## 9. Deliverables

Produce **complete, runnable code** for every file above, plus:
- `requirements.txt` with pinned versions
- `.env.example`
- `Dockerfile`
- `README.md` (setup, run locally, deploy to Railway/Render, usage examples, the burner-account warning)

## 10. Quality Bar

- Fully **async** (aiogram 3.x handlers, non-blocking downloads).
- Robust error handling: `try/except` around every network/download call; never crash the bot on one bad request.
- Use the `logging` module (info + error levels).
- Clean, modular, commented code — each downloader isolated in its own module.
- No secrets in code. No blocking `time.sleep` in async paths (use `asyncio.sleep`).

---

### Reminder for the AI
Return the full source of every file. Do not leave `TODO`s or placeholder functions —
the bot must run end-to-end after `pip install -r requirements.txt` and setting the `.env` values.
