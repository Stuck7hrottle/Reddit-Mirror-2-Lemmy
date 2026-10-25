# Reddit ↔ Lemmy Mirror

A self-hosted bridge that **syncs posts and comments** between Reddit and Lemmy communities — automatically, bi-directionally, and with media mirroring.

---

### 🌍 Overview

This system mirrors:
- **Reddit → Lemmy:** posts, comments, images, galleries, and videos  
- **Lemmy → Reddit:** comments (humanized phrasing + media rehosting)  
- **Dashboard control:** live stats, logs, and Docker container management  

Built for self-hosted instances and community moderation, the mirror runs entirely via Docker containers and SQLite databases.

---

### 🧱 Architecture

| Component | Purpose | Default |
|------------|----------|----------|
| `reddit-mirror` | Mirrors Reddit → Lemmy posts | Enabled |
| `reddit-refresh` | Periodically re-runs mirror cycles | Enabled |
| `reddit_comment_sync` | Mirrors Reddit comments → Lemmy | Enabled |
| `lemmy_comment_sync` | Mirrors Lemmy comments → Reddit | DISABLED by Default |
| `mirror-dashboard` | FastAPI + HTMX dashboard for monitoring | Enabled |
| `data/` | Persistent storage for SQLite and caches | Enabled |

Each container communicates through shared SQLite databases (`jobs.db`, `bridge_cache.db`) located under `/opt/Reddit-Mirror-2-Lemmy/data`.

---

### ✨ Features

- ✅ Full **two-way** post & comment mirroring  
- ✅ **Media rehosting** (images → `/pictrs`, video labeling)  
- ✅ **Dashboard** with live metrics, charts, and Docker controls  
- ✅ **Job queue persistence** (via SQLite)  
- ✅ Automatic **token renewal** for Lemmy & Reddit  
- ✅ Built-in **rate limiting and backoff** handling  
- ✅ Configurable sync intervals and comment filters  
- ✅ Support for `.env` hot reloads (e.g., SUB_MAP updates)

---

## 📦 Requirements

- Docker and Docker Compose
- A Lemmy instance (self-hosted or federated)
- Two Lemmy bot accounts (recommended):
  - One for posts (e.g. `mirrorbot`)
  - One for comments (e.g. `mirrorcomments`)
- 2 Reddit accounts with Reddit API credentials (from [Reddit App Console](https://www.reddit.com/prefs/apps))
  - One for Reddit to Lemmy - Choose script not web app when creating
  - One for Lemmy to Reddit - Choose script not web app when creating
---

### 📦 Installation

#### 1️⃣ Clone and prepare
```bash
git clone https://github.com/yourname/reddit-lemmy-mirror.git
cd reddit-lemmy-mirror
cp examples/.env .env
cp examples/docker-compose.yml docker-compose.yml
```

#### 2️⃣ Edit `.env`
Set up credentials and mappings:
```
LEMMY_URL=https://your.lemmy.instance
LEMMY_USER=botuser
LEMMY_PASS=botpass
REDDIT_CLIENT_ID=xxxx
REDDIT_CLIENT_SECRET=xxxx
REDDIT_USERNAME=redditbot
REDDIT_PASSWORD=secret
SUB_MAP=fosscad:fosscad,gundeals:gundeals
ENABLE_LEMMY_COMMENT_SYNC=true - THIS IS FALSE BY DEFAULT
```

#### 3️⃣ Start the stack
```bash
docker compose up -d
```

#### 4️⃣ Visit the Dashboard
The dashboard only listens on the server itself (`127.0.0.1:8080`) because it can
start and stop containers. Reach it one of two ways:

- **Through nginx with a login:** see `examples/nginx.conf`, then open
  `https://your-lemmy-domain/dashboard/`
- **Through an SSH tunnel:** run `ssh -L 8080:127.0.0.1:8080 you@your-server`,
  then open `http://localhost:8080/dashboard/`

Never publish port 8080 to the internet.

You’ll see:
- Live post/comment stats
- Container health (CPU/RAM)
- Start/stop/build controls
- Real-time logs

---

## 🔁 Refresh Cycles & Pagination

- The **refresh container** runs every 15 minutes by default (`REFRESH_INTERVAL=900`).  
- Each cycle checks all configured subreddits and mirrors new or edited content.  
- `POST_FETCH_LIMIT=all` enables full backfill with pagination — fetching thousands of posts safely.  
- The bridge pauses between batches to avoid Reddit API rate limits.

Example log:
```
🔁 Fetching subreddit: r/fosscad2
🪶 Found Reddit post abc123: New Frame Release
✨ Done — processed 145 posts from r/fosscad2.
```
---

### 🧠 Usage Notes

#### Mirror Cycles
- Default interval: **10 minutes**
- Controlled by `reddit-refresh` container
- Backfill and edit syncs enabled via `.env`

#### Dashboard API Endpoints
| Path | Description |
|------|--------------|
| `/dashboard/` | Main overview |
| `/dashboard/logs` | Live log page |
| `/dashboard/ws/logs` | WebSocket log stream |
| `/dashboard/health` | Docker container stats |
| `/dashboard/metrics` | JSON metrics API |
| `/dashboard/container/{name}/{action}` | Start/stop/restart a mirror worker (POST) |
| `/dashboard/container/{name}/build` | Rebuild and start a mirror worker (POST) |

Control endpoints only accept the four mirror worker containers and require the
`X-Requested-With: mirror-dashboard` header, which the dashboard's own buttons send.

---

### ⚙️ Configuration Reference

| Variable | Description | Default |
|-----------|--------------|----------|
| `LEMMY_URL` | Base Lemmy instance URL | required |
| `SUB_MAP` | `reddit_sub:lemmy_comm` mappings | example values |
| `ENABLE_LEMMY_COMMENT_SYNC` | Mirror Lemmy → Reddit comments | false |
| `REDDIT_COMMENT_SYNC_INTERVAL` | Reddit → Lemmy interval (sec) | 600 |
| `LEMMY_COMMENT_SYNC_INTERVAL` | Lemmy → Reddit interval (sec) | 600 |
| `DATA_DIR` | Data directory | `/opt/Reddit-Mirror-2-Lemmy/data` |
| `MIRROR_COMMENTS` | Enable comment mirroring | true |
| `MAX_POSTS_PER_RUN` | Limit per cycle | 5 |
| `POST_FETCH_LIMIT` | Post fetch limit | `all` |
| `REDDIT_BOT_USERNAME` | Prevents self-loop comments | optional |

---

### 🧩 Development

To run components manually:
```bash
python3 background_worker.py
python3 mirror_worker.py
python3 lemmy_comment_sync.py
python3 reddit_comment_sync.py
python3 dashboard/main.py  # dashboard
```

---

### 🪄 Legacy Version

The legacy JSON On-Way bridge (pre–SQLite) is archived here:
🔗 **[`legacy-json` branch](https://github.com/Stuck7hrottle/Reddit-Mirror-2-Lemmy/tree/legacy-json)**

The legacy SQLite One-Way bridge is archived here:
🔗 **[`legacy-sqlite` branch](https://github.com/Stuck7hrottle/Reddit-Mirror-2-Lemmy/tree/legacy-sqlite)**

---

### 🧾 License

MIT © 2025 — Developed by FOSSCAD contributors and the open-source community.
