import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import os

# Use DATA_DIR for container-friendly persistence (defaults to /app/data)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bridge_cache.db"


class DB:
    """
    Lightweight SQLite cache for Reddit → Lemmy bridge.
    Thread-safe, minimal dependencies, and auto-creates tables.
    """

    _lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path) if db_path else str(DB_PATH)
        self._init_db()

    def _get_conn(self):
        """Return a new SQLite connection with foreign keys enabled."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self):
        """Initialize tables if they don't exist."""
        with self._get_conn() as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reddit_id TEXT UNIQUE,
                    lemmy_id TEXT,
                    subreddit TEXT,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reddit_id TEXT UNIQUE,
                    lemmy_id TEXT,
                    parent_reddit_id TEXT,
                    parent_lemmy_id TEXT,
                    last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    # ─────────────────────────────── Posts ─────────────────────────────── #

    def save_post(self, reddit_id: str, lemmy_id: str, subreddit: Optional[str] = None) -> None:
        with self._lock, self._get_conn() as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO posts (reddit_id, lemmy_id, subreddit, last_synced)
                VALUES (?, ?, ?, ?);
                """,
                (reddit_id, lemmy_id, subreddit, datetime.utcnow()),
            )

    def get_lemmy_post_id(self, reddit_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT lemmy_id FROM posts WHERE reddit_id = ?;", (reddit_id,)
            ).fetchone()
            return row[0] if row else None

    # ─────────────────────────────── Comments ─────────────────────────────── #

    def save_comment(
        self,
        reddit_id: str,
        lemmy_id: str,
        parent_reddit_id: Optional[str] = None,
        parent_lemmy_id: Optional[str] = None,
    ) -> None:
        with self._lock, self._get_conn() as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO comments
                (reddit_id, lemmy_id, parent_reddit_id, parent_lemmy_id, last_synced)
                VALUES (?, ?, ?, ?, ?);
                """,
                (reddit_id, lemmy_id, parent_reddit_id, parent_lemmy_id, datetime.utcnow()),
            )

    def get_lemmy_comment_id(self, reddit_id: str) -> Optional[str]:
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                "SELECT lemmy_id FROM comments WHERE reddit_id = ?;", (reddit_id,)
            ).fetchone()
            return row[0] if row else None

    # ─────────────────────────────── Maintenance / Stats ────────────────── #

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
    db.save_post("r_demo", "l_123", "testsub")
    db.save_comment("c_demo", "lc_1", "r_demo", "l_123")
    print(db.get_stats())
