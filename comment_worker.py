#!/usr/bin/env python3
"""
comment_worker.py — universal comment mirror worker
---------------------------------------------------
Handles "mirror_comment" jobs for any supported platform
(e.g. Reddit → Lemmy, Mastodon → Lemmy, etc.)
by dynamically resolving the correct bridge.

This replaces the old comment_mirror.py logic,
and is compatible with your existing job queue / worker framework.
"""

import asyncio
import logging
from datetime import datetime

from job_queue import JobDB
from worker_base import WorkerBase
from core.bridge_registry import BridgeRegistry

logger = logging.getLogger(__name__)


class CommentWorker(WorkerBase):
    """Handles mirror_comment jobs via dynamic bridge lookup."""

    async def handle_job(self, job):
        payload = job.payload or {}
        job_id = job.id
        source = payload.get("source", "reddit").lower()
        destination = payload.get("destination", "lemmy").lower()

        reddit_post_id = payload.get("reddit_post_id") or payload.get("reddit_id")
        lemmy_post_id = payload.get("lemmy_post_id")

        if not reddit_post_id or not lemmy_post_id:
            logger.warning(f"⚠️ Incomplete comment job payload: {payload}")
            return

        logger.info(f"💬 [{source}→{destination}] job {job_id}: mirroring comments for post {reddit_post_id}")

        try:
            bridge = BridgeRegistry.get(source, destination)
            await bridge.mirror_comments(reddit_post_id, lemmy_post_id)
            logger.info(f"✅ Job {job_id}: mirrored comments for {reddit_post_id} → Lemmy {lemmy_post_id}")
            self.db.mark_complete(job_id)
        except Exception as e:
            logger.exception(f"❌ Job {job_id} failed: {e}")
            self.db.mark_failed(job_id, str(e))


async def main():
    logger.info("🚀 Starting CommentWorker (multi-source bridge mode)")
    db = JobDB()
    worker = CommentWorker(db=db, job_type="mirror_comment")

    while True:
        job = worker.db.fetch_next("mirror_comment")
        if not job:
            await asyncio.sleep(5)
            continue

        await worker.process_job(job)
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 CommentWorker stopped manually")
