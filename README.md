# 🪶 Reddit → Lemmy Bridge (Universal Deployment Guide)

A complete Docker-based system that automatically mirrors **Reddit posts, media, and comments** to **Lemmy communities**, and keeps them up to date.

Designed for **Lemmy-Ansible 0.19.x+** and **Docker Compose v2+** environments.

---

## ✨ Features

- 🔁 **Multi-subreddit → multi-community mapping** (`SUB_MAP` in `.env`)
- 🖼️ **Pictrs upload support** for Reddit media
- 💬 **Full comment mirroring** (threaded structure preserved)
- ✏️ **Edit synchronization** keeps Lemmy posts & comments updated
- 🧠 **Token & post mapping cache** for continuity across runs
- 🐳 **Docker-native** and compatible with Lemmy-Ansible deployments

---

## 🧱 Project Structure

```
.
├── auto_mirror.py
├── comment_mirror.py
├── edit_sync.py
├── Dockerfile
├── docker-compose.yml
├── .env
├── data/
└── README.md
```

---

## ⚙️ Environment Configuration (`.env`)

Create a `.env` file in the project root and configure the following variables:

```ini
# === Reddit API Credentials ===
REDDIT_CLIENT_ID=your_reddit_app_id
REDDIT_CLIENT_SECRET=your_reddit_app_secret
REDDIT_USERNAME=your_reddit_bot_username
REDDIT_PASSWORD=your_reddit_bot_password
REDDIT_USER_AGENT=reddit-lemmy-bridge/1.0

# === Lemmy Instance ===
LEMMY_URL=http://lemmy:8536
LEMMY_USER=mirrorbot
LEMMY_PASS=your_lemmy_bot_password

# === Mirroring Behavior ===
SUB_MAP=subreddit1:community1,subreddit2:community2
SLEEP_SECONDS=900
MAX_POSTS_PER_RUN=5

# === Comment Mirroring ===
MIRROR_COMMENTS=true
COMMENT_LIMIT=3
COMMENT_LIMIT_TOTAL=500
COMMENT_SLEEP=0.3

# === Edit Sync ===
MIRROR_EDITS=true
EDIT_CHECK_LIMIT=50
EDIT_SLEEP=0.5

# === Internal Data Storage ===
DATA_DIR=/app/data
```

---

## 📦 Quick Start: Using Example Files

To simplify setup, this repository includes example configuration templates:

| File | Purpose |
|------|----------|
| `.env.example` | Template for your Reddit and Lemmy credentials |
| `docker-compose.example.yml` | Generic Docker Compose setup, compatible with most Lemmy-Ansible instances |

Copy and edit them before deployment:

```bash
cp .env.example .env
cp docker-compose.example.yml docker-compose.yml
```

Then open `.env` and update your credentials and subreddit/community mappings.

---

## 🗺️ Project Roadmap

See [ROADMAP.md](./ROADMAP.md) for planned upgrades and new features.

---

## ❤️ Credits

Created by **Stuck7hrottle** and contributors.  
Built with 🐍 Python, Docker, and caffeine.

> “Mirror freely, federate widely.” 🌍
