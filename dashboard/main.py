from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os, re, sqlite3, asyncio, threading, time
import docker
import subprocess

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# ─────────────────────────────────────────────
# CONFIG PATHS
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/opt/Reddit-Mirror-2-Lemmy"))
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_DIR / "data")))
LOG_FILE = Path(os.getenv("LOG_FILE", str(PROJECT_DIR / "logs" / "bridge.log")))
ERROR_LOG_FILE = Path(os.getenv("ERROR_LOG_FILE", str(LOG_FILE.parent / "errors.log")))
ENV_PATH = Path(os.getenv("ENV_PATH", str(PROJECT_DIR / ".env")))
COMPOSE_FILE = Path(os.getenv("COMPOSE_FILE_PATH", str(PROJECT_DIR / "docker-compose.yml")))
STATUS_FILE = DATA_DIR / "state.json"

# How much of the end of each log file to scan for the last-24-hours figures
LOG_SCAN_BYTES = int(os.getenv("DASHBOARD_LOG_SCAN_BYTES", str(8 * 1024 * 1024)))
# No post or comment for this long and the overview says the mirror is quiet
QUIET_AFTER = timedelta(hours=int(os.getenv("DASHBOARD_QUIET_HOURS", "2")))

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
    "reddit_comment_sync": "reddit_comment_sync",
    "lemmy_comment_sync": "lemmy_comment_sync",
}
CONTAINER_ACTIONS = {"start", "stop", "restart"}

# Plain-language names for the worker list
WORKER_INFO = {
    "reddit_mirror_worker": ("Post worker", "Works through the queue of posts to mirror"),
    "reddit_mirror_refresh": ("Reddit check", "Looks for new and edited posts on each subreddit"),
    "reddit_comment_sync": ("Reddit → Lemmy comments", "Copies new Reddit comments onto mirrored posts"),
    "lemmy_comment_sync": ("Lemmy → Reddit comments", "Copies Lemmy replies back to Reddit"),
}

# ─────────────────────────────────────────────
# DASHBOARD APP
# ─────────────────────────────────────────────
app = FastAPI(title="Reddit–Lemmy Mirror Dashboard")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# Bump when CSS/JS/fonts change: nginx tells browsers to cache /dashboard/static for 7 days
templates.env.globals["static_version"] = "2"
templates.env.filters["thousands"] = lambda n: f"{n:,}" if isinstance(n, int) else n
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


def comment_sync_enabled() -> bool:
    return get_env_value("ENABLE_LEMMY_COMMENT_SYNC", "false").lower() == "true"


def compose(*args):
    """Run docker compose against the project's compose file."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_DIR))

# ─────────────────────────────────────────────
# TIME HELPERS
# ─────────────────────────────────────────────
def log_now() -> datetime:
    """
    'Now' in the same clock the workers write their logs in. Workers log
    local time, so honour a TZ set in .env; otherwise the containers are UTC.
    """
    tz_name = get_env_value("TZ") or os.getenv("TZ", "")
    if tz_name and ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now()


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def humanize_duration(delta: timedelta, parts: int = 2) -> str:
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "under a minute"
    units = [("day", 86400), ("hour", 3600), ("minute", 60)]
    out = []
    for name, size in units:
        value, seconds = divmod(seconds, size)
        if value:
            out.append(plural(value, name))
        if len(out) == parts:
            break
    return ", ".join(out)


def humanize_ago(when, now: datetime) -> str | None:
    if when is None:
        return None
    delta = now - when
    if delta.total_seconds() < 60:
        return "just now"
    return f"{humanize_duration(delta, parts=1)} ago"


def parse_docker_time(value: str):
    """Docker reports e.g. 2026-10-23T00:51:27.780239667Z (UTC, nanoseconds)."""
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None

# ─────────────────────────────────────────────
# LOG HELPERS
# ─────────────────────────────────────────────
LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| ?(.*)$")
ERROR_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| ERROR in (.+)$")


def tail_log(file_path, n=50):
    """Return the last n lines without loading the whole log into memory."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return list(deque(f, maxlen=n))
    except FileNotFoundError:
        return ["No logs found.\n"]


