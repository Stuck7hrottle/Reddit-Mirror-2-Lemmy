# Maintenance Guide — Reddit → Lemmy Bridge

This document explains how to safely maintain, update, and troubleshoot the Reddit → Lemmy bridge.  
It is designed for self-hosted operators and admins maintaining a persistent mirroring service.

---

## 🧩 Core Maintenance Tasks

### 🔄 1. Restarting the Bridge

Restart the bridge after configuration or code changes:

```bash
docker compose restart reddit-lemmy-bridge
```

To rebuild from scratch (after updates or dependency changes):

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

---

### 🧠 2. Managing Cached Data

The bridge stores:
- **JWT Token:** `/app/data/token.json`  
  → Cached login token for Lemmy Bearer authentication.
- **Community Map:** `/app/data/community_map.json`  
  → Cached mapping of Reddit → Lemmy community IDs.

These files regenerate automatically when missing or expired.  
You can safely delete them to trigger a fresh sync:

```bash
docker compose down
rm -rf data/token.json data/community_map.json
docker compose up -d reddit-lemmy-bridge
```

---

### 🕒 3. Automatic Map Refresh

The bridge auto-refreshes the Lemmy community map every **6 hours**  
(or immediately at startup if `REFRESH=true` is set in `.env`).

To manually force a refresh without restarting all containers:

```bash
docker compose run --rm -e REFRESH=true reddit-lemmy-bridge
```

---

### 🔑 4. Lemmy Authentication (Bearer Mode)

The bridge uses Lemmy’s modern **JWT Bearer authentication** system.

Every API call includes:
```http
Authorization: Bearer <token>
```

Tokens are refreshed automatically when invalid or expired, and cached between runs.  
The system includes **cooldown protection** to prevent the `"duplicate key value violates unique constraint 'login_token_pkey'"` error from rapid re-logins.

If login still fails, verify the `.env` credentials:

```
LEMMY_URL=https://your-lemmy-instance.com
LEMMY_USER=mirrorbot
LEMMY_PASS=yourStrongPassword
```

Then restart the bridge.

---

## 🧹 Cleanup Procedures

### 🧾 Reset Mirror State (Full Rebuild)

To clear and rebuild all mirrored data:

```bash
docker compose down
rm -rf data/*
docker compose up -d reddit-lemmy-bridge
```

This wipes local caches only — your Lemmy posts remain intact.

---

### 🧰 Manual Connectivity Test

Before troubleshooting deeper issues, confirm Lemmy API access:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://your-lemmy-instance.com/api/v3/site
```

If the result is **200**, your instance is reachable.

---

## 🧩 Troubleshooting

| Problem | Cause | Solution |
|----------|--------|-----------|
| **401 `incorrect_login`** | Token invalid or password mismatch | Restart bridge to refresh token |
| **400 `duplicate key value violates login_token_pkey`** | Lemmy login spam | Fixed in Bearer-auth version; wait cooldown |
| **404 `couldnt_find_community`** | Community name mismatch | Ensure names in `SUB_MAP` match Lemmy exactly |
| **Token not refreshing** | Cached `token.json` corrupted | Delete file and restart |
| **Empty mirrors** | Reddit rate limit or no new posts | Wait for next polling cycle |

---

## 🧱 Data Persistence

All cached and runtime data is stored in `/app/data`.  
This directory is **persistent across restarts**, ensuring:
- No redundant Lemmy logins between cycles
- Minimal API load
- Automatic state recovery after downtime

Ensure your `docker-compose.yml` binds the data directory correctly:

```yaml
volumes:
  - ./data:/app/data
```

---

## 🪪 License

MIT License  
Open-source and maintained for federated Lemmy and Reddit mirroring.
