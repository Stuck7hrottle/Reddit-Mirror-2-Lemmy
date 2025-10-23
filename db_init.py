#!/usr/bin/env python3
"""
db_init.py — Shared database initialization and schema migration
Ensures that all tables (jobs, posts, comments, db_meta) exist and match expected schema.
Can be safely imported and run multiple times.
"""

import os
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Default DB path (relative to container or project root)
BASE_DIR = Path(os.getenv("BASE_DIR", "/opt/Reddit-Mirror-2-Lemmy"))
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "jobs.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def ensure_column(cur, table: str, column: str, definition: str):
    """Add a column to a table if it doesn’t exist."""
    cur.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if column not in cols:
        logger.info(f"🛠️  Adding missing column '{column}' to '{table}'...")
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition};")


def init_database():
    """Ensure the database exists and schema is up to date."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()

    # ─────────────────────────────── JOBS TABLE ───────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            retries INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 5,
            next_run REAL,
            status TEXT DEFAULT 'queued',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()

    # Add missing job columns if needed
    ensure_column(cur, "jobs", "updated_at", "TEXT")

    # ─────────────────────────────── POSTS TABLE ───────────────────────────────
    cur.execute("""
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

    # ─────────────────────────────── COMMENTS TABLE ───────────────────────────────
    cur.execute("""
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

    # ─────────────────────────────── DB_META TABLE ───────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    conn.close()

    logger.info(f"✅ Database initialized and ready at {DB_PATH}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_database()
