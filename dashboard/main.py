from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from collections import deque
from pathlib import Path
import os, sqlite3, asyncio
from datetime import datetime
import docker
import subprocess

# ─────────────────────────────────────────────
# CONFIG PATHS
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/opt/Reddit-Mirror-2-Lemmy"))
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_DIR / "data")))
LOG_FILE = Path(os.getenv("LOG_FILE", str(PROJECT_DIR / "logs" / "bridge.log")))
ENV_PATH = Path(os.getenv("ENV_PATH", str(PROJECT_DIR / ".env")))
COMPOSE_FILE = Path(os.getenv("COMPOSE_FILE_PATH", str(PROJECT_DIR / "docker-compose.yml")))
STATUS_FILE = DATA_DIR / "state.json"

# ─────────────────────────────────────────────
# MANAGED CONTAINERS
# The dashboard may only act on these containers. Anything else (Lemmy,
# its database, other stacks on the same host) is off-limits, even though
# the Docker socket would technically allow it.
# Maps container name -> docker compose service name.
# ─────────────────────────────────────────────
MANAGED_CONTAINERS = {
    "reddit_mirror_worker": "reddit-mirror",
    "reddit_mirror_refresh": "reddit-refresh",
    "lemmy_comment_sync": "lemmy_comment_sync",
    "reddit_comment_sync": "reddit_comment_sync",
}
CONTAINER_ACTIONS = {"start", "stop", "restart"}

