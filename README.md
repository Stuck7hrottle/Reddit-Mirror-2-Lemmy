# Reddit → Lemmy Mirror

A Dockerized bridge that automatically mirrors Reddit posts and comments to Lemmy communities.

---

## Features
- ✅ Mirrors Reddit posts and comments to Lemmy.
- 🖼️ Supports images, galleries, Imgur, and Reddit-hosted videos.
- 🔁 Periodic refresh to update posts with new content.
- 🧠 SQLite caching (`jobs.db`) to prevent duplicates.
- ⚙️ Docker Compose for easy deployment.

---

## Containers
| Service | Purpose |
|----------|----------|
| `reddit-mirror` | Background worker that mirrors new posts/comments |
| `reddit-refresh` | Periodic scheduler that re-runs the mirroring logic |

---

## Setup
1. Copy `examples/.env` → `.env` and fill in your tokens.
2. Build and start:
   ```bash
   docker compose build
   docker compose up -d
   ```
3. View logs:
   ```bash
   docker compose logs -f reddit-mirror
   ```

---

## Manual Update
If you make code changes or want to refresh all mirrored posts:
```bash
docker compose run --rm reddit-mirror python3 auto_mirror.py --update-existing
```

---

## Data Storage
All persistent data lives in:
```
./data/
├── jobs.db             # post/comment cache
├── bridge_cache.db     # internal mappings
├── token.json          # Lemmy auth token
```

---

## License
MIT
