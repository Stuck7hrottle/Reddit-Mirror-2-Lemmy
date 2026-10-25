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

### 🎬 Media and Links

| Reddit post links to | What the Lemmy post gets |
|---|---|
| An image or gallery (i.redd.it, imgur, direct .jpg/.png/.gif) | Rehosted on your Lemmy and embedded |
| A Reddit video (v.redd.it) or a direct .mp4/.webm | Downloaded with sound, rehosted and embedded. Falls back to a link if your Lemmy rejects it (too long or too large) or the download fails |
| YouTube, Rumble, Odysee, Vimeo, Streamable, Twitch, Kick | Not downloaded. Used as the post's link, so Lemmy shows a thumbnail (and a player for YouTube) |
| Any other web page (news articles, stores...) | Used as the post's link; Lemmy fetches its title and thumbnail |
| Reddit itself (self posts, crossposts) | No link back to Reddit |

#### Allowing longer videos

Videos are stored by **pict-rs**, Lemmy's media server, which by default rejects
anything over **900 frames** (about 30 seconds at 30 fps) or **40 MB**, and stops
processing after **30 seconds**. Raise these in the `pictrs` service's
`environment:` in your **Lemmy** `docker-compose.yml`, then recreate pict-rs:

```yaml
      PICTRS__MEDIA__MAX_FILE_SIZE: "300"             # MB, all uploads
      PICTRS__MEDIA__VIDEO__MAX_FILE_SIZE: "300"      # MB, videos
      PICTRS__MEDIA__VIDEO__MAX_FRAME_COUNT: "36000"  # 20 min at 30 fps
      PICTRS__MEDIA__VIDEO__ALLOW_AUDIO: "true"       # keep sound
      PICTRS__MEDIA__PROCESS_TIMEOUT: "900"           # seconds to transcode
```

Every web server between the bridge and pict-rs must also accept the file size
(`client_max_body_size` in nginx). On the bridge side, keep
`MEDIA_UPLOAD_TIMEOUT_SECS` (default 600) longer than pict-rs takes to transcode,
or the bridge will give up and upload the same video again.

### 💾 Backups and Logs

`data/jobs.db` is the one file that can't be rebuilt: it records which Reddit
post or comment became which Lemmy one. Lose it and the mirror re-posts
everything as duplicates.

**Install nightly backups and log rotation** (run once, from the project folder):

```bash
sudo cp contrib/cron.d-reddit-mirror /etc/cron.d/reddit-mirror
sudo cp contrib/logrotate.conf /etc/logrotate.d/reddit-mirror
sudo contrib/backup.sh          # make the first backup now
```

- Backups go to `backups/mirror-YYYY-MM-DD.tar.gz` (database, media cache and
  `.env`), readable by root only, kept for 14 days (`KEEP_DAYS`).
- `logs/bridge.log` rotates daily (or at 100 MB) and keeps 14 days;
  `logs/errors.log` rotates weekly.
- If the project isn't in `/opt/Reddit-Mirror-2-Lemmy`, edit the paths in both files.
- Backups live on the same disk as the mirror. They protect against corruption
  and mistakes, not a lost server, so copy them somewhere else now and then.

**Restore a backup:**

```bash
docker compose stop reddit-mirror reddit-refresh reddit_comment_sync lemmy_comment_sync
mkdir -p /tmp/restore && tar -xzf backups/mirror-YYYY-MM-DD.tar.gz -C /tmp/restore
mv data/jobs.db data/jobs.db.before-restore
cp /tmp/restore/jobs.db data/jobs.db
docker compose start reddit-mirror reddit-refresh reddit_comment_sync lemmy_comment_sync
```

### 🛠️ Repairing Old Posts

Older versions dropped the link from link posts (news articles etc.), and long
Reddit videos fell back to a link. `repair_posts.py` fixes just those posts:
it checks each against the original on Reddit (25 per request) and edits the
Lemmy post **only** when there's something to add. Genuine title-only posts,
and videos that still don't fit, are left alone.

**1. List the candidates** from Lemmy's database (read-only). Replace
`lemmy-postgres-1` with your Postgres container and `mirrorbot` with your bot's username:

```bash
docker exec -i lemmy-postgres-1 psql -U lemmy -d lemmy -At -F, -c "
SELECT p.id,
       CASE WHEN p.body LIKE '%[Video](https://v.redd.it/%' THEN 'video_link' ELSE 'title_only' END
FROM post p JOIN person u ON u.id = p.creator_id
WHERE u.name = 'mirrorbot' AND u.local AND NOT p.deleted AND NOT p.removed
  AND ((p.url IS NULL AND coalesce(p.body,'') = '') OR p.body LIKE '%[Video](https://v.redd.it/%')
ORDER BY p.id" > data/repair_candidates.csv
```

**2. See what would change** (edits nothing):

```bash
docker exec reddit_mirror_worker python3 repair_posts.py --dry-run
```

**3. Run it** in the background; progress shows on the dashboard's Logs page:

```bash
docker exec -d reddit_mirror_worker python3 repair_posts.py
```

Progress is saved in `data/repair_progress.json`, so if it's interrupted, run
step 3 again and it continues where it stopped. `--limit 20` stops after 20
edits, handy for a first look.

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
