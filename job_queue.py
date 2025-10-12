from __future__ import annotations
import asyncio
from typing import Optional, Dict, Any
from worker_manager import WorkerManager
import os
import sqlite3
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "jobs.db")

# Shared WorkerManager across the app
manager = WorkerManager()

# ----------------------------
# ✅ Public enqueue helpers
# ----------------------------
async def enqueue_post(reddit_post_id: str):
    """Queue a background job to mirror a Reddit post."""
    payload = {"reddit_id": reddit_post_id}
    await manager.enqueue_job("mirror_post", payload)

async def enqueue_comment(reddit_comment_id: str, reddit_post_id: Optional[str] = None, lemmy_post_id: Optional[int] = None):
    """Queue a background job to mirror a Reddit comment (structured payload)."""
    payload: Dict[str, Any] = {"reddit_comment_id": reddit_comment_id}
    if reddit_post_id:
        payload["reddit_id"] = reddit_post_id
    if lemmy_post_id:
        payload["lemmy_post_id"] = lemmy_post_id

    await manager.enqueue_job("mirror_comment", payload)


# ----------------------------
# ✅ Unified Job & Mapping Database Helper
# ----------------------------
from typing import Optional
import json
import sqlite3
from datetime import datetime
from pathlib import Path


class JobDB:
    """
    A unified lightweight database helper for managing:
      - Reddit ↔ Lemmy post and comment mappings
      - Background job queue operations
    """

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        if conn is None:
            db_path = Path(__file__).parent / "data" / "jobs.db"
            conn = sqlite3.connect(db_path)

        self.conn = conn
        self.cursor = conn.cursor()
        self._init_schema()

    # ------------------------------------------------------------
    # 🔧 Schema setup
    # ------------------------------------------------------------
    def _init_schema(self) -> None:
        """Ensure all required tables exist."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reddit_post_id TEXT UNIQUE NOT NULL,
                lemmy_post_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reddit_comment_id TEXT UNIQUE NOT NULL,
                reddit_post_id TEXT,
                lemmy_comment_id INTEGER,
                lemmy_post_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'queued',
                retries INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.commit()

    # ------------------------------------------------------------
    # 🧩 Post mapping helpers
    # ------------------------------------------------------------
    def get_lemmy_post_id(self, reddit_post_id: str) -> Optional[int]:
        """Return Lemmy post ID for a Reddit post."""
        row = self.conn.execute(
            "SELECT lemmy_post_id FROM posts WHERE reddit_post_id = ?",
            (reddit_post_id,),
        ).fetchone()
        return row[0] if row else None

    def post_exists(self, reddit_post_id: str) -> bool:
        """Check if a Reddit post mapping already exists."""
        row = self.conn.execute(
            "SELECT 1 FROM posts WHERE reddit_post_id = ? LIMIT 1",
            (reddit_post_id,)
        ).fetchone()
        return row is not None

    def record_post_mapping(self, reddit_post_id: str, lemmy_post_id: int) -> None:
        """Save or update Reddit ↔ Lemmy post mapping."""
        self.conn.execute(
            "INSERT OR REPLACE INTO posts (reddit_post_id, lemmy_post_id) VALUES (?, ?)",
            (reddit_post_id, lemmy_post_id)
        )
        self.conn.commit()

    # ------------------------------------------------------------
    # 💬 Comment mapping helpers
    # ------------------------------------------------------------
    def get_parent_post_id(self, reddit_comment_id: str) -> Optional[str]:
        """Get Reddit post ID that a comment belongs to."""
        row = self.conn.execute(
            "SELECT reddit_post_id FROM comments WHERE reddit_comment_id = ?",
            (reddit_comment_id,),
        ).fetchone()
        return row[0] if row else None

    def get_lemmy_comment_id(self, reddit_comment_id: str) -> Optional[int]:
        """Return Lemmy comment ID for a given Reddit comment."""
        row = self.conn.execute(
            "SELECT lemmy_comment_id FROM comments WHERE reddit_comment_id = ?",
            (reddit_comment_id,),
        ).fetchone()
        return row[0] if row else None

    def comment_exists(self, reddit_comment_id: str) -> bool:
        """Check if a Reddit comment already exists in the DB."""
        cur = self.conn.execute(
            "SELECT 1 FROM comments WHERE reddit_comment_id = ? LIMIT 1",
            (reddit_comment_id,)
        )
        return cur.fetchone() is not None

    def record_comment_mapping(
        self,
        reddit_comment_id: str,
        lemmy_comment_id: int,
        reddit_post_id: Optional[str] = None,
        lemmy_post_id: Optional[int] = None,
    ) -> None:
        """Record or update a comment mapping between Reddit and Lemmy."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO comments (
                reddit_comment_id, reddit_post_id, lemmy_comment_id, lemmy_post_id
            ) VALUES (?, ?, ?, ?)
            """,
            (reddit_comment_id, reddit_post_id, lemmy_comment_id, lemmy_post_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------
    # ⚙️ Job Queue Helpers
    # ------------------------------------------------------------
    def enqueue(self, job_type: str, payload: dict, status: str = "queued") -> None:
        """Add a new background job to the queue."""
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO jobs (type, payload, status, retries, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_type,
                json.dumps(payload),
                status,
                0,
                now,
                now,
            ),
        )
        self.conn.commit()
        print(f"✅ Enqueued job type={job_type} ({payload})")

    # ------------------------------------------------------------
    # 🧩 Legacy compatibility
    # ------------------------------------------------------------
    def save_comment(self, reddit_comment_id, lemmy_comment_id, reddit_post_id=None, lemmy_post_id=None):
        """Legacy alias for record_comment_mapping to support older comment_mirror.py versions."""
        return self.record_comment_mapping(
            reddit_comment_id=reddit_comment_id,
            lemmy_comment_id=lemmy_comment_id,
            reddit_post_id=reddit_post_id,
            lemmy_post_id=lemmy_post_id,
        )
