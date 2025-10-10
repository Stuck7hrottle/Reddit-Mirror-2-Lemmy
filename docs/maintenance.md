# 🧰 Maintenance Guide — Reddit → Lemmy Bridge v1.1.0

This guide covers safe maintenance, cleanup, and diagnostic commands for the bridge after upgrading to **v1.1.0** (SQLite cache + Docker modernization).

---

## 📦 Where Data Is Stored

| File | Purpose |
|------|----------|
| `DATA_DIR/token.json` | Lemmy JWT tokens (auto-refreshed) |
| `DATA_DIR/community_map.json` + `.last` | Cached Lemmy community ID mappings |
| `DATA_DIR/bridge_cache.db` | **SQLite cache** (replaces legacy `post_map.json` and `comment_map.json`) |
| `DATA_DIR/post_map.json`, `DATA_DIR/comment_map.json` | Legacy JSON backups (still read on startup, but no longer written) |

---

## 🧹 Routine Maintenance

### 🔁 Force-Refresh Community Map
The community map auto-refreshes every 6 hours.  
To rebuild it immediately:

```bash
docker compose exec reddit-lemmy-bridge sh -lc 'rm -f "$DATA_DIR/community_map.last"'
docker compose restart reddit-lemmy-bridge
```

---

### 🔑 Clear Cached Token
If your Lemmy password changed or authentication fails:

```bash
docker compose exec reddit-lemmy-bridge sh -lc 'rm -f "$DATA_DIR/token.json"'
docker compose restart reddit-lemmy-bridge
```

---

### 🗃️ Inspect or Maintain the SQLite Cache

#### View Tables
```bash
docker compose exec reddit-lemmy-bridge sqlite3 "$DATA_DIR/bridge_cache.db" ".tables"
```

Expected output:
```
comments  posts
```

#### Show Row Counts
```bash
docker compose exec reddit-lemmy-bridge sqlite3 "$DATA_DIR/bridge_cache.db" "SELECT 'Posts', COUNT(*) FROM posts UNION ALL SELECT 'Comments', COUNT(*) FROM comments;"
```

#### Purge Old Entries (older than 30 days)
You can trigger this from within the container:
```bash
docker compose exec reddit-lemmy-bridge python3 - <<'PY'
from db_cache import DB
db = DB("/app/data/bridge_cache.db")
deleted = db.purge_old(30)
print(f"🧹 Purged {deleted} old cached rows (older than 30 days)")
PY
```

Or manually inside SQLite:
```bash
docker compose exec reddit-lemmy-bridge sqlite3 "$DATA_DIR/bridge_cache.db" "DELETE FROM posts WHERE last_synced < datetime('now','-30 days');"
```

---

### 💾 Back Up the Cache
```bash
docker compose exec reddit-lemmy-bridge cp "$DATA_DIR/bridge_cache.db" "$DATA_DIR/bridge_cache.backup.db"
```

Restore later if needed:
```bash
docker compose exec reddit-lemmy-bridge cp "$DATA_DIR/bridge_cache.backup.db" "$DATA_DIR/bridge_cache.db"
```

---

## 🧱 Safe Rebuild
Rebuild the service containers without data loss:

```bash
docker compose up -d --build reddit-lemmy-bridge
```

The `./data` directory (mounted to `/app/data`) preserves all tokens, cache, and backups automatically.

---

## 🧪 Test Mode
To verify functionality without touching Reddit:
1. Set `TEST_MODE=true` in `.env`
2. Restart:
   ```bash
   docker compose restart reddit-lemmy-bridge
   ```
3. You should see `Example mirrored post` in each mapped community.

---

## ⚖️ Handling Rate Limits
The bridge automatically backs off on comment posting.  
If your Lemmy instance enforces tighter limits, adjust the following in `.env`:

| Variable | Description |
|-----------|-------------|
| `REDDIT_LIMIT` | Lower to fetch fewer Reddit posts per cycle |
| `SLEEP_BETWEEN_CYCLES` | Increase (in seconds) to slow global mirror rate |
| `COMMENT_SLEEP` | Add per-comment delay if necessary |
| `MAX_POSTS_PER_RUN` | Optional cap on mirrored posts per cycle |

---

## 🧩 Notes
- The new SQLite cache (`bridge_cache.db`) is self-healing — if deleted, it will be recreated automatically.  
- Legacy JSON files remain untouched for rollback compatibility.  
- All maintenance commands are **idempotent**: safe to run multiple times.
