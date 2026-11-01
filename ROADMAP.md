# Project Roadmap

## ✅ Current Release (v3.0.0)

- Two-way post & comment mirroring between Reddit and Lemmy
- Odysee channel → Lemmy community mirroring, with full-history backfill
- Reddit videos downloaded directly from Reddit's video CDN (v.redd.it), with yt-dlp as a fallback
- Link posts keep their URL so Lemmy shows previews, thumbnails and players
- `repair_posts.py` to fix older posts that lost their link or video
- Nightly backups and log rotation (`contrib/`)
- Redesigned dashboard: plain status, 24-hour activity, accurate totals, worker controls, filterable logs
- Hardened dashboard: localhost-only by default, login via nginx, CSRF protection, control limited to the mirror workers
- Persistent queue with SQLite jobs database
- Media caching and `/pictrs` rehosting

---

## 🧩 Completed Milestones

| Version | Highlights |
|----------|-------------|
| v2.0 | Transitioned from JSON → SQLite job system |
| v2.1 | Reddit → Lemmy comment mirroring |
| v2.2 | Lemmy → Reddit mirroring (two-way) |
| v2.3 | Dashboard metrics, charts, and live logs |
| v2.4 | Docker health stats, rebuild actions |
| v3.0 | Odysee source, Reddit video fix, repair tool, backups, dashboard redesign and hardening |

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
| **Sources** | More non-Reddit sources using the shared `core/models.py` format |

---

## 🧭 Long-Term Vision

- Seamless federation across multiple Lemmy instances  
- Mirror multiple Reddit→Lemmy pairs concurrently  
- Adaptive rate-limiting using real-time metrics  
- Plugin API for community-specific rules  

---

## 🧾 Changelog Summary
See [CHANGELOG.md](CHANGELOG.md) for detailed changes in each release.