# ─────────────────────────────────────────────
# DASHBOARD APP
# ─────────────────────────────────────────────
app = FastAPI(title="Reddit–Lemmy Mirror Dashboard")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# ✅ Mount static assets at /dashboard/static
app.mount("/dashboard/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ─────────────────────────────────────────────
# SECURITY HELPERS
# ─────────────────────────────────────────────
def require_dashboard_request(request: Request):
    """
    Block cross-site requests to the control endpoints (CSRF).

    Browsers re-send the dashboard's Basic Auth login automatically, so any
    web page you visit could otherwise POST to these endpoints in the
    background. A custom header can't be added cross-site without a CORS
    preflight, which this app never approves, so only the dashboard's own
    JavaScript can send it.
    """
    if request.headers.get("X-Requested-With") != "mirror-dashboard":
        raise HTTPException(status_code=403, detail="Missing dashboard request header")


def require_managed_container(name: str) -> str:
    if name not in MANAGED_CONTAINERS:
        raise HTTPException(status_code=404, detail=f"{name} is not managed by this dashboard")
    return name


_docker_client = None

def docker_client():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.DockerClient(base_url="unix://var/run/docker.sock")
    return _docker_client


def set_env_value(key: str, value: str):
    """Set KEY=value in .env, keeping every other line and its order intact."""
    if not ENV_PATH.exists():
        raise FileNotFoundError(f".env file not found at {ENV_PATH}")

    lines = ENV_PATH.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def get_env_value(key: str, default: str = "") -> str:
    if not ENV_PATH.exists():
        return default
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def compose(*args):
    """Run docker compose against the project's compose file."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_DIR))

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def tail_log(file_path, n=50):
    """Return the last n lines without loading the whole log into memory."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return list(deque(f, maxlen=n))
    except FileNotFoundError:
        return ["No logs found.\n"]

def get_stats():
    db_path = DATA_DIR / "jobs.db"
    stats = {"mirror_status": "running", "posts_queued": 0, "comments_queued": 0, "duplicates_skipped": 0, "videos_uploaded": 0, "posts_done": 0, "uptime": "?"}
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status;")
            for status, count in cur.fetchall():
                if status == "done":
                    stats["posts_done"] += count
                elif status == "queued":
                    stats["posts_queued"] += count
                elif status == "in_progress":
                    stats["posts_queued"] += count
                    stats["mirror_status"] = "working"
        except sqlite3.Error:
            stats["mirror_status"] = "db unavailable"
        finally:
            conn.close()

    if LOG_FILE.exists():
        lines = tail_log(LOG_FILE, 300)
        stats["duplicates_skipped"] = sum("Skipping duplicate" in l for l in lines)
        stats["videos_uploaded"] = sum("METRIC_VIDEO_UPLOAD_SUCCESS" in l for l in lines)

    try:
        start_time = datetime.fromtimestamp(DATA_DIR.stat().st_ctime)
        delta = datetime.now() - start_time
        stats["uptime"] = f"{delta.days}d {delta.seconds // 3600}h"
    except Exception:
        pass
    return stats


def cpu_percent_from_stats(stats: dict) -> float:
    """
    Docker's CPU % formula. Works on cgroup v2 hosts, where per-CPU usage
    (percpu_usage) is no longer reported and the old calculation always gave 0.
    """
    cpu = stats.get("cpu_stats", {})
    pre = stats.get("precpu_stats", {})
    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage") or []) or 1
    if sys_delta <= 0 or cpu_delta < 0:
        return 0.0
    return round(cpu_delta / sys_delta * online * 100, 2)

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.get("/dashboard/", response_class=HTMLResponse)
def index(request: Request):
    stats = get_stats()
    return templates.TemplateResponse(request, "index.html", {"stats": stats})

@app.get("/dashboard/logs", response_class=HTMLResponse)
def show_logs(request: Request):
    lines = tail_log(LOG_FILE, 50)
    return templates.TemplateResponse(request, "logs.html", {"lines": lines})

@app.get("/dashboard/metrics")
def metrics():
    return get_stats()

@app.get("/dashboard/health", response_class=HTMLResponse)
def get_worker_health_html(request: Request):
    """Render Docker container stats as HTML for the dashboard (HTMX)."""
    client = docker_client()
    info = []

    for name in MANAGED_CONTAINERS:
        try:
            c = client.containers.get(name)
            stats = c.stats(stream=False)

            # ─── MEMORY USAGE ─────────────────────────────────────────────
            mem = stats.get("memory_stats", {})
            mem_usage = mem.get("usage", 0) / (1024 * 1024)
            mem_limit = (mem.get("limit") or 1) / (1024 * 1024)
            mem_percent = round((mem_usage / mem_limit) * 100, 1)

            # ─── Check ENABLE_LEMMY_COMMENT_SYNC flag ─────────────────────
            sync_state = None
            if name == "lemmy_comment_sync":
                enabled = get_env_value("ENABLE_LEMMY_COMMENT_SYNC", "false").lower() == "true"
                sync_state = "active" if enabled else "disabled"

            info.append({
                "name": name,
                "status": c.status,
                "cpu_percent": cpu_percent_from_stats(stats),
                "mem_mb": round(mem_usage, 1),
                "mem_percent": mem_percent,
                "uptime": c.attrs["State"]["StartedAt"],
                "sync_state": sync_state
            })
        except docker.errors.NotFound:
            info.append({"name": name, "status": "not found"})
        except Exception as e:
            info.append({"name": name, "status": f"error: {e}"})

    return templates.TemplateResponse(request, "partials/worker_health.html", {"containers": info})

# NOTE: the build route must be registered before the generic {action} route,
# otherwise "/build" is captured as an action and rejected.
@app.post("/dashboard/container/{name}/build", dependencies=[Depends(require_dashboard_request)])
def build_container(name: str, no_cache: bool = False):
    """
    Rebuild a worker's image and (re)create its container from docker-compose.yml.
    """
    require_managed_container(name)
    service = MANAGED_CONTAINERS[name]

    build_args = ["build", service]
    if no_cache:
        build_args.append("--no-cache")

    try:
        result = compose(*build_args)
        if result.returncode != 0:
            return {"error": f"❌ Build failed for {service}", "details": result.stderr or result.stdout}

        result = compose("up", "-d", service)
        if result.returncode != 0:
            return {"error": f"❌ Built {service}, but it failed to start", "details": result.stderr or result.stdout}

        return {"success": f"✅ Built and started {service}"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/dashboard/container/{name}/{action}", dependencies=[Depends(require_dashboard_request)])
def control_container(name: str, action: str):
    """
    Start/stop/restart a managed worker container.
    For lemmy_comment_sync, start/stop is also persisted to .env.
    """
    require_managed_container(name)
    if action not in CONTAINER_ACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported action")

    try:
        c = docker_client().containers.get(name)
        getattr(c, action)()

        # 🧠 Persist state for lemmy_comment_sync
        if name == "lemmy_comment_sync" and action in ("start", "stop"):
            set_env_value("ENABLE_LEMMY_COMMENT_SYNC", "true" if action == "start" else "false")

        return {"success": f"{name} {action} executed"}

    except docker.errors.NotFound:
        return {"error": f"Container {name} not found"}
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────
# TOGGLE COMMENT SYNC (Enable/Disable)
# ─────────────────────────────────────────────
@app.post("/dashboard/container/toggle-comment-sync", dependencies=[Depends(require_dashboard_request)])
def toggle_comment_sync():
    """
    Toggle ENABLE_LEMMY_COMMENT_SYNC in .env, rebuild container, and restart it.
    """
    if not ENV_PATH.exists():
        return {"error": ".env not found"}

    current = get_env_value("ENABLE_LEMMY_COMMENT_SYNC", "false").lower()
    new_value = "false" if current == "true" else "true"
    set_env_value("ENABLE_LEMMY_COMMENT_SYNC", new_value)

    # 🧱 Rebuild and recreate the container to apply env change
    try:
        result = compose("up", "-d", "--build", "--force-recreate", "lemmy_comment_sync")
        if result.returncode != 0:
            return {"error": result.stderr or result.stdout}
    except Exception as e:
        return {"error": str(e)}

    return {"success": f"Comment sync {'ENABLED' if new_value == 'true' else 'DISABLED'}", "state": new_value}

# ─────────────────────────────────────────────
# WEBSOCKET: Live Log Stream
# Lives under /dashboard/ so the same nginx location (and its login) covers it.
# ─────────────────────────────────────────────
@app.websocket("/dashboard/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    path = LOG_FILE
    if not path.exists():
        await websocket.send_text("No logs found.\n")
        await websocket.close()
        return

    # Start with recent history so the page isn't blank until the next log line
    await websocket.send_text("".join(tail_log(path, 100)))
    last_size = path.stat().st_size

    try:
        while True:
            await asyncio.sleep(1)
            if not path.exists():
                continue
            current_size = path.stat().st_size
            if current_size < last_size:
                # Log was rotated or truncated: start again from the top
                last_size = 0
            if current_size > last_size:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_size)
                    new_data = f.read()
                await websocket.send_text(new_data)
                last_size = current_size
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"⚠️ WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
