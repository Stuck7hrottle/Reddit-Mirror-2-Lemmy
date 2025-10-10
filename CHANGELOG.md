# 🧾 Reddit → Lemmy Bridge — Changelog

---

## [v1.1.0] — 2025-10-10
### 🚀 Major Update: SQLite Cache & Docker Modernization

#### 🗃️ New Features
- **SQLite Backend Cache**  
  - Added `db_cache.py` providing persistent caching for mirrored posts and comments.  
  - Prevents duplicate posting and enables “resume after restart” behavior.  
  - Automatically migrates legacy `post_map.json` and `comment_map.json` data on startup.  
  - Stores mappings in `/app/data/bridge_cache.db`.

- **Automatic JSON Migration (Backward Compatible)**  
  - Reads existing JSON map files at startup and imports them into SQLite.  
  - Keeps legacy JSONs writable for backward compatibility with the main branch.  
  - Logs detailed migration summaries during startup:
    ```
    📦 Migration complete: imported=87, skipped=12 (already cached)
    ```

- **Enhanced Logging & Diagnostics**  
  - Added structured startup logging, cache import summaries, and skip notices.  
  - Improved visibility for post/comment skipping due to existing cache entries.

#### 🐳 Docker Modernization
- Upgraded base image from `python:3.11-slim` → `python:3.12-slim`.  
- Introduced **non-root user** execution (`bridgeuser`) for improved security.  
- Added `DATA_DIR` and `PYTHONUNBUFFERED` environment variables for clean runtime consistency.  
- Ensured `/app/data` directory is auto-created at build time for cache + tokens.

#### 🧩 Docker Compose Enhancements
- Added explicit `DATA_DIR=/app/data` environment variable.  
- Mounted `/app/data` volume across all services for persistent caching.  
- Updated service definitions to use `python3` for full compatibility with Python 3.12.  
- Example Compose file (`docker-compose.example.yml`) now matches production layout.  
- Added `user: "1000:1000"` to run containers safely as non-root.

#### 🧰 Configuration Updates
- Updated `.env.example` with new fields:
  - `DATA_DIR`, `SQLITE_DB_NAME`, and `LOG_LEVEL`
  - Clarified Reddit + Lemmy credential usage
- Added inline documentation for future dashboard support (`DASHBOARD_PORT` placeholder).

#### ⚙️ Technical Improvements
- Simplified data flow and lock handling in SQLite backend.
- Introduced schema auto-initialization and thread-safe connection handling.
- Replaced JSON file writes with database inserts for posts/comments.
- Retained full backward compatibility with existing deployment setups.

#### 🧪 Internal Notes
- Compatible with existing Lemmy-Ansible deployments.
- Tested against Python 3.12.3 (Debian Bookworm base image).
- Verified migration path from `v1.0.0` without data loss.

---

## [v1.0.0] — Initial Stable Release
- Added Reddit → Lemmy bridge (`auto_mirror.py`).
- Added comment mirroring support with JWT refresh and rate-limit handling.
- Added Docker Compose integration.
- Initial `.env.example`, `README.md`, and `ROADMAP.md`.
