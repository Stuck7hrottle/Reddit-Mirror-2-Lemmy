# 🗺️ Project Roadmap — Reddit → Lemmy Bridge

This roadmap outlines the progressive development stages for the Reddit-to-Lemmy mirroring bridge.  
Each stage builds upon the previous one, focusing on long-term reliability, transparency, and ease of deployment.

---

## 🧩 Stage 1 — Stability & Core Features ✅ *(Completed)*

- **Token Management**
  - Cached login tokens for persistent sessions  
  - Automatic refresh and rate-limit cooldown logic  
- **Mapping Persistence**
  - Persistent post and community mapping (`post_map.json`, `community_map.json`)  
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

## ⚙️ Stage 2 — UX & Diagnostics *(In Progress)*

- **Advanced Logging**
  - Optional `DEBUG_MODE=true` for detailed request/response tracing  
  - Structured JSON logs for easier parsing and external analysis  
- **Diagnostics**
  - `/health` endpoint for container status monitoring  
  - Log summary per mirror cycle (posts created, comments mirrored, skipped, errors)  
- **Enhanced Formatting**
  - Embed Reddit permalinks and media previews in Lemmy posts  
  - Preserve markdown and formatting consistency  

---

## 🌐 Stage 3 — Federation Awareness *(Planned)*

- **Remote Community Support**
  - Recognize and post to federated communities using `!community@domain` syntax  
  - Handle federation propagation delays gracefully  
- **Queue System**
  - Queue failed or rate-limited posts for retry  
  - Optionally backoff exponential retry schedule  
- **Cross-Linking**
  - (Optional) Post mirrored Lemmy URLs back to Reddit as crosslinks  

---

## 📦 Stage 4 — Automation & Maintenance *(Planned)*

- **Environment Self-Check**
  - Warn on missing `.env` variables or bad credentials  
- **Scheduled Tasks**
  - Automatic map refresh (community/post) every 6 hours  
  - Periodic cleanup of stale data entries  
- **Maintenance CLI**
  - Add standalone management commands:
    - `--clean-data` → clear mirror cache safely  
    - `--refresh-map` → force immediate map rebuild  
    - `--rebuild-tokens` → discard and renew JWTs  

---

## 🧠 Stage 5 — Smart Syncing & Analytics *(Future Goals)*

- **Metrics**
  - Track mirrored posts, failed attempts, retry counts, and duration per cycle  
  - Export metrics to Prometheus or Grafana  
- **Performance**
  - Optimize API request concurrency and adaptive backoff logic  
- **Dashboard**
  - Optional web dashboard for monitoring bridges, logs, and mapping status  

---

## 💡 Long-Term Vision

To evolve the Reddit–Lemmy Bridge into a fully autonomous synchronization service that:
- Self-heals after errors or restarts  
- Adapts to rate limits dynamically  
- Provides clear operational transparency  
- Serves as a general-purpose Lemmy integration template  

---

## 🤝 Contributing

Contributions are welcome!  
If you'd like to help improve the Reddit–Lemmy Bridge:

1. Fork this repository and create a new branch for your feature.  
2. Follow existing code conventions and add docstrings where appropriate.  
3. Test your changes using `TEST_MODE=true` before making pull requests.  
4. Open a PR describing your feature, enhancement, or fix.  

All contributions are reviewed for maintainability, clarity, and backward compatibility.
