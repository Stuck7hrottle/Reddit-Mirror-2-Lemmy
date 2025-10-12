> **Note:** The legacy JSON-based bridge has been archived under the branch [`legacy-json`](https://github.com/Stuck7hrottle/Reddit-Mirror-2-Lemmy/tree/legacy-json).
> The current `master` branch uses the new SQLite background worker system.

# Reddit → Lemmy Bridge

A self-hosted Python system that mirrors Reddit communities, posts, and comments into matching Lemmy communities.

---

## 🚀 Features

- Mirrors Reddit → Lemmy posts, titles, selftext, images, galleries, and videos  
- Mirrors Reddit comments in chronological order  
- Supports periodic refresh cycles (via a separate container)  
- Fetches up to 100 posts per batch and supports full-history pagination (`POST_FETCH_LIMIT=all`)  
- Automatically skips duplicates using local SQLite caches  
- Preserves Reddit post permalinks and embeds galleries cleanly in Lemmy  
- Optional refresh/update mode to rebuild posts with new formatting or embeds  

---

## 🧩 Architecture

| Component | Description |
|------------|-------------|
| **reddit-mirror** | Worker service that mirrors posts and comments from Reddit to Lemmy |
| **reddit-refresh** | Scheduler container that periodically re-triggers mirroring cycles |
| **data/** | Local persistent directory containing SQLite databases (`jobs.db`, `bridge_cache.db`) and caches |

Each subreddit listed in the bridge’s configuration will map to a Lemmy community of the same name (e.g., `r/example` → `c/example`).

---

## ⚙️ Setup

### 1️⃣ Clone the Repository
```bash
git clone https://github.com/yourname/reddit-to-lemmy.git
cd reddit-to-lemmy
```

### 2️⃣ Copy Example Configuration
```bash
cp examples/.env.example .env
cp examples/docker-compose.example.yml docker-compose.yml
```

### 3️⃣ Edit `.env`
Set:
- `LEMMY_URL` → your Lemmy instance  
- `LEMMY_USER` / `LEMMY_PASS` → your bot credentials  
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` → your Reddit app credentials  
- `POST_FETCH_LIMIT` → number of posts per subreddit (`50`, `100`, or `all`)  

### 4️⃣ Run the Bridge
```bash
docker compose up -d
```

Monitor logs:
```bash
docker compose logs -f reddit-refresh
```

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

## 🧠 Advanced Usage

### Updating Existing Lemmy Posts
If you improve post embedding logic (e.g., gallery or video support), reprocess all mirrored posts:

```bash
docker compose run --rm reddit-mirror python3 auto_mirror.py --update-existing
```

### Manual Trigger
You can also force an immediate refresh cycle:
```bash
docker compose run --rm reddit-refresh python3 auto_mirror.py --refresh
```

---

## 🛠️ Maintenance

See [`docs/maintenance.md`](docs/maintenance.md) for:
- Cache resets  
- Database inspection (`sqlite3 data/jobs.db`)  
- Rebuilding containers  
- Troubleshooting comment syncs  

---

## 🧩 Example Folder

| File | Purpose |
|------|----------|
| `examples/.env.example` | Minimal environment file for setup |
| `examples/docker-compose.example.yml` | Example Docker Compose stack |
| `docs/maintenance.md` | Common admin operations and recovery steps |

---

## 🗺️ Roadmap

Planned enhancements:
- Direct video upload to Lemmy (instead of Reddit-hosted links)
- Edit tracking for mirrored posts/comments
- Multi-instance federation sync
- Web UI for queue and job status
- Optional moderation tools for mirrored content

---

## ⚖️ License

MIT License © 2025 — Open source and community maintained.

---

## 💬 Credits

Developed by the FOSSCAD team and contributors.  
Built for resilient, federated content archiving between Reddit and Lemmy.
