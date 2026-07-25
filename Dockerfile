FROM python:3.11-slim

# ffmpeg is required by yt-dlp to merge video+audio streams.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persisted instaloader session lives here; mount a volume to keep it across deploys.
ENV SESSION_DIR=/app/.sessions
RUN mkdir -p /app/.sessions

CMD ["python", "bot.py"]
