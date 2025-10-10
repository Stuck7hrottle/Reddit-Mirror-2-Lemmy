# 🪞 Reddit → Lemmy Bridge

Mirror posts and comments from Reddit communities into Lemmy communities — automatically and continuously.  
Designed for stability, rate-limit resilience, and easy container deployment.

---

## ✨ Features

- 🔁 **Mirrors Reddit posts** to a matching Lemmy community  
- 💬 **Mirrors Reddit comments** into their respective Lemmy threads  
- 🧠 Caches JWT tokens and refreshes automatically  
- 🕒 Respects Lemmy’s rate limits with exponential backoff  
- 🐳 Dockerized for simple deployment  
- 🔧 Configurable entirely through `.env`  
- 💾 Tracks mirrored items via `post_map.json` and `comment_map.json`

---

## 📦 Requirements

- Docker and Docker Compose
- A Lemmy instance (self-hosted or federated)
- Two Lemmy bot accounts (recommended):
  - One for posts (e.g. `mirrorbot`)
  - One for comments (e.g. `mirrorcomments`)
- Reddit API credentials (from [Reddit App Console](https://www.reddit.com/prefs/apps))

---

## ⚙️ Setup

### 1️⃣ Clone and enter the repository

```bash
git clone https://github.com/YOURNAME/Reddit-Mirror-2-Lemmy.git
cd Reddit-Mirror-2-Lemmy
```

### 2️⃣ Create your `.env` file

Copy and edit the provided example:

```bash
cp .env.example .env
```

Fill in your Reddit and Lemmy credentials inside `.env` (see below).

### 3️⃣ Build and start the containers

```bash
docker compose build
docker compose up -d
```

This runs both bots:
- `reddit-lemmy-bridge`: Handles posts  
- `reddit-comment-mirror`: Handles comments

Logs can be followed with:

```bash
docker compose logs -f
```

---

## ⚙️ `.env` Configuration

| Variable | Description |
|-----------|--------------|
| `REDDIT_CLIENT_ID` | Your Reddit app’s client ID |
| `REDDIT_CLIENT_SECRET` | Your Reddit app’s client secret |
| `REDDIT_USERNAME` | Reddit account username |
| `REDDIT_PASSWORD` | Reddit account password |
| `REDDIT_USER_AGENT` | User agent (e.g. `reddit-lemmy-bot/1.0`) |
| `REDDIT_SUBREDDITS` | Comma-separated list of subreddits to mirror |
| `LEMMY_URL` | Base URL of your Lemmy instance |
| `LEMMY_USER` | Lemmy bot account for posts |
| `LEMMY_PASS` | Password for post bot |
| `LEMMY_USER_COMMENTS` | Lemmy account for comments |
| `LEMMY_PASS_COMMENTS` | Password for comment bot |
| `MIRROR_COMMUNITY` | Lemmy community to post to (e.g. `fosscad2`) |
| `POLL_INTERVAL` | Seconds between Reddit checks |
| `COMMENT_LIMIT` | Max comments per post mirrored |
| `COMMENT_LIMIT_TOTAL` | Global comment mirror limit |
| `COMMENT_SLEEP` | Delay between comment posts (seconds) |

---

## 🧩 Docker Compose Overview

Two coordinated services:

```yaml
services:
  reddit-lemmy-bridge:
    build: .
    container_name: reddit-lemmy-bridge
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./auto_mirror.py:/app/auto_mirror.py:ro
      - ./data:/app/data
    command: ["python", "-u", "auto_mirror.py"]

  reddit-comment-mirror:
    build: .
    container_name: reddit-comment-mirror
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./comment_mirror.py:/app/comment_mirror.py:ro
      - ./data:/app/data
    command: ["python", "-u", "comment_mirror.py"]
```

Both containers share a `/data` directory containing:
- `post_map.json` — tracked Reddit→Lemmy post pairs  
- `comment_map.json` — tracked Reddit→Lemmy comment pairs  
- `token.json` — cached JWTs

---

## 🚀 Advanced Options

### 🔁 Refresh existing posts
If you update your formatting logic or add embeds:

```bash
docker exec -it reddit-lemmy-bridge python3 /app/auto_mirror.py --update-existing
```

### 🧱 Rate Limit Adjustments
If you host Lemmy yourself, add to `lemmy.hjson`:

```hjson
trusted_users: ["mirrorbot", "mirrorcomments"]
rate_limit: {
  message: 100
  message_per_second: 5
  post: 50
  post_per_second: 3
}
```

Restart Lemmy after editing:
```bash
docker compose restart lemmy
```

---

## 🧠 Troubleshooting

| Problem | Cause / Fix |
|----------|--------------|
| `rate_limit_error` | Reduce COMMENT_LIMIT or raise Lemmy limits |
| `invalid_post_title` | Reddit post title too long → truncate in script |
| `JWT appears invalid` | Normal — token auto-refreshes |
| No comments mirrored | Ensure `comment_mirror.py` is running |
| 404 on update | Lemmy post deleted or unlisted |

---

## 🧭 Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features, including:
- Web dashboard for monitoring sync
- Cross-instance Lemmy mirroring
- Better media embedding

---

## 🛠️ Contributing

Pull requests welcome!  
For bug reports or feature ideas, open an issue.

---

## 🧾 License

MIT License © 2025 YourName  
Contributions welcome under the same license.