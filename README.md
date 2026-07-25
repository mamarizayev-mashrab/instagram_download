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
| `MAX_UPLOAD_MB` | optional | Max upload size, default 50 |
| `SESSION_DIR` | optional | Where the IG session is cached, default `.sessions` |

## 📈 Raising the 50 MB limit (optional)

Telegram's **Bot API** caps bot uploads at 50 MB. To send larger files, run a
[local Bot API server](https://github.com/tdlib/telegram-bot-api) and point the bot at it —
that raises the limit to ~2 GB. This is optional and only needed for very large videos.

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