def read_tail_lines(path: Path, max_bytes: int = LOG_SCAN_BYTES) -> list[str]:
    """Lines from the last max_bytes of a file (cheap even for huge logs)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except FileNotFoundError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # first line is probably cut in half
    return lines


def classify(message: str):
    if message.startswith("✅ Posted"):
        return "post"
    if message.startswith("💬 Mirrored"):
        return "comment"
    return None


def log_activity(now: datetime):
    """Hourly post/comment counts for the last 24 hours, read from bridge.log."""
    window_start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
    posts, comments = [0] * 24, [0] * 24
    last = {"post": None, "comment": None, "any": None}

    for line in read_tail_lines(LOG_FILE):
        m = LOG_LINE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        last["any"] = ts
        kind = classify(m.group(2))
        if kind is None:
            continue
        last[kind] = ts
        if ts < window_start or ts > now + timedelta(minutes=5):
            continue
        slot = min(23, int((ts - window_start).total_seconds() // 3600))
        (posts if kind == "post" else comments)[slot] += 1

    hours = [(window_start + timedelta(hours=i)).strftime("%H") for i in range(24)]
    return {"hours": hours, "posts": posts, "comments": comments}, last


def recent_errors(now: datetime):
    """Errors logged by log_error() in the last 24 hours, newest last."""
    since = now - timedelta(hours=24)
    count, latest = 0, None
    for line in read_tail_lines(ERROR_LOG_FILE, max_bytes=2 * 1024 * 1024):
        m = ERROR_LINE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts >= since:
            count += 1
            latest = (ts, m.group(2).strip())
    return count, latest

# ─────────────────────────────────────────────
# DATABASE + DOCKER HELPERS
# ─────────────────────────────────────────────
def db_stats():
    db_path = DATA_DIR / "jobs.db"
    out = {
        "available": False, "posts": 0, "comments": 0,
        "queued_posts": 0, "queued_comments": 0, "queued_other": 0,
        "running": 0, "failed": 0, "jobs_done": 0,
    }
    if not db_path.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return out
    try:
        cur = conn.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "posts" in tables:
            out["posts"] = cur.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        if "comments" in tables:
            out["comments"] = cur.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        if "jobs" in tables:
            for job_type, status, count in cur.execute(
                "SELECT type, status, COUNT(*) FROM jobs GROUP BY type, status"
            ):
                if status == "queued":
                    key = {"mirror_post": "queued_posts", "mirror_comment": "queued_comments"}.get(job_type, "queued_other")
                    out[key] += count
                elif status in ("running", "in_progress"):
                    out["running"] += count
                elif status == "failed":
                    out["failed"] += count
                elif status == "done":
                    out["jobs_done"] += count
        out["available"] = True
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    # Older installs only tracked jobs, not a posts table
    if out["posts"] == 0 and out["jobs_done"]:
        out["posts"] = out["jobs_done"]
    return out


def worker_states():
    """Name -> (status, started_at) for each managed worker, without CPU stats."""
    states = {}
    try:
        client = docker_client()
    except Exception:
        return {name: ("unknown", None) for name in MANAGED_CONTAINERS}
    for name in MANAGED_CONTAINERS:
        try:
            c = client.containers.get(name)
            states[name] = (c.status, parse_docker_time(c.attrs["State"].get("StartedAt")))
        except docker.errors.NotFound:
            states[name] = ("not found", None)
        except Exception:
            states[name] = ("unknown", None)
    return states

# ─────────────────────────────────────────────
# OVERVIEW
# ─────────────────────────────────────────────
_cache = {"at": 0.0, "value": None}
_cache_lock = threading.Lock()


def get_overview():
    with _cache_lock:
        if _cache["value"] and time.time() - _cache["at"] < 8:
            return _cache["value"]

    now = log_now()
    activity, last = log_activity(now)
    errors_24h, latest_error = recent_errors(now)
    db = db_stats()
    states = worker_states()

    stopped = [WORKER_INFO[n][0] for n, (s, _) in states.items() if s not in ("running", "unknown")]
    started = [t for (s, t) in states.values() if s == "running" and t]
    last_mirrored = max([t for t in (last["post"], last["comment"]) if t], default=None)

    parts = []
    if last["post"]:
        parts.append(f"Last post {humanize_ago(last['post'], now)}.")
    if last["comment"]:
        parts.append(f"Last comment {humanize_ago(last['comment'], now)}.")

    if stopped:
        level = "bad"
        headline = f"{plural(len(stopped), 'worker')} stopped"
        names = stopped[0] if len(stopped) == 1 else ", ".join(stopped[:-1]) + " and " + stopped[-1]
        parts.insert(0, f"{names} {'is' if len(stopped) == 1 else 'are'} not running.")
    elif last_mirrored is None:
        level = "warn"
        headline = "Waiting for activity"
        parts = ["Nothing mirrored in the recent logs yet."]
    elif now - last_mirrored > QUIET_AFTER:
        level = "warn"
        headline = f"Quiet for {humanize_duration(now - last_mirrored, parts=1)}"
    else:
        level = "ok"
        headline = "Mirroring normally"

    oldest_start = min(started) if started else None
    overview = {
        "level": level,
        "headline": headline,
        "detail": " ".join(parts),
        "totals": {"posts": db["posts"], "comments": db["comments"]},
        "last24": {"posts": sum(activity["posts"]), "comments": sum(activity["comments"])},
        "queue": {
            "posts": db["queued_posts"], "comments": db["queued_comments"],
            "other": db["queued_other"], "running": db["running"], "failed": db["failed"],
        },
        "errors24": errors_24h,
        "last_error": (
            {"ago": humanize_ago(latest_error[0], now), "context": latest_error[1]}
            if latest_error else None
        ),
        "running_for": (
            humanize_duration(datetime.now(timezone.utc) - oldest_start) if oldest_start else None
        ),
        "workers": {"running": sum(1 for s, _ in states.values() if s == "running"), "total": len(states)},
        "activity": activity,
        "updated": now.strftime("%H:%M"),
        # Kept for anything already reading the old /dashboard/metrics format
        "mirror_status": "working" if db["running"] else ("running" if level != "bad" else "degraded"),
        "posts_queued": db["queued_posts"],
        "comments_queued": db["queued_comments"],
        "posts_done": db["posts"],
    }
    with _cache_lock:
        _cache.update(at=time.time(), value=overview)
    return overview


def get_stats():
    """Backwards-compatible alias for the old metrics function."""
    return get_overview()


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


def worker_details(name: str, sync_enabled: bool) -> dict:
    """Status, uptime and resource use for one worker (for the health list)."""
    label, description = WORKER_INFO[name]
    info = {
        "name": name, "label": label, "description": description,
        "status": "unknown", "state": "unknown", "uptime": None,
        "cpu_percent": None, "mem_mb": None, "mem_percent": None,
        "is_comment_sync": name == "lemmy_comment_sync", "sync_enabled": sync_enabled,
    }
    try:
        c = docker_client().containers.get(name)
    except docker.errors.NotFound:
        info.update(status="not found", state="missing")
        return info
    except Exception as e:
        info.update(status=f"error: {e}", state="error")
        return info

    info["status"] = c.status
    started = parse_docker_time(c.attrs["State"].get("StartedAt"))
    if c.status == "running":
        info["state"] = "paused" if (info["is_comment_sync"] and not sync_enabled) else "running"
        if started:
            info["uptime"] = humanize_duration(datetime.now(timezone.utc) - started)
        try:
            stats = c.stats(stream=False)
            mem = stats.get("memory_stats", {})
            usage = mem.get("usage", 0) / (1024 * 1024)
            limit = (mem.get("limit") or 1) / (1024 * 1024)
            info.update(
                cpu_percent=cpu_percent_from_stats(stats),
                mem_mb=round(usage, 1),
                mem_percent=round(usage / limit * 100, 1),
            )
        except Exception:
            pass
    elif c.status == "restarting":
        info["state"] = "restarting"
    else:
        info["state"] = "stopped"
        finished = parse_docker_time(c.attrs["State"].get("FinishedAt"))
        # Docker uses year 1 for "never finished"
        if finished and finished.year > 2000:
            ago = humanize_ago(finished.replace(tzinfo=None), datetime.now(timezone.utc).replace(tzinfo=None))
            info["stopped_ago"] = ago
            exit_code = c.attrs["State"].get("ExitCode")
            info["crashed"] = exit_code not in (0, 137, 143, None)  # 137/143: stopped on purpose
    return info

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.get("/dashboard/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"overview": get_overview(), "page": "overview"})

@app.get("/dashboard/logs", response_class=HTMLResponse)
def show_logs(request: Request):
    return templates.TemplateResponse(request, "logs.html", {"page": "logs"})

@app.get("/dashboard/metrics")
def metrics():
    return get_overview()

_stats_pool = ThreadPoolExecutor(max_workers=len(MANAGED_CONTAINERS))

@app.get("/dashboard/health", response_class=HTMLResponse)
def get_worker_health_html(request: Request):
    """Render the worker list (fetched by the overview page every few seconds)."""
    sync_enabled = comment_sync_enabled()
    # Docker takes about a second per container to sample CPU, so ask in parallel
    workers = list(_stats_pool.map(lambda n: worker_details(n, sync_enabled), MANAGED_CONTAINERS))
    return templates.TemplateResponse(request, "partials/worker_health.html", {"containers": workers})

# NOTE: the build route must be registered before the generic {action} route,
# otherwise "/build" is captured as an action and rejected.
@app.post("/dashboard/container/{name}/build", dependencies=[Depends(require_dashboard_request)])
def build_container(name: str, no_cache: bool = False):
    """
    Rebuild a worker's image and (re)create its container from docker-compose.yml.
    """
    require_managed_container(name)
    service = MANAGED_CONTAINERS[name]
    label = WORKER_INFO[name][0]

    build_args = ["build", service]
    if no_cache:
        build_args.append("--no-cache")

    try:
        result = compose(*build_args)
        if result.returncode != 0:
            return {"error": f"Couldn't rebuild {label}", "details": result.stderr or result.stdout}

        result = compose("up", "-d", service)
        if result.returncode != 0:
            return {"error": f"Rebuilt {label}, but it didn't start", "details": result.stderr or result.stdout}

        return {"success": f"Rebuilt and started {label}"}
    except Exception as e:
        return {"error": str(e)}

PAST_TENSE = {"start": "Started", "stop": "Stopped", "restart": "Restarted"}

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

        _cache["value"] = None  # make the next overview reflect the change
        return {"success": f"{PAST_TENSE[action]} {WORKER_INFO[name][0]}"}

    except docker.errors.NotFound:
        return {"error": f"{WORKER_INFO[name][0]} isn't set up yet. Rebuild it first."}
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

    new_value = "false" if comment_sync_enabled() else "true"
    set_env_value("ENABLE_LEMMY_COMMENT_SYNC", new_value)

    # 🧱 Rebuild and recreate the container to apply env change
    try:
        result = compose("up", "-d", "--build", "--force-recreate", "lemmy_comment_sync")
        if result.returncode != 0:
            return {"error": result.stderr or result.stdout}
    except Exception as e:
        return {"error": str(e)}

    _cache["value"] = None
    state = "on" if new_value == "true" else "off"
    return {"success": f"Turned Lemmy → Reddit comments {state}", "state": new_value}

# ─────────────────────────────────────────────
# WEBSOCKET: Live Log Stream
# Lives under /dashboard/ so the same nginx location (and its login) covers it.
# ─────────────────────────────────────────────
@app.websocket("/dashboard/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    path = LOG_FILE
    if not path.exists():
        await websocket.send_text("No log file yet. Lines will appear here once the workers start writing.\n")
        await websocket.close()
        return

    # Start with recent history so the page isn't blank until the next log line
    await websocket.send_text("".join(tail_log(path, 300)))
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
