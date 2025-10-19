#!/usr/bin/env python3
"""
db_cache.py — Unified mapping cache for multi-source → Lemmy bridge
-------------------------------------------------------------------
Stores mappings between source platform IDs and Lemmy IDs
for posts and comments.

Replaces the Reddit-only DB with a source-agnostic version.
Includes automatic migration from old schema.
"""

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

# ───────────────────────────────
# Configuration
# ───────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bridge_cache.db"


class DB:
    """
    Multi-source SQLite cache for {source} → Lemmy ID mappings.
    Thread-safe and lightweight, with automatic schema migration.
    """

    _lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path) if db_path else str(DB_PATH)
        self._init_db()
        self._migrate_old_schema()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # ───────────────────────────────
    # Schema Initialization
    # ───────────────────────────────
    def _init_db(self):
        with self._get_conn() as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_post_id TEXT NOT NULL,
                    lemmy_id TEXT,
                    community TEXT,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, source_post_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_comment_id TEXT NOT NULL,
                    lemmy_id TEXT,
                    parent_source_id TEXT,
                    parent_lemmy_id TEXT,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, source_comment_id)
                );
                """
            )

    # ───────────────────────────────
    # Migration (from Reddit-only)
    # ───────────────────────────────
    def _migrate_old_schema(self):
        """Automatically migrate from old reddit_id-based schema if found."""
        with self._get_conn() as conn, conn:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='posts';")
            if not cur.fetchone():
                return  # Nothing to migrate

            cols = [r[1] for r in conn.execute("PRAGMA table_info(posts);").fetchall()]
            if "reddit_id" in cols and "source_post_id" not in cols:
                # Rename and migrate old tables
                conn.execute("ALTER TABLE posts RENAME TO posts_old;")
                conn.execute("ALTER TABLE comments RENAME TO comments_old;")
                self._init_db()
                # Migrate data as 'reddit' source
                conn.execute(
                    """
                    INSERT OR IGNORE INTO posts (source, source_post_id, lemmy_id, community, last_synced)
                    SELECT 'reddit', reddit_id, lemmy_id, subreddit, last_synced FROM posts_old;
                    """
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO comments (source, source_comment_id, lemmy_id, parent_source_id, parent_lemmy_id, last_synced)
                    SELECT 'reddit', reddit_id, lemmy_id, parent_reddit_id, parent_lemmy_id, last_synced FROM comments_old;
                    """
                )
                conn.execute("DROP TABLE posts_old;")
                conn.execute("DROP TABLE comments_old;")
                conn.commit()
                print("✅ Migrated old reddit-only schema to multi-source format.")

    # ───────────────────────────────
    # Post Mappings
    # ───────────────────────────────
    def save_post(self, source: str, source_post_id: str, lemmy_id: str, community: Optional[str] = None) -> None:
        with self._lock, self._get_conn() as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO posts (source, source_post_id, lemmy_id, community, last_synced)
                VALUES (?, ?, ?, ?, ?);
                """,
                (source, source_post_id, lemmy_id, community, datetime.utcnow()),
            )

    def get_lemmy_post_id(self, source: str, source_post_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT lemmy_id FROM posts WHERE source = ? AND source_post_id = ?;",
                (source, source_post_id),
            ).fetchone()
            return row[0] if row else None

    # ───────────────────────────────
    # Comment Mappings
    # ───────────────────────────────
    def save_comment(
        self,
        source: str,
        source_comment_id: str,
        lemmy_id: str,
        parent_source_id: Optional[str] = None,
        parent_lemmy_id: Optional[str] = None,
    ) -> None:
        with self._lock, self._get_conn() as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO comments
                (source, source_comment_id, lemmy_id, parent_source_id, parent_lemmy_id, last_synced)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (source, source_comment_id, lemmy_id, parent_source_id, parent_lemmy_id, datetime.utcnow()),
            )

    def get_lemmy_comment_id(self, source: str, source_comment_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT lemmy_id FROM comments WHERE source = ? AND source_comment_id = ?;",
                (source, source_comment_id),
            ).fetchone()
            return row[0] if row else None

    # ───────────────────────────────
    # Maintenance / Stats
    # ───────────────────────────────
    def purge_old(self, days: int = 30) -> int:
        """Delete entries older than N days. Returns total rows deleted."""
        cutoff = datetime.utcnow().timestamp() - (days * 86400)
        with self._lock, self._get_conn() as conn, conn:
            c1 = conn.execute(
                "DELETE FROM posts WHERE strftime('%s', last_synced) < ?;", (cutoff,)
            ).rowcount
            c2 = conn.execute(
                "DELETE FROM comments WHERE strftime('%s', last_synced) < ?;", (cutoff,)
            ).rowcount
            return c1 + c2

    def get_stats(self) -> Dict[str, Any]:
        """Return basic counts for dashboard / monitoring."""
        with self._lock, self._get_conn() as conn:
            posts = conn.execute("SELECT COUNT(*) FROM posts;").fetchone()[0]
            comments = conn.execute("SELECT COUNT(*) FROM comments;").fetchone()[0]
            return {"posts": posts, "comments": comments}


if __name__ == "__main__":
    db = DB()
    db.save_post("reddit", "abc123", "lemmy_456", "testsub")
    db.save_comment("reddit", "com_789", "lemmy_890", "abc123", "lemmy_456")
    print(db.get_stats())
