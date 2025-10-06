# 🗺️ Reddit → Lemmy Bridge Roadmap

A forward-looking guide to planned and potential upgrades for the Reddit → Lemmy Bridge system.

---

## 🚀 Phase 2 — Core Upgrades

### 🖼️ Direct Pictrs Uploads
- [ ] Upload Reddit-hosted images, GIFs, and videos directly to Lemmy’s Pictrs endpoint.
- [ ] Replace external media links with local Lemmy media URLs.
- [ ] Add fallback logic if Pictrs upload fails.

### 💾 SQLite Cache Backend
- [ ] Replace JSON mapping files with a small SQLite database (`data/bridge.db`).
- [ ] Store Reddit↔Lemmy mappings, tokens, and sync state.
- [ ] Add CLI tools to query mappings and resync posts.

### 🧠 Rate Limit Handling
- [ ] Parse `X-Ratelimit-Used` and `X-Ratelimit-Remaining` headers from Reddit API.
- [ ] Automatically throttle requests when approaching API limits.
- [ ] Add exponential backoff for 429 responses.

---

## 📊 Phase 3 — Monitoring & Observability

### 📈 Local Status API
- [ ] Add `/status` endpoint to expose current sync stats.
  ```json
  {
    "posts_mirrored": 142,
    "comments_mirrored": 5410,
    "last_run": "2025-10-07T03:05:00Z"
  }
  ```

### 🔍 Prometheus Metrics
- [ ] Expose metrics for posts, comments, and errors.
  - `reddit_bridge_posts_total`
  - `reddit_bridge_comments_total`
  - `reddit_bridge_errors_total`

### 🧩 Health Checks
- [ ] Add `/healthz` endpoint to verify Reddit and Lemmy connectivity.
- [ ] Integrate with Docker healthcheck for automatic restart on failure.

---

## 🧵 Phase 4 — Scalability & Architecture

### ⚙️ Parallel Fetching
- [ ] Use multithreading to fetch multiple subreddits concurrently.
- [ ] Post sequentially to Lemmy to avoid rate-limits.

### 🧭 Queue System (Optional)
- [ ] Introduce Redis or RabbitMQ for queued mirroring tasks.
- [ ] Decouple Reddit fetching and Lemmy posting into separate workers.

---

## 🌍 Phase 5 — Federation Expansion

### 🪴 Multi-Lemmy Mirroring
- [ ] Support multiple destination Lemmy instances in `.env`.
  ```env
  SUB_MAP=fosscad2:fosscad2@fosscad.guncaddesigns.com,fosscad2@lemmy.world
  ```

### 🔁 Cross-Instance Fallback
- [ ] Retry failed posts on backup Lemmy servers.

---

## 🧰 Phase 6 — Developer Tools

### 🧹 CLI Utilities
- [ ] Add management scripts for debugging and maintenance:
  - `tools/clear_cache.py`
  - `tools/test_login.py`
  - `tools/resync_post.py reddit_id`

### 🧪 CI/CD Integration
- [ ] Add GitHub Actions to lint Python code and build Docker images automatically.
- [ ] Push releases to Docker Hub or GHCR.

### 🧩 Local Testing
- [ ] Create `docker-compose.test.yml` with mock Reddit and Lemmy APIs for safe testing.

---

## 📡 Phase 7 — Notifications & QoL

### 📬 Webhook Alerts
- [ ] Send Telegram/Discord alerts on mirror failures or token expiration.
  ```
  ⚠️ Mirror Failure: “FGC-9 Receiver Drop”
  401 incorrect_login (will retry)
  ```

### 🕒 Scheduler Service
- [ ] Add a lightweight scheduler container to trigger mirrors periodically.
- [ ] Replace infinite `sleep` loops inside scripts.

---

## ❤️ Summary of Priorities

| Priority | Upgrade | Benefit |
|-----------|----------|----------|
| 🔥 High | Pictrs uploads | Makes Lemmy posts fully self-contained |
| 🧱 Medium | SQLite cache | Improves stability and persistence |
| 📊 Medium | Status API / Metrics | Adds visibility and monitoring |
| 🧩 Medium | CLI & CI/CD tools | Easier debugging and maintenance |
| 🌍 Low | Multi-Lemmy federation | Expands federation reach |

---

## 🏁 Future Vision
- [ ] Web-based dashboard for monitoring mirrored posts and logs.
- [ ] Native Lemmy plugin for receiving mirrored Reddit content via federation.
- [ ] Migration to async Python for ultra-fast multi-subreddit support.

---

> “The bridge works. Now let’s make it beautiful, bulletproof, and federated.” 🌍
