#!/usr/bin/env python3
import asyncio
import logging
import signal
import os
from worker_manager import WorkerManager
from mirror_worker import MirrorWorker
from job_queue import manager  # shared global manager
from utils import write_status
from mirror_worker import LemmyCommentWorker, RedditCommentWorker

manager.register_worker("mirror_lemmy_comment", LemmyCommentWorker("mirror_lemmy_comment"))
manager.register_worker("mirror_reddit_comment", RedditCommentWorker("mirror_reddit_comment"))

# ───────────────────────────────
# Override write_status for dashboard (state.json compatibility)
# ───────────────────────────────
def write_status(state: str, posts_queued: int, comments_queued: int):
    """Write dashboard-friendly state.json instead of status.json."""
    import json
    from datetime import datetime
    from pathlib import Path

    data = {
        "mirror_status": state,
        "posts_queued": posts_queued,
        "comments_queued": comments_queued,
        "timestamp": datetime.utcnow().isoformat(),
    }

    path = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data")) / "state.json"
    path.write_text(json.dumps(data, indent=2))

# ───────────────────────────────
# Logging setup
# ───────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ───────────────────────────────
# Register workers globally
# ───────────────────────────────
manager.register_worker("mirror_post", MirrorWorker("mirror_post"))
manager.register_worker("mirror_comment", MirrorWorker("mirror_comment"))

print("✅ Registered mirror_post and mirror_comment workers")

# ───────────────────────────────
# Dashboard heartbeat updater
# ───────────────────────────────
async def monitor_status(manager: WorkerManager):
    """Periodically update dashboard state.json with live queue stats."""
    import sqlite3
    from pathlib import Path

    db_path = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data")) / "jobs.db"

    while True:
        try:
            queued_posts = queued_comments = running = 0

            if db_path.exists():
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()

                cur.execute("SELECT COUNT(*) FROM jobs WHERE status='queued' AND type='mirror_post'")
                queued_posts = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM jobs WHERE status='queued' AND type='mirror_comment'")
                queued_comments = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM jobs WHERE status='running'")
                running = cur.fetchone()[0]

                conn.close()

            write_status(
                "running" if running or queued_posts or queued_comments else "idle",
                queued_posts + running,
                queued_comments,
            )

        except Exception as e:
            logging.warning(f"[monitor_status] failed: {e}")

        await asyncio.sleep(30)

# ───────────────────────────────
# Main event loop
# ───────────────────────────────
async def main():
    stop_event = asyncio.Event()

    def handle_shutdown(*_):
        logging.warning("🛑 Received shutdown signal — stopping all workers...")
        asyncio.create_task(manager.stop_all())
        write_status("stopping", 0, 0)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    write_status("starting", 0, 0)
    await asyncio.gather(
        manager.start_all(),
        monitor_status(manager),
        stop_event.wait(),
    )

    write_status("stopped", 0, 0)
    logging.info("✅ All workers stopped cleanly.")

# ───────────────────────────────
# Entry point
# ───────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 Interrupted manually — shutting down.")
        write_status("stopped", 0, 0)
