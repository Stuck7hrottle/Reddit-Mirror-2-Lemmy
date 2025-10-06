# 🪶 Reddit → Lemmy Bridge

A fully automated bridge that mirrors **Reddit posts, images, videos, galleries, and comments** into **Lemmy communities**, complete with full comment trees, Pictrs uploads, and optional edit synchronization.

Designed for **Lemmy-Ansible 0.19.x** and **Docker Compose v2+**.

---

## ✨ Features

- 🔁 **Multi-subreddit → multi-community mapping** (`SUB_MAP` in `.env`)  
- 🖼️ **Pictrs upload support** for images, galleries, and thumbnails  
- 💬 **Full comment mirroring**, including nested threads (depth-aware)  
- 🧠 **Smart caching** — token, community, and post mappings saved in `/data`  
- ✏️ **Optional edit sync** — keeps Lemmy posts and comments up to date  
- 🐳 **Docker native** — drop-in compatible with Lemmy-Ansible’s network  

---

## 🧱 Project Structure

```
.
├── auto_mirror.py          # Main Reddit → Lemmy mirroring engine
├── edit_sync.py            # Optional edit synchronization companion
├── Dockerfile              # Lightweight Python 3.11 image
├── docker-compose.yml      # Multi-service bridge setup
├── .env                    # Environment configuration
└── data/                   # Cached tokens, mappings, and logs
```

---

## ⚙️ Environment Configuration (`.env`)

```env
# === Lemmy Connection ===
LEMMY_INSTANCE=http://lemmy:8536
LEMMY_USER=YOUR-LEMMY-BOT-USER-NAME
LEMMY_PASS=YOUR-LEMMY-BOT-USER-PASSWORD

# === Subreddit → Community Mapping ===
# Format: reddit_subreddit:lemmy_community,sub2:comm2,...
SUB_MAP=fosscad2:fosscad2,FOSSCADtoo:FOSSCADtoo,3d2a:3d2a

# === Main Behavior ===
SLEEP_SECONDS=900           # Wait time between cycles (seconds)
MIRROR_COMMENTS=true        # Enable full comment tree mirroring

# === Comment & Rate Limit Settings ===
COMMENT_LIMIT_TOTAL=500     # Max total comments per Reddit post
COMMENT_SLEEP=0.3           # Delay between comment posts (seconds)
GALLERY_SLEEP=1             # Delay between gallery uploads (seconds)

# === Optional Edit Synchronization ===
MIRROR_EDITS=true
EDIT_CHECK_LIMIT=50         # Max recent posts to recheck per cycle
EDIT_SLEEP=0.5              # Delay between edit checks (seconds)

# === Internal Cache Directory ===
DATA_DIR=/app/data
```

---

## 🐳 Docker Setup

### **Dockerfile**

```dockerfile
FROM python:3.11-slim

LABEL maintainer="Stuck7hrottle"       description="Reddit → Lemmy Bridge"

WORKDIR /app

RUN apt-get update &&     apt-get install -y --no-install-recommends curl ca-certificates &&     pip install --no-cache-dir requests &&     rm -rf /var/lib/apt/lists/*

RUN useradd -m bridge
USER bridge

COPY . /app
CMD ["python", "-u", "auto_mirror.py"]
```

---

### **docker-compose.yml**

```yaml
version: "3.8"

services:
  reddit-lemmy-bridge:
    container_name: reddit-lemmy-bridge
    build: .
    restart: unless-stopped
    env_file:
      - .env
    working_dir: /app
    volumes:
      - ./auto_mirror.py:/app/auto_mirror.py:ro
      - ./data:/app/data
    command: ["python", "-u", "auto_mirror.py"]
    networks:
      - lemmy_net
    hostname: reddit-lemmy-bridge

  edit-sync:
    container_name: reddit-lemmy-edit-sync
    build: .
    restart: unless-stopped
    env_file:
      - .env
    working_dir: /app
    volumes:
      - ./edit_sync.py:/app/edit_sync.py:ro
      - ./data:/app/data
    command: ["python", "-u", "edit_sync.py"]
    networks:
      - lemmy_net
    hostname: reddit-lemmy-edit-sync

networks:
  # Attach to Lemmy’s Docker network so “lemmy” resolves correctly
  lemmy_net:
    external: true
#    name: EDIT-FOR-YOUR-NETWORK-IF-NEEDED
```

---

## 🚀 Deployment

### 1️⃣ Build and Start

```bash
cd /opt/Reddit-Mirror-2-Lemmy
docker compose build --no-cache
docker compose up -d
```

### 2️⃣ Watch Logs

```bash
docker logs -f reddit-lemmy-bridge
```

Sample output:
```
🔑 Logging in to http://lemmy:8536/api/v3/user/login as mirrorbot
✅ Logged into Lemmy (new token cached)
🔍 Checking r/fosscad2 → c/fosscad2 ...
🖼️ Uploaded to Pictrs: http://lemmy:8536/pictrs/image/abcd1234
✅ Posted “FGC-9 Receiver Drop”
💬 Comment 1 by u/FOSSCADDev
💬 Comment 2 by u/PrintMaster
✅ Mirrored 230 comments.
🕒 Sleeping 900s...
```

---

## ✏️ Edit Synchronization

`edit_sync.py` keeps mirrored threads in sync with Reddit when posts or comments are edited.

### Run manually
```bash
docker compose run --rm edit-sync
```

### Or run continuously (Compose auto-restarts it)
```bash
docker compose up -d edit-sync
```

Example output:
```
🪶 Checking edits for r/fosscad2 ...
✏️ Post u/PrintMaster edited — updating Lemmy post 203
✅ Updated Lemmy post
🕒 Finished edit sync at 2025-10-07 00:05:02 UTC
```

---

## 🧠 Tips

| Setting | Description | Default |
|----------|--------------|----------|
| `SLEEP_SECONDS` | Delay between Reddit checks | 900 |
| `COMMENT_LIMIT_TOTAL` | Max number of comments per post | 500 |
| `COMMENT_SLEEP` | Delay between comment posts | 0.3 |
| `GALLERY_SLEEP` | Delay between Pictrs uploads | 1 |
| `EDIT_CHECK_LIMIT` | Recent posts to check for edits | 50 |
| `EDIT_SLEEP` | Delay between edit checks | 0.5 |

---

## 🧹 Maintenance

To reset everything (new login + fresh sync):

```bash
docker compose down
rm -rf data/*
docker compose up -d --build
```

---

## 🧩 Testing Connectivity

If your bridge can’t reach Lemmy, test from inside the container:

```bash
docker exec -it reddit-lemmy-bridge curl -I http://lemmy:8536/api/v3/site
```

Expected output:
```
HTTP/1.1 200 OK
content-type: application/json
```

If it fails, double-check your `LEMMY_INSTANCE` and that the container is on the `fosscadguncaddesignscom_default` network:
```bash
docker network inspect fosscadguncaddesignscom_default | grep reddit-lemmy-bridge
```

---

## ❤️ Credits

Created by **Stuck7hrottle** and contributors.  
Built with 🐍 Python, Docker, and caffeine.

> “Mirror freely, federate widely.” 🌍
