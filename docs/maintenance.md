# Maintenance & Operations Guide

For system administrators and developers maintaining a Reddit ↔ Lemmy Mirror instance.

---

## 🧱 Core Components

| File | Purpose |
|------|----------|
| `auto_mirror.py` | Reddit → Lemmy posts |
| `reddit_comment_sync.py` | Reddit → Lemmy comments |
| `lemmy_comment_sync.py` | Lemmy → Reddit comments |
| `mirror_worker.py` | Job execution |
| `background_worker.py` | Queue and dashboard status |
| `worker_manager.py` | Job persistence and retry logic |
| `mirror_media.py` | Image/video mirroring |
| `main.py` | FastAPI dashboard backend |

---

## 🧩 Routine Maintenance

### View logs
```bash
docker compose logs -f mirror-dashboard
```

### Check container health
```bash
docker compose ps
```

### Restart all services
```bash
docker compose restart
```

### Backup databases
```bash
tar -czvf backup_$(date +%F).tar.gz data/
```

---

## 🧠 Dashboard Operations

| Action | Description |
|--------|--------------|
| 🩺 **Health Tab** | Displays CPU/RAM for all workers |
| 🪵 **Logs Tab** | Streams live logs via WebSocket |
| 🔁 **Restart/Stop/Start** | Direct container control |
| 🧱 **Build (no cache)** | Rebuilds Docker images |

All actions trigger feedback modals and automatic UI refreshes.

---

## 🧩 Database Locations

| Path | Purpose |
|------|----------|
| `data/jobs.db` | Job queue state |
| `data/bridge_cache.db` | Reddit↔Lemmy ID map |
| `data/media_cache.json` | Media rehosting cache |
| `data/state.json` | Dashboard heartbeat |

---

## ⚠️ Common Issues

| Symptom | Cause | Resolution |
|----------|--------|-------------|
| 401 Unauthorized | Expired Lemmy JWT | Auto-refreshes, or restart container |
| Skipped comments | Bot self-loops detected | Verify `REDDIT_BOT_USERNAME` |
| Missing posts | SUB_MAP mismatch | Restart `reddit-refresh` |
| Slow sync | Large queue backlog | Inspect `jobs.db` for retries |
| No dashboard | Port conflict or FastAPI error | Check logs of `mirror-dashboard` |

---

## 🧰 Advanced Debugging

Inspect jobs:
```bash
sqlite3 data/jobs.db "SELECT id,type,status FROM jobs LIMIT 20;"
```

Clear stuck jobs:
```bash
sqlite3 data/jobs.db "DELETE FROM jobs WHERE status='retrying';"
```

---

## 🧩 Extending the Bridge

- Add new job types in `worker_manager.py`  
- Register new workers via `BaseWorker` subclasses  
- Update `main.py` to expose new metrics  

---

## 🛡️ Security & Privacy

- Tokens stored locally (`data/token.json`)
- Do **not** commit `.env` files
- Use different Reddit/Lemmy accounts for testing

---

## 🧾 Versioning

Track schema and behavioral changes in `ROADMAP.md`.

Maintained by: FOSSCAD contributors and open-source community.
