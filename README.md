# Reddit → Lemmy Bridge

A self-hosted bridge that mirrors posts and comments from selected Reddit communities (subreddits) into corresponding Lemmy communities — keeping content synchronized and formatted for federated discussion.

---

## 🚀 Quick Start (Docker)

Clone the repository and configure your `.env` file:

```bash
cp .env.example .env
```

Then start the bridge:

```bash
docker compose up -d reddit-lemmy-bridge
```

The bridge will automatically:
- Log in to your Lemmy instance using Bearer token authentication
- Mirror posts and comments from defined Reddit subs
- Cache tokens and community mappings for reuse

---

## ⚙️ Environment Configuration

Set these environment variables in your `.env` file:

| Variable | Description | Example |
|-----------|-------------|----------|
| `LEMMY_URL` | Your Lemmy base URL (public address) | `https://your-lemmy-instance.com` |
| `LEMMY_USER` | Lemmy bot username | `mirrorbot` |
| `LEMMY_PASS` | Lemmy bot password | `yourStrongPassword` |
| `SUB_MAP` | Comma-separated mapping of Reddit→Lemmy communities | `fosscad2:fosscad2,3d2a:3d2a,example:Example` |
| `DATA_DIR` | Path for token and cache data | `/app/data` |
| `REFRESH` | Force community map refresh on startup | `true` |

---

## 🧩 Lemmy Compatibility

This bridge supports **Lemmy v0.19.x and later**, which use **JWT Bearer authentication** instead of the deprecated `"auth"` field.

All API requests now send:
```http
Authorization: Bearer <token>
```

The bridge automatically logs in when needed and reuses tokens between runs.

---

## 🧠 Token & Map Caching

Tokens and community maps are cached inside the container:

- **Token file:** `/app/data/token.json`
- **Community map:** `/app/data/community_map.json`

The map automatically refreshes every **6 hours**, or immediately if `REFRESH=true` is set.

---

## 🔄 Comment Mirroring

Each mirrored post is created on Lemmy with the original Reddit permalink and media preview embedded in the body.  
Comments are synchronized below the corresponding Lemmy post, preserving structure and order.

---

## 🧰 Maintenance

To safely reset caches or re-mirror all posts:

```bash
docker compose down
rm -rf data/*
docker compose up -d reddit-lemmy-bridge
```

To trigger an immediate refresh of community mappings:
```bash
docker compose run --rm -e REFRESH=true reddit-lemmy-bridge
```

---

## 🧪 Testing Connectivity

Verify your Lemmy instance API before running the bridge:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://your-lemmy-instance.com/api/v3/site
```
If you see `200`, the bridge can connect successfully.

---

## 🧩 Troubleshooting

| Issue | Cause | Solution |
|-------|--------|-----------|
| **401 `incorrect_login`** | Lemmy password changed or token expired | Restart container to refresh token |
| **404 `couldnt_find_community`** | Mismatched names in `SUB_MAP` | Ensure Lemmy and Reddit names match exactly |
| **duplicate key `login_token_pkey`** | Rapid login attempts | Fixed in Bearer-auth version; ensure cooldown remains active |
| **Empty mirrors** | Reddit API rate limits or no new posts | Wait for next cycle or set `REFRESH=true` |

---

## 🧭 Roadmap

See [ROADMAP.md](docs/ROADMAP.md) for current development phases and future goals.

---

## 🪪 License

MIT License — open source, self-hosted, and federated.
