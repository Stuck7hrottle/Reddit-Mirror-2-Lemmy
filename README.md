# Reddit → Lemmy Bridge

A self-hosted bridge that mirrors Reddit posts, edits, and comments into Lemmy communities.  
Compatible with Docker and Lemmy-Ansible deployments.

---

## 🚀 Quick Start (Docker)

```bash
git clone https://github.com/yourname/Reddit-Mirror-2-Lemmy.git
cd Reddit-Mirror-2-Lemmy
cp .env.example .env
docker compose up -d reddit-lemmy-bridge
```

---

## ⚙️ Environment Configuration

Edit `.env` to match your Reddit and Lemmy credentials.

```ini
# --- Reddit API credentials ---
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_secret
REDDIT_USERNAME=your_username
REDDIT_PASSWORD=your_password
REDDIT_USER_AGENT=reddit-lemmy-bridge/1.0

# --- Lemmy credentials ---
LEMMY_URL=http://lemmy:8536
LEMMY_USER=mirrorbot
LEMMY_PASS=securepassword
LEMMY_COMMUNITY=example

# --- Mirroring options ---
SUB_MAP=fosscad2:fosscad2,FOSSCADtoo:FOSSCADtoo,3d2a:3d2a
REDDIT_LIMIT=10
SLEEP_SECONDS=900
DATA_DIR=/data

# --- Optional limits ---
MAX_POSTS_PER_RUN=5
COMMENT_LIMIT_TOTAL=500
COMMENT_SLEEP=0.3
MIRROR_COMMENTS=true
```

---

## 🧱 Deployment

```bash
docker compose up -d reddit-lemmy-bridge
```
Logs:
```bash
docker compose logs -f reddit-lemmy-bridge
```

---

## 💬 Comment Mirroring

To sync Reddit comments into Lemmy:

```bash
docker compose run --rm reddit-comment-mirror
```

Rebuild all comment threads or fill missing ones:

```bash
docker compose run --rm -e REFRESH=true reddit-comment-mirror
```

A `comment_map.json` file is stored in `/data` to prevent duplicates.

---

## ✏️ Edit Synchronization

```bash
docker compose run --rm edit-sync
```

Updates existing posts and comments if edited on Reddit.

---

## 🧰 Maintenance

```bash
rm -rf data/*
docker compose run --rm -e REFRESH=true reddit-comment-mirror
```

See [`docs/maintenance.md`](docs/maintenance.md) for details.

---

## 🔍 Testing Connectivity

Check Docker network resolution:

```bash
docker network inspect example_default | grep reddit-lemmy-bridge
```

If needed, attach to Lemmy’s network:

```yaml
networks:
  lemmy_net:
    external: true
    name: example_default
```

---

## 🧩 Tools Included

| Script | Purpose |
|---------|----------|
| `auto_mirror.py` | Mirrors Reddit submissions |
| `comment_mirror.py` | Syncs Reddit comments |
| `edit_sync.py` | Mirrors Reddit edits |

---

## 🗺️ Roadmap

See [`ROADMAP.md`](ROADMAP.md).

---

## 📜 License

MIT License © 2025 YourName
