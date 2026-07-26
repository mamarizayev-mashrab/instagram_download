FROM python:3.11-slim

# ffmpeg is required by yt-dlp to merge video+audio streams.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Deno is the JS runtime yt-dlp (+ yt-dlp-ejs) uses to solve YouTube's "n"
# challenge; without it YouTube returns no downloadable formats.
COPY --from=denoland/deno:bin-2.9.4 /deno /usr/local/bin/deno
ENV DENO_DIR=/tmp/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persisted instaloader session lives here; mount a volume to keep it across deploys.
ENV SESSION_DIR=/app/.sessions
RUN mkdir -p /app/.sessions

CMD ["python", "bot.py"]
