import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class Job:
    type: str
    payload: Dict[str, Any]
    retries: int = 0
    max_retries: int = 5
    next_run: Any = field(default_factory=lambda: time.time())
    id: int = None  # ✅ new field for DB tracking

class BaseWorker:
    def __init__(self, name: str, concurrency: int = 2):
        self.name = name
        self.queue = asyncio.Queue()
        self.concurrency = concurrency
        self.active = True

    async def enqueue(self, job: Job):
        await self.queue.put(job)
        logger.info(f"[{self.name}] Enqueued job {job.type}")

    async def start(self):
        logger.info(f"[{self.name}] Starting with {self.concurrency} workers")
        tasks = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(self.concurrency)
        ]
        await asyncio.gather(*tasks)

    async def _worker_loop(self, worker_id: int):
        from worker_manager import WorkerManager  # safe local import
        manager = WorkerManager()  # used only for DB writes

        while self.active:
            job = await self.queue.get()
            now = time.time()

            # --- 🩹 Convert string timestamps to float seconds
            if isinstance(job.next_run, str):
                try:
                    # Try ISO 8601 first
                    dt = datetime.fromisoformat(job.next_run)
                except ValueError:
                    # Fallback to SQLite-style format
                    try:
                        dt = datetime.strptime(job.next_run, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        dt = datetime.utcnow()
                job.next_run = dt.timestamp()

            # --- ✅ Ensure non-null next_run
            if not job.next_run:
                job.next_run = time.time()

            # --- Standard scheduling delay
            if job.next_run > now:
                await asyncio.sleep(job.next_run - now)

            try:
                if job.id:
                    manager.mark_job_status(job.id, "in_progress")
                await self.process(job)
                logger.info(f"[{self.name}] Job {job.type} processed successfully")
                if job.id:
                    manager.mark_job_status(job.id, "done")
            except Exception as e:
                await self._handle_failure(job, e, manager)
            finally:
                self.queue.task_done()

    async def _handle_failure(self, job: Job, error: Exception, manager=None):
        job.retries += 1
        if job.retries > job.max_retries:
            logger.error(f"[{self.name}] Job {job.type} failed permanently: {error}")
            if job.id and manager:
                manager.mark_job_status(job.id, "failed")
        else:
            delay = 2 ** job.retries
            job.next_run = time.time() + delay
            logger.warning(
                f"[{self.name}] Retry {job.retries}/{job.max_retries} for {job.type} in {delay}s"
            )
            if job.id and manager:
                manager.mark_job_status(job.id, "queued")
            await self.queue.put(job)

    async def process(self, job: Job):
        """Override in subclass"""
        raise NotImplementedError("process() must be implemented by subclasses")

    def stop(self):
        self.active = False
        logger.info(f"[{self.name}] Worker stopped")
