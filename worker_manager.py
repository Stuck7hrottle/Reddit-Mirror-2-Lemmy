import asyncio
import signal
import logging
import sqlite3
import json
import time
import os
from typing import Dict
from worker_base import Job, BaseWorker

# --- NEW: import shared DB initializer ---
from db_init import init_database

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "jobs.db")


class WorkerManager:
    def __init__(self):
        # ✅ Ensure DB exists and is migrated before connecting
        init_database()

        self.workers: Dict[str, BaseWorker] = {}
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._create_tables()  # safety redundancy
        self._stop_event = None

    # ───────────────────────────────
    # Database setup & persistence
    # ───────────────────────────────
    def _create_tables(self):
        cur = self.db.cursor()
        # Create the base table if it doesn't exist
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
        self.db.commit()

        # --- Schema migration check ---
        cur.execute("PRAGMA table_info(jobs)")
        existing_cols = [r[1] for r in cur.fetchall()]

        if "created_at" not in existing_cols:
            print("🛠️  Adding 'created_at' column to jobs table (migration)...", flush=True)
            cur.execute("ALTER TABLE jobs ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
            self.db.commit()

    def save_job(self, job: Job) -> int:
        cur = self.db.cursor()
        cur.execute(
            """
            INSERT INTO jobs (type, payload, retries, max_retries, next_run, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                job.type,
                json.dumps(job.payload),
                job.retries,
                job.max_retries,
                job.next_run,
                "queued",
            ),
        )
        self.db.commit()
        return cur.lastrowid

    def mark_job_status(self, job_id: int, status: str):
        """Update a job's status in the database."""
        try:
            cur = self.db.cursor()
            cur.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
            self.db.commit()
            logger.info(f"Updated job {job_id} → {status}")
        except Exception as e:
            logger.error(f"Failed to update job {job_id} status: {e}")

    def load_queued_jobs(self):
        cur = self.db.cursor()
        cur.execute(
            "SELECT id, type, payload, retries, max_retries, next_run FROM jobs WHERE status IN ('queued', 'retrying')"
        )
        rows = cur.fetchall()
        jobs = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                payload = {}
            job = Job(
                type=row["type"],
                payload=payload,
                retries=row["retries"],
                max_retries=row["max_retries"],
                next_run=row["next_run"],
                id=row["id"],
            )
            jobs.append(job)
        return jobs

    # ───────────────────────────────
    # Worker registration & management
    # ───────────────────────────────
    def register_worker(self, worker_type: str, worker: BaseWorker):
        if worker_type in self.workers:
            raise ValueError(f"Worker type '{worker_type}' already registered")
        self.workers[worker_type] = worker
        logger.info(f"Registered worker: {worker_type}")

    async def enqueue_job(self, job_type: str, payload: dict):
        if job_type not in self.workers:
            raise ValueError(f"No worker registered for job type '{job_type}'")
        job = Job(type=job_type, payload=payload)
        job.id = self.save_job(job)
        await self.workers[job_type].enqueue(job)

    def enqueue_job_direct(self, job_type: str, payload: dict):
        job = Job(type=job_type, payload=payload, next_run=time.time())
        job.id = self.save_job(job)
        print(f"✅ Directly saved job {job_type} (id={job.id}) to DB")

    # ───────────────────────────────
    # Lifecycle controls
    # ───────────────────────────────
    async def start_all(self):
        logger.info("Starting all workers...")
        for worker in self.workers.values():
            asyncio.create_task(worker.start())

        # Continuous polling loop to pick up new jobs
        self._stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self.stop_all()))

        try:
            while not self._stop_event.is_set():
                jobs = self.load_queued_jobs()
                for job in jobs:
                    if job.type in self.workers:
                        await self.workers[job.type].enqueue(job)
                        self.mark_job_status(job.id, "in_progress")
                        logger.info(f"Queued job {job.id} → worker {job.type}")
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def stop_all(self):
        logger.info("Shutting down workers...")
        for worker in self.workers.values():
            worker.stop()
        if self._stop_event:
            self._stop_event.set()
        self.db.close()
        logger.info("✅ WorkerManager stopped cleanly.")
