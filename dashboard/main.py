from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json
import os
import sqlite3
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG PATHS
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data"))
LOG_FILE = Path(os.getenv("LOG_FILE", "/opt/Reddit-Mirror-2-Lemmy/logs/bridge.log"))
STATUS_FILE = DATA_DIR / "state.json"

# ─────────────────────────────────────────────
# DASHBOARD APP
# ─────────────────────────────────────────────
app = FastAPI(title="Reddit–Lemmy Mirror Dashboard")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def tail_log(file_path, n=50):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.readlines()[-n:]
    except FileNotFoundError:
        return ["No logs found.\n"]

def load_status():
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text())
    except Exception:
        pass
    return {"mirror_status": "unknown", "posts_queued": 0, "comments_queued": 0, "uptime": "?"}

# ─────────────────────────────────────────────
# LIVE STATS
# ─────────────────────────────────────────────
def get_stats():
    db_path = DATA_DIR / "jobs.db"
    stats = {
        "mirror_status": "running",
        "posts_queued": 0,
        "comments_queued": 0,
        "duplicates_skipped": 0,
        "videos_uploaded": 0,   # 🆕 new metric
        "posts_done": 0,
        "uptime": "?",
    }

    # 🧱 Load counts from jobs.db
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status;")
        for status, count in cur.fetchall():
            if status == "done":
                stats["posts_done"] += count
            elif status == "queued":
                stats["posts_queued"] += count
            elif status == "in_progress":
                stats["posts_queued"] += count  # 👈 include active jobs too
                stats["mirror_status"] = "working"
        conn.close()

    # 🪶 Parse duplicates skipped from logs (quick scan)
    if LOG_FILE.exists():
        try:
            lines = tail_log(LOG_FILE, 300)
            stats["duplicates_skipped"] = sum("Skipping duplicate" in l for l in lines)

            # 🎬 Count successful video uploads
            stats["videos_uploaded"] = sum("METRIC_VIDEO_UPLOAD_SUCCESS" in l for l in lines)
        except Exception:
            pass

    # 🕒 Approximate uptime
    try:
        start_time = datetime.fromtimestamp(DATA_DIR.stat().st_ctime)
        delta = datetime.now() - start_time
        stats["uptime"] = f"{delta.days}d {delta.seconds // 3600}h"
    except Exception:
        pass

    return stats

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    stats = get_stats()
    return templates.TemplateResponse("index.html", {"request": request, "stats": stats})

@app.get("/logs", response_class=HTMLResponse)
def show_logs(request: Request):
    lines = tail_log(LOG_FILE, 50)
    return templates.TemplateResponse("logs.html", {"request": request, "lines": lines})

@app.get("/metrics")
def metrics():
    """Optional JSON metrics for external monitoring"""
    return get_stats()

from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import os

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """Stream bridge.log lines live to the dashboard with graceful disconnects."""
    await websocket.accept()
    path = LOG_FILE

    if not path.exists():
        await websocket.send_text("No logs found.\n")
        await websocket.close()
        return

    try:
        # Move to the end of the log file so we only stream new lines
        with open(path, "r", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            last_size = path.stat().st_size

        while True:
            await asyncio.sleep(1)
            if not path.exists():
                continue

            current_size = path.stat().st_size
            if current_size > last_size:
                with open(path, "r", encoding="utf-8") as f:
                    f.seek(last_size)
                    new_data = f.read()
                try:
                    await websocket.send_text(new_data)
                except WebSocketDisconnect:
                    print("🔌 Client disconnected from /ws/logs")
                    break
                last_size = current_size

    except Exception as e:
        print(f"⚠️ WebSocket error in /ws/logs: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
