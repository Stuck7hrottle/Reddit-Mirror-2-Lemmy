import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import os

# Use DATA_DIR for container-friendly persistence
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path("/opt/Reddit-Mirror-2-Lemmy/data/jobs.db")


class DB:
    """
    Lightweight SQLite cache for Reddit ↔ Lemmy bridge.
    Thread-safe and automatically migrates schema for new fields.
    """

    _lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path) if db_path else str(DB_PATH)
        self._init_db()

    def _get_conn(self):
        """Return a new SQLite connection with foreign keys enabled."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    # ─────────────────────────────── DB Initialization ─────────────────────────────── #
    def _init_db(self):
        """Ensure tables exist (safe for repeated initialization)."""
        with self._get_conn() as conn, conn:
            # posts table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reddit_id TEXT UNIQUE,
                    lemmy_id TEXT,
                    subreddit TEXT,
                    source TEXT DEFAULT 'reddit',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # comments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reddit_id TEXT UNIQUE,
                    lemmy_id TEXT,
                    parent_reddit_id TEXT,
                    parent_lemmy_id TEXT,
                    source TEXT DEFAULT 'reddit',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # db_meta table (for version flags)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS db_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # ignored_posts table (for permanently skipped Reddit posts)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ignored_posts (
                    reddit_id TEXT PRIMARY KEY,
                    reason TEXT DEFAULT 'forbidden',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

    # ─────────────────────────────── Post Helpers ─────────────────────────────── #
    def save_post(self, reddit_id: str, lemmy_id: str, subreddit: str = "", source="reddit"):
        with self._lock, self._get_conn() as conn, conn:
            conn.execute("""
                INSERT OR REPLACE INTO posts (reddit_id, lemmy_id, subreddit, source, last_synced)
                VALUES (?, ?, ?, ?, ?)
            """, (reddit_id, lemmy_id, subreddit, source, datetime.utcnow()))

    def get_lemmy_post_id(self, reddit_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute("SELECT lemmy_id FROM posts WHERE reddit_id = ?;", (reddit_id,)).fetchone()
            return row["lemmy_id"] if row else None

    def get_reddit_post_id(self, lemmy_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute("SELECT reddit_id FROM posts WHERE lemmy_id = ?;", (lemmy_id,)).fetchone()
            return row["reddit_id"] if row else None

    # ─────────────────────────────── Ignored Post Helpers ─────────────────────────────── #
    def mark_post_ignored(self, reddit_id: str, reason: str = "forbidden"):
        """Mark a Reddit post as permanently ignored (deleted/forbidden)."""
        with self._lock, self._get_conn() as conn, conn:
            conn.execute("""
                INSERT OR REPLACE INTO ignored_posts (reddit_id, reason, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP);
            """, (reddit_id, reason))

    def is_post_ignored(self, reddit_id: str) -> bool:
        """Return True if a Reddit post is permanently ignored."""
        with self._lock, self._get_conn() as conn:
            row = conn.execute("SELECT 1 FROM ignored_posts WHERE reddit_id = ?;", (reddit_id,)).fetchone()
            return bool(row)

    def get_ignored_posts(self) -> list[str]:
        """Return a list of all permanently ignored Reddit post IDs."""
        with self._lock, self._get_conn() as conn:
            rows = conn.execute("SELECT reddit_id FROM ignored_posts;").fetchall()
            return [r["reddit_id"] for r in rows]

    # ─────────────────────────────── Comment Helpers ─────────────────────────────── #
    def save_comment(
        self,
        reddit_id: str,
        lemmy_id: str,
        parent_reddit_id: Optional[str] = None,
        parent_lemmy_id: Optional[str] = None,
        source: str = "reddit",
    ):
        with self._lock, self._get_conn() as conn, conn:
            conn.execute("""
                INSERT OR REPLACE INTO comments
                (reddit_id, lemmy_id, parent_reddit_id, parent_lemmy_id, source, last_synced)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (reddit_id, lemmy_id, parent_reddit_id, parent_lemmy_id, source, datetime.utcnow()))

    def get_lemmy_comment_id(self, reddit_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute("SELECT lemmy_id FROM comments WHERE reddit_id = ?;", (reddit_id,)).fetchone()
            return row["lemmy_id"] if row else None

    def get_reddit_comment_id(self, lemmy_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute("SELECT reddit_id FROM comments WHERE lemmy_id = ?;", (lemmy_id,)).fetchone()
            return row["reddit_id"] if row else None

    # ─────────────────────────────── Maintenance / Stats ─────────────────────────────── #
    def purge_old(self, days: int = 30) -> int:
        """Delete entries older than N days."""
        cutoff = datetime.utcnow().timestamp() - (days * 86400)
        with self._lock, self._get_conn() as conn, conn:
            c1 = conn.execute("DELETE FROM posts WHERE strftime('%s', last_synced) < ?;", (cutoff,)).rowcount
            c2 = conn.execute("DELETE FROM comments WHERE strftime('%s', last_synced) < ?;", (cutoff,)).rowcount
            return c1 + c2

    def get_stats(self) -> Dict[str, Any]:
        """Return basic counts for dashboard / monitoring."""
        with self._lock, self._get_conn() as conn:
            posts = conn.execute("SELECT COUNT(*) FROM posts;").fetchone()[0]
            comments = conn.execute("SELECT COUNT(*) FROM comments;").fetchone()[0]
            return {"posts": posts, "comments": comments}


if __name__ == "__main__":
    db = DB()
    db.save_post("r_demo", "l_123", "testsub", source="reddit")
    db.save_comment("c_demo", "lc_1", "r_demo", "l_123", source="lemmy")
    print(db.get_stats())
