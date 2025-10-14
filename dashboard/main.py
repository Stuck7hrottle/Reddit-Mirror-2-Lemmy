from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json
import os

app = FastAPI(title="Reddit–Lemmy Mirror Dashboard")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data"))
LOG_FILE = DATA_DIR / "logs/bridge.log"
STATUS_FILE = DATA_DIR / "state.json"

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    stats = load_status()
    return templates.TemplateResponse("index.html", {"request": request, "stats": stats})


@app.get("/logs", response_class=HTMLResponse)
def show_logs(request: Request):
    lines = tail_log(LOG_FILE, 50)
    return templates.TemplateResponse("logs.html", {"request": request, "lines": lines})


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
