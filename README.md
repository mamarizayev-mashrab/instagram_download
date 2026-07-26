# 📥 Instagram Downloader Telegram Bot

A Telegram bot (Python + aiogram 3.x) that downloads Instagram **reels, videos, posts,
carousels, stories, highlights and HD profile pictures** and sends them straight into chat.

## ✨ Features

| You send | Bot returns |
|---|---|
| `https://instagram.com/reel/XXXX/` | the reel video |
| `https://instagram.com/p/XXXX/` | photo / video / album (carousel) |
| `https://instagram.com/tv/XXXX/` | the IGTV video |
| `@username stories` or a `/stories/` link | active stories |
| a highlights link | highlight items |
| `@username pfp` | HD profile picture |

- Public reels/videos → **yt-dlp** (no login).
- Stories / highlights / profile pics / carousels → **instaloader** (uses the bot's login).
- Handles the 50 MB Telegram upload limit, private profiles, invalid links, and retries.

## 🧠 How it works

```
message → utils/url_parser.py (classify)
        → bot.py routes:
              public video  → downloaders/ytdlp_downloader.py  (yt-dlp)
              login-required → downloaders/insta_downloader.py  (instaloader session)
        → send back as video / photo / media-group
        → temp files auto-deleted (utils/files.py)
```

## 🛠️ Setup (run locally)

Requirements: **Python 3.11+** and **ffmpeg** installed.

```bash
# 1. Get the code, then:
pip install -r requirements.txt

# 2. Configure secrets
cp .env.example .env      # Windows: copy .env.example .env
# edit .env and fill in BOT_TOKEN (from @BotFather) and IG_USERNAME / IG_PASSWORD

# 3. Run
python bot.py
```

> **ffmpeg**: on Windows install from https://ffmpeg.org and add it to PATH.
> On Linux/Mac: `sudo apt install ffmpeg` or `brew install ffmpeg`.

## ⚠️ Instagram account warning

Stories, highlights and profile pictures require the bot to **log in to Instagram**.
**Use a secondary / burner account — never your main one.** Automated logins can get an
account rate-limited or temporarily restricted. The session is cached to `.sessions/` so the
bot doesn't re-login on every request (which reduces the risk of flagging).

If you only need **public reels/videos/posts**, you can leave `IG_USERNAME` / `IG_PASSWORD`
empty — those still work; only login-required features are disabled.

## ☁️ Deploy on free hosting

The bot uses **long polling**, so no public URL / webhook is needed — perfect for free tiers.

### Railway
1. Push this project to a GitHub repo.
2. On [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**.
3. Railway auto-detects the `Dockerfile`.
4. Go to **Variables** and add:
   - `BOT_TOKEN`
   - `IG_USERNAME`
   - `IG_PASSWORD`
5. (Optional) Add a **Volume** mounted at `/app/.sessions` so the Instagram session survives redeploys.
6. Deploy. Check the logs for `Bot starting (long polling)...`.

### Render
1. Push to GitHub.
2. On [render.com](https://render.com) → **New → Background Worker** (not a Web Service — no HTTP port needed).
3. Select the repo; Render uses the `Dockerfile`.
4. Add the same environment variables under **Environment**.
5. (Optional) Add a **Disk** mounted at `/app/.sessions`.
6. Create the worker and watch the logs.

## 🧩 Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from @BotFather |
| `IG_USERNAME` | for login features | Instagram (burner) username |
| `IG_PASSWORD` | for login features | Instagram password |
| `MAX_UPLOAD_MB` | optional | Max upload size (default 50, or 2000 when `TELEGRAM_API_URL` is set) |
| `SESSION_DIR` | optional | Where the IG session is cached, default `.sessions` |
| `TELEGRAM_API_URL` | optional | Self-hosted Bot API server URL for 2 GB uploads (e.g. `http://telegram-bot-api:8081`) |
| `TELEGRAM_API_ID` | for 2 GB | Telegram app id from my.telegram.org (used by the Bot API server) |
| `TELEGRAM_API_HASH` | for 2 GB | Telegram app hash from my.telegram.org |

## 📈 Raising the limit to 2 GB (self-hosted Bot API)

Telegram's public **Bot API** (`api.telegram.org`) caps bot uploads at **50 MB** — that
number cannot be changed. To send up to **2 GB**, run your **own** copy of the
[Bot API server](https://github.com/tdlib/telegram-bot-api) and point the bot at it via
`TELEGRAM_API_URL`. "Local" here means *your own server*, **not your laptop** — run it on an
always-on host (a small VPS works) so the bot keeps running when your laptop is off. Running
it on your laptop works too, but then the laptop must stay on 24/7.

The included **`docker-compose.yml`** does the whole thing (bot API server + bot, long-polling):

```bash
# 1. Get API credentials from https://my.telegram.org  ->  API development tools
#    (this gives you TELEGRAM_API_ID + TELEGRAM_API_HASH)

# 2. If the bot ever ran on the public API, log it out ONCE so the local server can take over:
curl "https://api.telegram.org/bot<BOT_TOKEN>/logOut"

# 3. Create a .env with BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH (+ IG_* if used)

# 4. Launch both containers
docker compose up -d --build
```

The bot auto-detects `TELEGRAM_API_URL` and bumps `MAX_UPLOAD_MB` to 2000.

> ⚠️ **Render free tier note:** a 2 GB pipeline (download → store → upload) needs real RAM and
> disk. The free tier (512 MB RAM, small ephemeral disk) reliably handles roughly a few hundred
> MB, not true 2 GB files. Use a VPS for large files.

## 📁 Project structure

```
.
├── bot.py                     # aiogram entrypoint + routing + sending
├── downloaders/
│   ├── ytdlp_downloader.py    # public reels/videos/igtv (yt-dlp)
│   └── insta_downloader.py    # stories/highlights/pfp/carousel (instaloader)
├── utils/
│   ├── url_parser.py          # classify the incoming link/command
│   └── files.py               # temp dirs, media collection, cleanup
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

## ⚖️ Disclaimer

For personal use. Respect Instagram's Terms of Service and other users' copyright.
Don't redistribute content you don't have rights to.
