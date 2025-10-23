#!/usr/bin/env python3
"""
Auto Backfill Helper
─────────────────────
Shared utilities for first-run detection and flagging between
Reddit ↔ Lemmy comment sync scripts.

Provides:
  • is_first_run(db, flag_name="backfill_done")
  • mark_backfill_complete(db, flag_name="backfill_done")
"""

from utils import log, log_error


def is_first_run(db, flag_name="backfill_done"):
    """Return True if DB has posts but no comments, or flag is missing."""
    try:
        conn = db._get_conn()
        posts = conn.execute("SELECT COUNT(*) FROM posts;").fetchone()[0]
        comments = conn.execute("SELECT COUNT(*) FROM comments;").fetchone()[0]
        flag_row = conn.execute(
            "SELECT value FROM db_meta WHERE key=?;", (flag_name,)
        ).fetchone()
        conn.close()
        return (posts > 0 and comments == 0) or not flag_row
    except Exception as e:
        log_error(f"auto_backfill.is_first_run({flag_name})", e)
        return True


def mark_backfill_complete(db, flag_name="backfill_done"):
    """Mark a backfill as done in db_meta."""
    try:
        conn = db._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, 'true');",
            (flag_name,),
        )
        conn.commit()
        conn.close()
        log(f"✅ Backfill flag '{flag_name}' set successfully.")
    except Exception as e:
        log_error(f"auto_backfill.mark_backfill_complete({flag_name})", e)
