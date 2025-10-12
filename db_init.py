"""
db_init.py — Shared database initialization and schema migration
Ensures that the jobs table and all necessary columns exist.
Can be safely imported and run multiple times.
"""

import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

# Default DB path (relative to project root)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "data", "jobs.db"))


def init_database():
    """Ensure the jobs database exists and schema is up to date."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()

    # --- Step 1: Create base jobs table ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            retries INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 5,
            next_run REAL,
            status TEXT DEFAULT 'queued'
        )
        """
    )
    conn.commit()

    # --- Step 2: Perform column migrations ---
    cur.execute("PRAGMA table_info(jobs)")
    existing_cols = [r[1] for r in cur.fetchall()]

    # Add created_at if missing
    if "created_at" not in existing_cols:
        logger.warning("🛠️  Adding 'created_at' column to jobs table (migration)...")
        cur.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")

    # Add updated_at if you want to track changes later
    if "updated_at" not in existing_cols:
        logger.info("🛠️  Adding 'updated_at' column to jobs table (migration)...")
        cur.execute("ALTER TABLE jobs ADD COLUMN updated_at TEXT")

    conn.commit()

    # --- Step 3: Create db_meta table for versioning ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS db_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        )
        """
    )
    conn.commit()

    # Check and set schema version
    cur.execute("SELECT version FROM db_meta WHERE id = 1")
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO db_meta (id, version) VALUES (1, 1)")
        conn.commit()
        logger.info("📦 Initialized db_meta with version 1")

    conn.close()
    logger.info("✅ Database initialized and ready at %s", DB_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_database()
