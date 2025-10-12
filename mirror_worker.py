#!/usr/bin/env python3
import asyncio
import logging
from worker_base import BaseWorker
from auto_mirror import mirror_post_to_lemmy
from comment_mirror import mirror_comment_to_lemmy

logger = logging.getLogger(__name__)

class MirrorWorker(BaseWorker):
    """Handles Reddit → Lemmy mirroring jobs asynchronously."""

    async def process(self, job):
        job_type = job.type
        payload = job.payload

        try:
            if job_type == "mirror_post":
                await self._mirror_post(payload)
            elif job_type == "mirror_comment":
                await self._mirror_comment(payload)
            else:
                logger.warning(f"[{self.name}] Unknown job type: {job_type}")
        except Exception as e:
            logger.exception(f"[{self.name}] Error processing {job_type}: {e}")
            raise

    async def _mirror_post(self, payload):
        reddit_id = payload.get("reddit_id") or payload.get("reddit_post_id")
        if not reddit_id:
            raise ValueError(f"Missing reddit_id in payload: {payload}")

        logger.info(f"[{self.name}] Mirroring Reddit post {reddit_id}")
        result = await mirror_post_to_lemmy(payload)
        logger.info(f"[{self.name}] ✅ Mirrored post {reddit_id} → {result}")
        return result

    async def _mirror_comment(self, payload):
        reddit_post_id = payload.get("reddit_post_id") or payload.get("reddit_id")
        lemmy_post_id = payload.get("lemmy_post_id")

        if not reddit_post_id or not lemmy_post_id:
            logger.warning(f"⚠️ Incomplete payload for comment job: {payload}")
            return None

        result = await mirror_comment_to_lemmy(payload)
        logger.info(f"[{self.name}] ✅ Mirrored comments for {reddit_post_id} → Lemmy {lemmy_post_id}")
        return result


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("▶️ comment_mirror.py starting (refresh=False)")

    async def main():
        worker = MirrorWorker("mirror_worker")
        await worker.run_forever()  # Uses BaseWorker’s loop

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 MirrorWorker stopped manually.")
