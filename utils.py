import os
import json
import requests
import traceback
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pathlib import Path
import psutil
import time

# ───────────────────────────────
# Environment Setup
# ───────────────────────────────
load_dotenv()

LEMMY_URL = os.getenv("LEMMY_URL")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")

# ───────────────────────────────
# Directory & Lock Setup
# ───────────────────────────────
DATA_DIR = Path("/opt/Reddit-Mirror-2-Lemmy/data")
LOG_DIR = Path("logs")
TOKEN_PATH = DATA_DIR / "lemmy_token.json"
TOKEN_LOCK_FILE = DATA_DIR / "login.lock"
LOG_FILE = LOG_DIR / "bridge.log"
ERROR_FILE = LOG_DIR / "errors.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)


def acquire_token_lock(timeout=90):
    """
    Prevent concurrent Lemmy logins across multiple processes.
    Returns True if safe to log in, False if another process logged in recently.
    """
    if TOKEN_LOCK_FILE.exists():
        age = time.time() - TOKEN_LOCK_FILE.stat().st_mtime
        if age < timeout:
            print(f"🕒 Recent Lemmy login {age:.0f}s ago — skipping new login.")
            return False
    TOKEN_LOCK_FILE.touch()
    return True


# ───────────────────────────────
# Dashboard Helper
# ───────────────────────────────
def write_status(mirror_status="running", posts=0, comments=0):
    """Write mirror runtime stats to data/state.json for the dashboard."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        status_file = DATA_DIR / "state.json"

        uptime = time.strftime("%H:%M:%S", time.gmtime(time.time() - psutil.boot_time()))

        data = {
            "mirror_status": mirror_status,
            "posts_queued": posts,
            "comments_queued": comments,
            "uptime": uptime,
        }

        status_file.write_text(json.dumps(data, indent=2))
    except Exception as e:
        log_error("write_status", e)


# ───────────────────────────────
# Logging Utilities
# ───────────────────────────────
def log(msg: str):
    """Logs normal info messages to console and bridge.log"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} | {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_error(context: str, exc: Exception):
    """
    Logs detailed errors to errors.log with stack trace.
    Example: log_error("mirror_post", e)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    line = (
        f"\n{'='*60}\n"
        f"{timestamp} | ERROR in {context}\n"
        f"{'-'*60}\n"
        f"{tb.strip()}\n"
    )
    print(f"❌ {context}: {exc}")
    with open(ERROR_FILE, "a", encoding="utf-8") as f:
        f.write(line)


# ───────────────────────────────
# Lemmy Authentication Helpers
# ───────────────────────────────
def get_valid_token(username: str = None, password: str = None, force: bool = False) -> str:
    """
    Returns a valid Lemmy JWT from cache, refreshing only if missing or expired.
    You can override credentials with username/password for multi-user setups.
    """
    user = username or LEMMY_USER
    pwd = password or LEMMY_PASS

    if not all([LEMMY_URL, user, pwd]):
        raise RuntimeError("Missing Lemmy credentials (.env or override)")

    # Token file per user (so mirrorbot and mirrorcomments each get their own)
    user_safe = user.replace("@", "_").replace(".", "_")
    token_file = DATA_DIR / f"lemmy_token_{user_safe}.json"

    try:
        if not force and token_file.exists():
            data = json.loads(token_file.read_text())
            jwt = data.get("jwt")
            expiry = data.get("expires")

            # ✅ Reuse unexpired token
            if jwt and expiry and datetime.utcnow() < datetime.fromisoformat(expiry):
                return jwt

            # ⏳ If expired very recently, wait for another process to refresh it
            if jwt and expiry:
                age = (datetime.utcnow() - datetime.fromisoformat(expiry)).total_seconds()
                if age < 120 and not acquire_token_lock(timeout=90):
                    print("🕒 Token recently expired — waiting for another process to refresh it.")
                    time.sleep(5)
                    try:
                        data = json.loads(token_file.read_text())
                        new_jwt = data.get("jwt")
                        new_expiry = data.get("expires")
                        if new_jwt and new_expiry and datetime.utcnow() < datetime.fromisoformat(new_expiry):
                            print("♻️ Found freshly refreshed token after wait.")
                            return new_jwt
                    except Exception:
                        pass
    except Exception as e:
        log_error("get_valid_token", e)

    # 🚀 Fallback: perform real refresh
    return refresh_token(username=user, password=pwd, token_file=token_file)


def refresh_token(username: str = None, password: str = None, token_file: Path = None) -> str:
    """Fetch a new JWT token from Lemmy and cache it locally (per-user, shared + rate-safe)."""
    user = username or LEMMY_USER
    pwd = password or LEMMY_PASS
    token_file = token_file or (DATA_DIR / "lemmy_token.json")

    if not all([LEMMY_URL, user, pwd]):
        raise RuntimeError("Missing Lemmy credentials for refresh_token")

    if not acquire_token_lock(timeout=90):
        # Another process logged in recently — reuse cache if possible
        if token_file.exists():
            try:
                data = json.loads(token_file.read_text())
                jwt = data.get("jwt")
                expiry = data.get("expires")
                if jwt and expiry and datetime.utcnow() < datetime.fromisoformat(expiry):
                    print("♻️ Using cached Lemmy token (recent login detected).")
                    return jwt
            except Exception:
                pass

    url = f"{LEMMY_URL}/api/v3/user/login"
    payload = {"username_or_email": user, "password": pwd}

    for attempt in range(1, 6):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 429 or "rate_limit_error" in r.text:
                wait_time = 15 * attempt
                print(f"⚠️ Lemmy rate limit hit, waiting {wait_time}s...")
                time.sleep(wait_time)
                continue

            if r.status_code != 200:
                raise RuntimeError(f"Lemmy login failed: {r.status_code} {r.text}")

            data = r.json()
            jwt = data.get("jwt")
            if not jwt:
                raise RuntimeError("Lemmy returned no JWT")

            token_file.write_text(
                json.dumps(
                    {
                        "jwt": jwt,
                        "expires": (datetime.utcnow() + timedelta(hours=4)).isoformat(),
                    },
                    indent=2,
                )
            )
            log(f"🔑 Refreshed Lemmy token for {user}")
            return jwt

        except Exception as e:
            log_error("refresh_token", e)
            time.sleep(3)

    raise RuntimeError(f"Lemmy login failed for {user} after multiple attempts")

# ───────────────────────────────
# Lemmy API Helpers
# ───────────────────────────────
def get_community_id(community_name: str, jwt: str) -> int:
    """Return Lemmy community ID by name."""
    url = f"{LEMMY_URL}/api/v3/community"
    try:
        r = requests.get(
            url,
            params={"name": community_name},
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Failed to fetch community: {r.status_code} {r.text}")

        return r.json().get("community_view", {}).get("community", {}).get("id")
    except Exception as e:
        log_error("get_community_id", e)
        raise


def create_lemmy_post(subreddit: str, post_data: dict, jwt: str, community_id: int) -> int:
    """Create a new Lemmy post from Reddit submission data."""
    url = f"{LEMMY_URL}/api/v3/post"

    body = {
        "name": post_data.get("title", "[untitled]"),
        "body": post_data.get("selftext", "") or "",
        "community_id": community_id,
    }

    if post_data.get("force_link") and post_data.get("url"):
        body["url"] = post_data["url"]

    try:
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Post creation failed: {r.status_code} {r.text}")

        post_id = r.json().get("post_view", {}).get("post", {}).get("id")
        if not post_id:
            raise RuntimeError("Invalid Lemmy response: missing post ID")

        log(f"✅ Created Lemmy post for subreddit {subreddit}: ID={post_id}")
        return post_id

    except Exception as e:
        log_error("create_lemmy_post", e)
        raise