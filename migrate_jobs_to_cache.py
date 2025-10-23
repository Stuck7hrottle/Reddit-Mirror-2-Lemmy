#!/usr/bin/env python3
"""
One-time migration:
Copy mirrored Reddit→Lemmy posts from jobs.db into bridge_cache.db.
"""

import sqlite3
from pathlib import Path

JOBS_DB = Path("/opt/Reddit-Mirror-2-Lemmy/data/jobs.db")
CACHE_DB = Path("/opt/Reddit-Mirror-2-Lemmy/data/bridge_cache.db")

if not JOBS_DB.exists():
    raise SystemExit(f"❌ jobs.db not found at {JOBS_DB}")
if not CACHE_DB.exists():
    raise SystemExit(f"❌ bridge_cache.db not found at {CACHE_DB}")

print(f"🔍 Opening {JOBS_DB} and {CACHE_DB}…")

src = sqlite3.connect(JOBS_DB)
dst = sqlite3.connect(CACHE_DB)

# Try to detect likely columns
cursor = src.execute("PRAGMA table_info(posts)")
columns = [c[1] for c in cursor.fetchall()]
print(f"📋 jobs.db 'posts' columns: {columns}")

# Figure out which schema to use
if "reddit_post_id" in columns and "lemmy_post_id" in columns:
    rows = src.execute("SELECT reddit_post_id, lemmy_post_id FROM posts").fetchall()
elif "reddit_id" in columns and "lemmy_id" in columns:
    rows = src.execute("SELECT reddit_id, lemmy_id FROM posts").fetchall()
else:
    raise SystemExit("❌ Could not find compatible columns in jobs.db")

print(f"📦 Found {len(rows)} rows to import")

# Insert into bridge_cache.db
inserted = 0
for reddit_id, lemmy_id in rows:
    if not reddit_id or not lemmy_id:
        continue
    try:
        dst.execute(
            """
            INSERT OR IGNORE INTO posts (reddit_id, lemmy_id, subreddit, last_synced)
            VALUES (?, ?, NULL, CURRENT_TIMESTAMP)
            """,
            (reddit_id, str(lemmy_id)),
        )
        inserted += 1
    except Exception as e:
        print(f"⚠️ Error inserting {reddit_id}: {e}")

dst.commit()
dst.close()
src.close()

print(f"✅ Migration complete. {inserted} records copied into bridge_cache.db.")
