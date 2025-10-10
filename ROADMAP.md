# 🗺️ Project Roadmap — Reddit → Lemmy Bridge

This roadmap outlines the progressive development stages for the Reddit-to-Lemmy mirroring bridge.  
Each stage builds upon the previous one, focusing on long-term reliability, transparency, and ease of deployment.

---

## 🧩 Stage 1 — Stability & Core Features ✅ *(Completed)*

- **Token Management**
  - Cached login tokens for persistent sessions  
  - Automatic refresh and rate-limit cooldown logic  
- **Mapping Persistence**
  - Persistent post and community mapping (originally `post_map.json`, now migrated to SQLite)  
  - Automatic refresh every 6 hours or on demand  
- **Error Recovery**
  - Graceful token invalidation handling  
  - Retry logic with cooldowns to avoid duplicate-token errors  
- **Test Mode**
  - Toggle `TEST_MODE=true` in `.env` for dry-run verification  
  - Posts “Example mirrored post” instead of live Reddit content  
- **Case-Insensitive Resolution**
  - Community lookup normalized for consistent matching across instances  

---

## ⚙️ Stage 2 — UX, Diagnostics & Persistence *(Completed / In Progress)*

- **SQLite Backend Cache**
  - Replaces JSON maps for posts/comments with `bridge_cache.db`  
  - Automatic migration from legacy JSON files on startup  
  - Enables duplicate-prevention and resumable sync after restart  
- **Advanced Logging**
  - Structured, timestamped logs with per-cycle summaries  
  - Optional verbose mode via `LOG_LEVEL=DEBUG`  
- **Diagnostics**
  - Planned `/status` and `/health` endpoints for live container monitoring  
  - Integration hooks for lightweight dashboards (Stage 3)  
- **Improved Docker Integration**
  - Updated to Python 3.12 base image  
  - Uses `/app/data` for persistent runtime state and caching  
  - Non-root container execution for improved security  

---

## 🌐 Stage 3 — Health Dashboard & Queue System *(In Design)*

- **Web Dashboard**
  - Lightweight Flask/FastAPI interface for health, stats, and manual actions  
  - Displays:
    - Total mirrored posts/comments  
    - Lemmy rate-limit state  
    - JWT expiration countdown  
    - Queue backlog and errors  
- **Queue System**
  - Local retry queue for failed or deferred posts/comments  
  - Optional exponential backoff retry logic  
- **Metrics Export**
  - JSON or Prometheus-compatible `/metrics` endpoint for external monitoring  

---

## 📦 Stage 4 — Federation Awareness & Automation *(Planned)*

- **Remote Community Support**
  - Recognize and post to federated communities via `!community@domain`  
  - Handle propagation delays gracefully  
- **Scheduled Tasks**
  - Built-in job scheduling for periodic refresh and cleanup  
- **Maintenance CLI**
  - Add standalone management commands:
    - `--clean-db` → clear or vacuum SQLite safely  
    - `--refresh-map` → rebuild community mappings  
    - `--rebuild-tokens` → force JWT renewal  
- **Federation Logging**
  - Track posts routed through remote instances with detailed metrics  

---

## 🧠 Stage 5 — Smart Syncing & Analytics *(Future Goals)*

- **Performance**
  - Optimize multi-threaded or asyncio-based mirroring  
  - Adaptive pacing based on Lemmy instance load  
- **Analytics**
  - Track mirrored posts, errors, latency, and retries per cycle  
  - Export to Prometheus or Grafana for visualization  
- **Self-Healing**
  - Detect and retry broken states automatically  
  - Optional alerting via webhook or email  

---

## 💡 Long-Term Vision

To evolve the Reddit–Lemmy Bridge into a fully autonomous synchronization service that:
- Self-heals after errors or restarts  
- Adapts to rate limits dynamically  
- Provides live operational transparency  
- Offers optional UI/metrics layers  
- Serves as a general-purpose Lemmy integration framework  

---

## 🤝 Contributing

Contributions are welcome!  
If you'd like to help improve the Reddit–Lemmy Bridge:

1. Fork this repository and create a new branch for your feature.  
2. Follow existing code conventions and add docstrings where appropriate.  
3. Test your changes using `TEST_MODE=true` before making pull requests.  
4. Open a PR describing your feature, enhancement, or fix.  

All contributions are reviewed for maintainability, clarity, and backward compatibility.
