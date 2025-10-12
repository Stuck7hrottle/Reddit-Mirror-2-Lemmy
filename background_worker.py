# background_worker.py
import asyncio
import logging
from worker_manager import WorkerManager
from mirror_worker import MirrorWorker
from job_queue import manager  # shared global manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# Register workers once, globally
manager.register_worker("mirror_post", MirrorWorker("mirror_post"))
manager.register_worker("mirror_comment", MirrorWorker("mirror_comment"))

print("✅ Registered mirror_post and mirror_comment workers")

async def main():
    # Just start the shared global manager
    await manager.start_all()

if __name__ == "__main__":
    asyncio.run(main())
