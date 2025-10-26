# Project Roadmap

## ✅ Current Release (v2.4)

- Two-way post & comment mirroring  
- Dashboard with live Docker container controls  
- Persistent queue with SQLite jobs database  
- Media caching and `/pictrs` rehosting  
- Auto-reconnect WebSocket log viewer  
- `.env` reloadable without restart  

---

## 🧩 Completed Milestones

| Version | Highlights |
|----------|-------------|
| v2.0 | Transitioned from JSON → SQLite job system |
| v2.1 | Reddit → Lemmy comment mirroring |
| v2.2 | Lemmy → Reddit mirroring (two-way) |
| v2.3 | Dashboard metrics, charts, and live logs |
| v2.4 | Docker health stats, rebuild actions |

---

## 🛠️ In Progress

- Image/video mirroring inside **comments**
- Toggle comment sync from dashboard  
- Dashboard config editor (`submap.json`)  
- Error reporting panel in dashboard  
- Graceful pause/resume for background workers  

---

## 🌱 Planned Features

| Area | Enhancement |
|-------|-------------|
| **Media** | Direct upload to Lemmy `/pictrs` from comments |
| **Moderation** | Blocklist support for users or subs |
| **Dashboard** | Token health display and job queue graph |
| **Federation** | Cross-instance post synchronization |
| **Scaling** | Multi-instance load balancing |
| **Security** | Token rotation and audit logging |

---

## 🧭 Long-Term Vision

- Seamless federation across multiple Lemmy instances  
- Mirror multiple Reddit→Lemmy pairs concurrently  
- Adaptive rate-limiting using real-time metrics  
- Plugin API for community-specific rules  

---

## 🧾 Changelog Summary
See commit history for detailed updates and schema migrations.
