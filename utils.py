import os
import json
import requests
import traceback
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pathlib import Path

# ───────────────────────────────
# Environment Setup
# ───────────────────────────────
load_dotenv()

LEMMY_URL = os.getenv("LEMMY_URL")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")

DATA_DIR = Path("data")
LOG_DIR = Path("logs")
TOKEN_PATH = DATA_DIR / "lemmy_token.json"
LOG_FILE = LOG_DIR / "bridge.log"
ERROR_FILE = LOG_DIR / "errors.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

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
def get_valid_token() -> str:
    """
    Returns a valid Lemmy JWT from cache, refreshing if expired or missing.
    """
    if TOKEN_PATH.exists():
        try:
            data = json.loads(TOKEN_PATH.read_text())
            jwt = data.get("jwt")
            expiry = data.get("expires")
            if jwt and expiry:
                if datetime.utcnow() < datetime.fromisoformat(expiry):
                    return jwt
        except Exception:
            pass  # Fall through to refresh

    return refresh_token()


def refresh_token() -> str:
    """Fetch a new JWT token from Lemmy and cache it locally."""
    if not all([LEMMY_URL, LEMMY_USER, LEMMY_PASS]):
        raise RuntimeError("Missing Lemmy credentials in .env")

    url = f"{LEMMY_URL}/api/v3/user/login"
    payload = {"username_or_email": LEMMY_USER, "password": LEMMY_PASS}
    try:
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Lemmy login failed: {r.status_code} {r.text}")

        data = r.json()
        jwt = data.get("jwt")
        if not jwt:
            raise RuntimeError("Lemmy returned no JWT")

        TOKEN_PATH.write_text(
            json.dumps(
                {
                    "jwt": jwt,
                    "expires": (datetime.utcnow() + timedelta(hours=4)).isoformat(),
                },
                indent=2,
            )
        )
        log("🔑 Refreshed Lemmy token")
        return jwt

    except Exception as e:
        log_error("refresh_token", e)
        raise


# ───────────────────────────────
# Lemmy API Helpers
# ───────────────────────────────
def get_community_id(community_name: str, jwt: str) -> int:
    """Return Lemmy community ID by name."""
    url = f"{LEMMY_URL}/api/v3/community"
    try:
        r = requests.get(url, params={"name": community_name}, headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Failed to fetch community: {r.status_code} {r.text}")

        return r.json().get("community_view", {}).get("community", {}).get("id")
    except Exception as e:
        log_error("get_community_id", e)
        raise


def create_lemmy_post(subreddit: str, post_data: dict, jwt: str, community_id: int) -> int:
    """Create a new Lemmy post from Reddit submission data."""
    url = f"{LEMMY_URL}/api/v3/post"

    # Build Lemmy post body: default to text post, not link post
    body = {
        "name": post_data.get("title", "[untitled]"),
        "body": post_data.get("selftext", "") or "",
        "community_id": community_id,
    }

    # Only include 'url' if you explicitly want a link-style post
    if post_data.get("force_link") and post_data.get("url"):
        body["url"] = post_data["url"]

    try:
        r = requests.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30,
        )
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
