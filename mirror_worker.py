#!/usr/bin/env python3
"""
mirror_worker.py — universal source→destination mirror worker
--------------------------------------------------------------
Handles "mirror_post" jobs by delegating to the appropriate bridge
(e.g., Reddit→Lemmy, Mastodon→Lemmy, etc.) via the BridgeRegistry.
"""

import asyncio
import logging
from datetime import datetime
from job_queue import JobDB
from worker_base import BaseWorker
from core.bridge_registry import BridgeRegistry

logger = logging.getLogger(__name__)


class MirrorWorker(BaseWorker):
    """Worker that executes mirror_post jobs using dynamic bridge lookup."""

    async def handle_job(self, job):
        payload = job.payload or {}
        job_id = job.id
        source = payload.get("source", "reddit").lower()
        destination = payload.get("destination", "lemmy").lower()

        reddit_id = payload.get("reddit_id") or payload.get("reddit_post_id")
        if not reddit_id:
            logger.error(f"⚠️ Missing reddit_id in job payload: {payload}")
            return

        logger.info(f"🔁 [{source}→{destination}] job {job_id}: mirroring post {reddit_id}")

        try:
            bridge = BridgeRegistry.get(source, destination)
            result = await bridge.mirror_post(reddit_id)
            logger.info(f"✅ Job {job_id}: mirrored post {reddit_id} → Lemmy {result['lemmy_id']}")
            self.db.mark_complete(job_id)
        except Exception as e:
            logger.exception(f"❌ Job {job_id} failed: {e}")
            self.db.mark_failed(job_id, str(e))


async def main():
    logger.info("🚀 Starting MirrorWorker (multi-source bridge mode)")
    db = JobDB()
    worker = MirrorWorker("mirror_worker")

    while True:
        job = worker.db.fetch_next("mirror_post")
        if not job:
            await asyncio.sleep(5)
            continue

        await worker.process_job(job)
        await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 MirrorWorker stopped manually")
