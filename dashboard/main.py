from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json, os, sqlite3, asyncio
from datetime import datetime
import docker
import subprocess

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
# ✅ Mount static assets at /dashboard/static
app.mount("/dashboard/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def tail_log(file_path, n=50):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.readlines()[-n:]
    except FileNotFoundError:
        return ["No logs found.\n"]

def get_stats():
    db_path = DATA_DIR / "jobs.db"
    stats = {"mirror_status": "running", "posts_queued": 0, "comments_queued": 0, "duplicates_skipped": 0, "videos_uploaded": 0, "posts_done": 0, "uptime": "?"}
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
                stats["posts_queued"] += count
                stats["mirror_status"] = "working"
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

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.get("/dashboard/", response_class=HTMLResponse)
def index(request: Request):
    stats = get_stats()
    return templates.TemplateResponse("index.html", {"request": request, "stats": stats})

@app.get("/dashboard/logs", response_class=HTMLResponse)
def show_logs(request: Request):
    lines = tail_log(LOG_FILE, 50)
    return templates.TemplateResponse("logs.html", {"request": request, "lines": lines})

@app.get("/dashboard/metrics")
def metrics():
    return get_stats()

@app.get("/dashboard/health", response_class=HTMLResponse)
def get_worker_health_html(request: Request):
    """Render Docker container stats as HTML for the dashboard (HTMX)."""
    client = docker.DockerClient(base_url="unix://var/run/docker.sock")
    container_names = [
        "reddit_mirror_worker",
        "reddit_mirror_refresh",
        "lemmy_comment_sync",
        "reddit_comment_sync"
    ]
    info = []

    for name in container_names:
        try:
            c = client.containers.get(name)
            stats = c.stats(stream=False)

            # ─── CPU USAGE ────────────────────────────────────────────────
            cpu_total = stats["cpu_stats"]["cpu_usage"]["total_usage"]
            sys_cpu = stats["cpu_stats"]["system_cpu_usage"]
            prev_cpu_total = stats["precpu_stats"]["cpu_usage"]["total_usage"]
            prev_sys_cpu = stats["precpu_stats"]["system_cpu_usage"]
            cpu_delta = cpu_total - prev_cpu_total
            sys_delta = sys_cpu - prev_sys_cpu
            cpu_percent = round(
                (cpu_delta / sys_delta)
                * len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", []))
                * 100,
                2
            ) if sys_delta > 0 else 0

            # ─── MEMORY USAGE ─────────────────────────────────────────────
            mem_usage = stats["memory_stats"]["usage"] / (1024 * 1024)
            mem_limit = stats["memory_stats"].get("limit", 1) / (1024 * 1024)
            mem_percent = round((mem_usage / mem_limit) * 100, 1)

            # ─── Check ENABLE_LEMMY_COMMENT_SYNC flag ─────────────────────
            sync_state = None
            if name == "lemmy_comment_sync":
                env_path = Path("/opt/Reddit-Mirror-2-Lemmy/.env")
                if env_path.exists():
                    for line in env_path.read_text().splitlines():
                        if line.startswith("ENABLE_LEMMY_COMMENT_SYNC="):
                            sync_state = "active" if "true" in line.lower() else "disabled"
                            break

            # ─── Append container info ───────────────────────────────────
            info.append({
                "name": name,
                "status": c.status,
                "cpu_percent": cpu_percent,
                "mem_mb": round(mem_usage, 1),
                "mem_percent": mem_percent,
                "uptime": c.attrs["State"]["StartedAt"],
                "sync_state": sync_state
            })
        except docker.errors.NotFound:
            info.append({"name": name, "status": "not found"})
        except Exception as e:
            info.append({"name": name, "status": f"error: {e}"})

    return templates.TemplateResponse("partials/worker_health.html", {
        "request": request,
        "containers": info
    })

@app.post("/dashboard/container/{name}/{action}")
def control_container(name: str, action: str):
    """
    Control containers (start/stop/restart).
    If the container is lemmy_comment_sync, also persist its state to .env.
    """
    client = docker.DockerClient(base_url="unix://var/run/docker.sock")
    env_path = Path(os.getenv("ENV_PATH", "/opt/Reddit-Mirror-2-Lemmy/.env"))

    try:
        c = client.containers.get(name)

        # Perform the requested action
        if action == "restart":
            c.restart()
        elif action == "stop":
            c.stop()
        elif action == "start":
            c.start()
        else:
            return {"error": "Unsupported action"}

        # 🧠 Persist state for lemmy_comment_sync
        if name == "lemmy_comment_sync":
            if not env_path.exists():
                raise FileNotFoundError(f".env file not found at {env_path}")

            lines = env_path.read_text().splitlines()
            updated_lines = []
            found = False

            for line in lines:
                if line.startswith("ENABLE_LEMMY_COMMENT_SYNC="):
                    found = True
                    new_value = "true" if action == "start" else "false"
                    updated_lines.append(f"ENABLE_LEMMY_COMMENT_SYNC={new_value}")
                else:
                    updated_lines.append(line)

            if not found:
                # Add it if not already in file
                updated_lines.append(
                    f"ENABLE_LEMMY_COMMENT_SYNC={'true' if action == 'start' else 'false'}"
                )

            env_path.write_text("\n".join(updated_lines))

        return {"success": f"{name} {action} executed"}

    except docker.errors.NotFound:
        return {"error": f"Container {name} not found"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/dashboard/container/{name}/build")
def build_container(name: str, no_cache: bool = False):
    """
    Trigger a Docker build for a given container (same image used in docker-compose).
    """
    try:
        # Map container names to their Docker Compose service names
        service_map = {
            "reddit_mirror_worker": "reddit-mirror",
            "reddit_mirror_refresh": "reddit-refresh",
            "lemmy_comment_sync": "lemmy_comment_sync",
            "reddit_comment_sync": "reddit_comment_sync",
            "mirror-dashboard": "mirror-dashboard",
        }

        service = service_map.get(name)
        if not service:
            return {"error": f"No docker-compose service mapped for {name}"}

        build_cmd = ["docker", "compose", "build", service]
        if no_cache:
            build_cmd.append("--no-cache")

        result = subprocess.run(build_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return {"success": f"✅ Build completed for {service}"}
        else:
            return {
                "error": f"❌ Build failed for {service}",
                "details": result.stderr or result.stdout,
            }
    except Exception as e:
        return {"error": str(e)}

# ─────────────────────────────────────────────
# TOGGLE COMMENT SYNC (Enable/Disable)
# ─────────────────────────────────────────────
@app.post("/dashboard/container/toggle-comment-sync")
def toggle_comment_sync():
    """
    Toggle ENABLE_LEMMY_COMMENT_SYNC in .env, rebuild container, and restart it.
    """
    env_path = Path("/opt/Reddit-Mirror-2-Lemmy/.env")
    if not env_path.exists():
        return {"error": ".env not found"}

    # 🔄 Read and flip the state
    lines = env_path.read_text().splitlines()
    new_lines = []
    current = "false"
    for line in lines:
        if line.startswith("ENABLE_LEMMY_COMMENT_SYNC="):
            current = line.split("=")[1].strip().lower()
        else:
            new_lines.append(line)

    new_value = "false" if current == "true" else "true"
    new_lines.append(f"ENABLE_LEMMY_COMMENT_SYNC={new_value}")
    env_path.write_text("\n".join(new_lines))

    # 🧱 Rebuild and recreate the container to apply env change
    try:
        rebuild_cmd = [
            "docker", "compose",
            "-f", "/opt/Reddit-Mirror-2-Lemmy/docker-compose.yml",
            "up", "-d", "--build", "--force-recreate", "lemmy_comment_sync"
        ]
        result = subprocess.run(rebuild_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"error": result.stderr or result.stdout}

    except Exception as e:
        return {"error": str(e)}

    return {"success": f"Comment sync {'ENABLED' if new_value == 'true' else 'DISABLED'}", "state": new_value}

# ─────────────────────────────────────────────
# WEBSOCKET: Live Log Stream
# ─────────────────────────────────────────────
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    path = LOG_FILE
    if not path.exists():
        await websocket.send_text("No logs found.\n")
        await websocket.close()
        return

    with open(path, "r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        last_size = path.stat().st_size

    try:
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
        print(f"⚠️ WebSocket error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
