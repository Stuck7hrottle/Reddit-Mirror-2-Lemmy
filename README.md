# Reddit → Lemmy Bridge

Mirror new posts (and comments) from one or more subreddits to matching Lemmy communities.
Works with Docker Compose and Lemmy-Ansible deployments.

## Features
- ✅ **TEST_MODE toggle** (`TEST_MODE=true`) to run without hitting Reddit
- 🔐 Lemmy **token caching** with cautious re-login
- 🗺️ **Community map** auto-refresh every 6 hours and stored on disk
- 💬 **Comment mirroring** with adaptive rate-limit backoff
- 🧩 Multiple subreddit→community mappings via `SUB_MAP`
- 📝 Text posts embed the **Reddit permalink** and (for link posts) the **media URL**

## Quick Start

1. **Clone your repo** and copy these files:
   - `auto_mirror.py`
   - `Dockerfile` (see below)
   - `docker-compose.yml` (see below)
   - `.env` (see example block)

2. **Create `.env`** (example):
   ```env
   # Lemmy
   LEMMY_URL=https://your-lemmy.example.com
   LEMMY_USER=mirrorbot
   LEMMY_PASS=changeme

   # Mapping: subreddit:community (comma-separated)
   SUB_MAP=fosscad2:fosscad2,3d2a:3D2A,FOSSCADtoo:FOSSCADtoo

   # Reddit API (live mode only)
   REDDIT_CLIENT_ID=xxxx
   REDDIT_CLIENT_SECRET=xxxx
   REDDIT_USERNAME=xxxx
   REDDIT_PASSWORD=xxxx
   REDDIT_USER_AGENT=reddit-lemmy-bridge/1.0

   # Options
   DATA_DIR=/app/data
   REDDIT_LIMIT=10
   MAX_POSTS_PER_RUN=5
   MIRROR_COMMENTS=true
   COMMENT_LIMIT_TOTAL=500
   SLEEP_SECONDS=900

   # Testing
   TEST_MODE=false
   ```

3. **Dockerfile** (example):
   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY . /app
   RUN pip install --no-cache-dir praw requests python-dotenv
   ENV PYTHONUNBUFFERED=1
   CMD ["python", "-u", "auto_mirror.py"]
   ```

4. **docker-compose.yml** (example):
   ```yaml
   services:
     reddit-lemmy-bridge:
       container_name: reddit-lemmy-bridge
       build: .
       restart: unless-stopped
       env_file: .env
       volumes:
         - .:/app
       # If your Lemmy is on another compose project, attach to its network:
       # networks:
       #   - lemmy_net

   # networks:
   #   lemmy_net:
   #     external: true
   #     name: your_lemmy_compose_default
   ```

5. **Run**
   ```bash
   docker compose up -d --build reddit-lemmy-bridge
   docker compose logs -f reddit-lemmy-bridge
   ```

## Test Mode
Set `TEST_MODE=true` in `.env` to avoid calling Reddit. The bridge will post
a single deterministic test item to each mapped community so you can verify
login, community resolution, and posting flow without credentials.

## Rate Limits
Comment posting uses adaptive backoff. If Lemmy returns a rate-limit error,
the bridge sleeps a few seconds and retries with a gradually increasing delay.

## Community Map
The bridge fetches `/api/v3/community/list` and persists a lowercase name→id
map to `DATA_DIR/community_map.json`. It auto-refreshes every 6 hours or on demand
if the file is missing/corrupt.

## Notes
- `SUB_MAP` parsing is strict: each entry must be `subreddit:community`.
- For link posts, the bridge prefers a **text post** with the media URL embedded,
  to preserve context and formatting.
- Token caching lives in `DATA_DIR/token.json`. The bridge only re-logins when it
  encounters a 401 or needs to rotate.

See `maintenance.md` for admin tasks.
