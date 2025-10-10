# 🌉 Reddit → Lemmy Bridge
**Mirror Reddit posts and comments to Lemmy communities automatically**

![Python](https://img.shields.io/badge/python-3.12-blue)
![Docker](https://img.shields.io/badge/docker-ready-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 🧭 Overview
The **Reddit → Lemmy Bridge** syncs new Reddit posts and their comment threads to corresponding Lemmy communities.

Originally JSON-based, it now supports a **persistent SQLite backend**, offering durability, restart-safe caching, and faster duplicate detection.

---

## ✨ Features
- 🔄 **Automated Reddit → Lemmy post mirroring**
- 💬 **Comment mirroring** with parent threading
- 🔑 **JWT refresh** and rate-limit handling
- 🗃️ **SQLite caching** for reliable, resumable syncs
- 🐳 **Docker-ready**, clean `.env`-based configuration
- 🧩 **Backwards compatible** with legacy `post_map.json` and `comment_map.json`
- 🔍 Verbose structured logging with timestamps
- 🧱 Modular design for extending to new platforms or dashboards

---

## 🧰 Requirements
- **Python 3.12+**
- A **Lemmy account** with post/comment permissions
- A **Reddit script app** (Client ID & Secret)
- Docker (optional but recommended)

---

## ⚙️ Installation

### 🐳 Option 1: Docker Compose (Recommended)
```bash
git clone https://github.com/yourname/reddit-lemmy-bridge.git
cd reddit-lemmy-bridge
cp .env.example .env
cp docker-compose.example.yml docker-compose.yml
docker compose build --no-cache
docker compose up -d
```

**Persistent Data:**  
The folder `./data` on your host maps to `/app/data` inside containers and stores:
- `token.json` — JWTs and auth state
- `bridge_cache.db` — SQLite cache (new in v1.1)
- Legacy backups (`post_map.json`, `comment_map.json`)

---

### 🧩 Option 2: Manual (Development Mode)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 auto_mirror.py
```

---

## 🔐 Environment Variables

All configuration lives in `.env`.  
See the example file: [`.env.example`](./.env.example)

| Variable | Description |
|-----------|--------------|
| `LEMMY_URL` | Base URL of your Lemmy instance |
| `LEMMY_USER`, `LEMMY_PASS` | Lemmy account for posts |
| `LEMMY_USER_COMMENTS`, `LEMMY_PASS_COMMENTS` | Secondary Lemmy account for comments (optional) |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Reddit API credentials |
| `REDDIT_USER`, `REDDIT_PASS` | Reddit login for mirroring |
| `REDDIT_SUBS` | Comma-separated list of subreddits to mirror |
| `REDDIT_LIMIT` | Number of posts per cycle |
| `DATA_DIR` | Path for runtime data (`/app/data` default) |
| `SQLITE_DB_NAME` | Optional database filename override |
| `SLEEP_BETWEEN_CYCLES` | Time between mirror runs (seconds) |
| `LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARN`) |

---

## 🐳 Docker Services

| Service | Purpose |
|----------|----------|
| `reddit-lemmy-bridge` | Mirrors Reddit posts to Lemmy |
| `reddit-comment-mirror` | Mirrors Reddit comments to Lemmy |
| `reddit-edit-sync` | Handles Reddit edit synchronization |
| `/app/data` | Persistent cache & tokens shared between containers |

Each service reads `.env` and uses `DATA_DIR` for its local cache.

---

## 🧱 Data Persistence

The bridge now stores mappings and cache data in SQLite:

**Database:** `/app/data/bridge_cache.db`  
**Tables:**
- `posts` — Reddit ↔ Lemmy post mappings  
- `comments` — Reddit ↔ Lemmy comment mappings  

Legacy files (`post_map.json`, `comment_map.json`) are still read once and migrated automatically on startup.  
They remain untouched for rollback compatibility.

---

## 🔄 Upgrading from v1.0.0 → v1.1.0

> _This section summarizes the upgrade guide for existing users._

1. **Pull latest release**  
   ```bash
   git pull origin main
   ```

2. **Add SQLite support**  
   Update `Dockerfile` to Python 3.12 (see example in repo).

3. **Add DATA_DIR**  
   Add this to your `.env`:
   ```dotenv
   DATA_DIR=/app/data
   ```

4. **Rebuild containers**  
   ```bash
   docker compose build --no-cache
   docker compose up -d
   ```

5. **Automatic migration**  
   On first startup:
   ```
   📂 Found legacy post_map.json — migrating...
   📦 Migration complete: imported=87, skipped=12
   ```
   Your old JSONs stay as backups.

6. **Verify migration**  
   ```bash
   docker exec -it reddit-lemmy-bridge sqlite3 /app/data/bridge_cache.db ".tables"
   ```

---

## 🧩 Troubleshooting

| Issue | Fix |
|--------|-----|
| `401 {"error":"incorrect_login"}` | Check Lemmy credentials in `.env` |
| `ModuleNotFoundError: No module named 'praw'` | Rebuild Docker image (installs all deps) |
| `DeprecationWarning: datetime.utcnow()` | Safe to ignore; fixed in next patch |
| SQLite file missing | Ensure `DATA_DIR` volume (`./data:/app/data`) is mounted properly |
| Old JSON not migrating | Confirm JSON is in `/app/data` or relative to working dir |

---

## 🧪 Development Notes
- Run directly with `python3 auto_mirror.py` for debugging.
- Use `LOG_LEVEL=DEBUG` for verbose trace logs.
- `db_cache.py` can be imported standalone for CLI inspection:
  ```bash
  python3 db_cache.py
  ```

---

## 🛠️ Roadmap

| Milestone | Description | Status |
|------------|--------------|---------|
| v1.1.0 | SQLite cache, Docker modernization | ✅ Released |
| v1.2.0 | Health monitor dashboard (Flask/FastAPI) | 🧩 In design |
| v1.3.0 | Async job queue for per-subreddit mirroring | 🧠 Planned |
| v1.4.0 | Web dashboard + metrics exporter | 🚧 Planned |

---

## 🤝 Contributing
PRs welcome!  
If you add new integrations or improve cache logic, please include tests or migration notes.  
Run `black` and `flake8` before committing for style consistency.

---

## 📜 License
MIT License © 2025 — Patrick Kelley  
See [`LICENSE`](./LICENSE) for full terms.
