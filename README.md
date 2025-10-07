# Reddit → Lemmy Bridge

A Dockerized bridge that mirrors **Reddit posts and comments** into **Lemmy communities** and keeps them synchronized.

- Multi‑subreddit → multi‑community mapping
- Full comment mirroring (nested threads)
- Edit synchronization
- Safe, configurable, and production‑friendly

> Works with Lemmy-Ansible 0.19.x+ and Docker Compose v2+.

---

## 🚀 Quick Start (Docker)

```bash
# 1) Clone your repo
git clone https://github.com/<you>/Reddit-Mirror-2-Lemmy.git
cd Reddit-Mirror-2-Lemmy

# 2) Prepare configuration
cp .env.example .env
# edit .env with your Reddit & Lemmy credentials

# 3) Choose a docker-compose file
cp docker-compose.example.yml docker-compose.yml
# edit the external network name to match your Lemmy-Ansible network

# 4) Build & start the bridge
docker compose build --no-cache
docker compose up -d

# 5) Watch logs
docker logs -f reddit-lemmy-bridge
```

> The bridge will begin mirroring new Reddit submissions based on your `SUB_MAP` configuration. Use the comment mirror and edit sync on demand (see below).

---

## ⚙️ Environment Configuration

Create a `.env` at the repository root using `.env.example` as a template.

Key settings (see `.env.example` for full list and documentation):

- **Reddit**: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`, `REDDIT_USER_AGENT`
- **Lemmy**: `LEMMY_URL` (e.g., `http://lemmy:8536` inside Docker or `https://lemmy.example.com`), `LEMMY_USER`, `LEMMY_PASS`
- **Mapping**: `SUB_MAP` → `subreddit1:community1,subreddit2:community2`
- **Data**: `DATA_DIR` → path inside the container (default `/app/data`)

> Do **not** commit `.env` to version control.

---

## 🧱 Project Layout

```
.
├── auto_mirror.py              # Mirrors new Reddit submissions to Lemmy
├── comment_mirror.py           # Mirrors full Reddit comment trees
├── edit_sync.py                # Syncs edits from Reddit to Lemmy
├── Dockerfile                  # Python 3.11 slim base
├── docker-compose.example.yml  # Generic Compose (copy to docker-compose.yml)
├── .env.example                # Example environment file
├── data/                       # Local cache (tokens, mappings) - bind mounted
└── docs/
    └── maintenance.md          # Advanced ops & troubleshooting
```

---

## 🐳 Deployment (Compose)

- Use `docker-compose.example.yml` as a starting point.
- Ensure the service attaches to your **external Lemmy Docker network** so the hostname `lemmy` resolves (or set `LEMMY_URL` to your public Lemmy URL).

Common commands:

```bash
# Start only the main post-mirroring service
docker compose up -d reddit-lemmy-bridge

# Run comment mirror on demand
docker compose run --rm reddit-comment-mirror
# or refresh all mapped posts to fill missing comments
docker compose run --rm reddit-comment-mirror --refresh

# Run edit sync on demand
docker compose run --rm reddit-edit-sync
```

---

## ✏️ Edit Synchronization

The `reddit-edit-sync` service checks Reddit for edits and updates corresponding Lemmy posts/comments.

```bash
docker compose run --rm reddit-edit-sync
```

Configure cadence with `EDIT_CHECK_LIMIT` and `EDIT_SLEEP` in `.env` if desired.

---

## 🔌 Testing Connectivity (Docker Networks)

Ensure the bridge can reach Lemmy via Docker networking:

```bash
# 1) List networks and find your Lemmy-Ansible network
docker network ls | grep lemmy

# 2) Confirm the bridge service is attached
docker network inspect <lemmy_network_name> | grep reddit-lemmy-bridge

# 3) Optional: test DNS from inside the container
docker compose exec reddit-lemmy-bridge getent hosts lemmy
```

If `lemmy` does not resolve, either attach to the correct external network in `docker-compose.yml` or set `LEMMY_URL` to your public instance URL (e.g., `https://lemmy.example.com`).

---

## 🧹 Maintenance & Troubleshooting

See **[docs/maintenance.md](./docs/maintenance.md)** for:
- Safe resets and what the `data/` cache stores
- Avoiding accidental re-mirroring
- Rebuilding mapping files
- Handling rate limits and retries
- Common errors and resolutions

---

## 🗺️ Roadmap

See **[ROADMAP.md](./ROADMAP.md)** for planned features and enhancements.

---

## 📝 License

Consider adding an open-source license (e.g., MIT) to welcome community contributions.
