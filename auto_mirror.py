#!/usr/bin/env python3
"""
Reddit → Lemmy Auto Mirror
Stable production-ready version
----------------------------------------------------------
Features:
 - Token reuse for 23h (prevents Lemmy duplicate-token bug)
 - Auto refresh community map every 6h
 - Rich formatting with Reddit permalinks and media
 - Comment mirroring with persistent mapping
 - Clean logging for Docker-based deployment
"""

import os
import time
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# ENV CONFIG
# ─────────────────────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER", "mirrorbot")
LEMMY_PASS = os.getenv("LEMMY_PASS", "password")

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# ─────────────────────────────────────────────
# SUBREDDIT → COMMUNITY MAP
# ─────────────────────────────────────────────
SUB_MAP = {}
raw_sub_map = os.getenv(
    "SUB_MAP", "fosscad2:fosscad2,3d2a:3D2A,FOSSCADtoo:FOSSCADtoo"
)

for pair in raw_sub_map.split(","):
    if ":" in pair:
        k, v = pair.split(":", 1)
        SUB_MAP[k.strip()] = v.strip()


TOKEN_FILE = DATA_DIR / "token.json"
COMMUNITY_MAP_FILE = DATA_DIR / "community_map.json"
POST_MAP_FILE = DATA_DIR / "post_map.json"

TOKEN_REUSE_HOURS = 23
COMMUNITY_REFRESH_HOURS = 6
SLEEP_BETWEEN_CYCLES = 900  # 15 min between full cycles

token_state = {}

def log(msg: str):
    print(f"{datetime.utcnow().isoformat()} | {msg}", flush=True)

# ─────────────────────────────────────────────
# POST MAP (PERSISTENT TRACKING)
# ─────────────────────────────────────────────
if POST_MAP_FILE.exists():
    try:
        post_map = json.loads(POST_MAP_FILE.read_text())
        log(f"🗂️ Loaded {len(post_map)} mirrored posts from {POST_MAP_FILE}")
    except Exception as e:
        log(f"⚠️ Failed to read post_map.json: {e}")
        post_map = {}
else:
    post_map = {}
    log("📂 No post_map.json found, starting fresh.")

# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def load_json(path, default=None):
    if not Path(path).exists():
        return default if default is not None else {}
    try:
        return json.load(open(path))
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)

# ─────────────────────────────────────────────
# LEMMY AUTHENTICATION (STABLE TOKEN REUSE)
# ─────────────────────────────────────────────
if TOKEN_FILE.exists():
    try:
        token_state = json.load(open(TOKEN_FILE))
    except Exception as e:
        log(f"⚠️ Failed to read token cache: {e}")


def lemmy_login(force=False):
    """Return a valid Lemmy JWT, reusing cached token for up to 23h."""
    global token_state

    # 1️⃣ Use cached token if fresh
    if not force and token_state.get("jwt"):
        age = time.time() - token_state.get("ts", 0)
        if age < TOKEN_REUSE_HOURS * 3600:
            log(f"🔁 Using cached Lemmy token (age={int(age)}s)")
            return token_state["jwt"]

    # 2️⃣ Otherwise login fresh
    log(f"🔑 Logging in to {LEMMY_URL}/api/v3/user/login as {LEMMY_USER}")
    r = requests.post(
        f"{LEMMY_URL}/api/v3/user/login",
        json={"username_or_email": LEMMY_USER, "password": LEMMY_PASS},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Lemmy login failed: {r.status_code} {r.text[:300]}")

    data = r.json()
    jwt = data.get("jwt")
    if not jwt:
        raise RuntimeError(f"No JWT returned: {data}")

    token_state = {"jwt": jwt, "ts": time.time(), "last_login": time.time()}
    save_json(TOKEN_FILE, token_state)
    log("✅ Logged into Lemmy (token cached)")
    return jwt


def get_valid_token():
    try:
        return lemmy_login(force=False)
    except Exception as e:
        log(f"⚠️ Token check failed, retrying login: {e}")
        return lemmy_login(force=True)


# ─────────────────────────────────────────────
# COMMUNITY CACHE
# ─────────────────────────────────────────────
def refresh_community_map(jwt):
    """Fetch all local communities and persist their IDs."""
    log("🌐 Refreshing community map from Lemmy...")
    headers = {"Authorization": f"Bearer {jwt}"}
    r = requests.get(f"{LEMMY_URL}/api/v3/community/list", headers=headers, timeout=20)
    if not r.ok:
        log(f"⚠️ Failed to fetch communities: {r.status_code} {r.text[:200]}")
        return

    data = r.json()
    mapping = {
        c["community"]["name"].lower(): c["community"]["id"]
        for c in data.get("communities", [])
    }
    mapping["_fetched_at"] = time.time()
    save_json(COMMUNITY_MAP_FILE, mapping)
    log(f"✅ Saved {len(mapping)-1} communities to map.")


def get_community_id(name: str, jwt: str):
    """Case-insensitive local lookup."""
    mapping = load_json(COMMUNITY_MAP_FILE, {})
    if not mapping or time.time() - mapping.get("_fetched_at", 0) > COMMUNITY_REFRESH_HOURS * 3600:
        refresh_community_map(jwt)
        mapping = load_json(COMMUNITY_MAP_FILE, {})
    for k, v in mapping.items():
        if k.lower() == name.lower():
            return v
    raise RuntimeError(f"community lookup error: could not resolve '{name}' (case-insensitive)")


# ─────────────────────────────────────────────
# POST & COMMENT MIRRORING
# ─────────────────────────────────────────────
def create_lemmy_post(sub, post, jwt, comm_id):
    """Create a Lemmy post with embedded Reddit metadata."""
    url = f"{LEMMY_URL}/api/v3/post"
    body = (
        f"**Reddit Post:** [{post['title']}]({post['url']})\n\n"
        f"{post.get('selftext','')}\n\n"
        f"[View on Reddit](https://reddit.com{post['permalink']})"
    )

    headers = {"Authorization": f"Bearer {jwt}"}
    payload = {
        "name": post["title"][:200],
        "community_id": comm_id,
        "body": body,
    }

    def attempt_post():
        return requests.post(url, json=payload, headers=headers, timeout=20)

    r = attempt_post()
    if r.status_code == 401:
        log("⚠️ Lemmy returned 401, refreshing token once and retrying…")
        new_jwt = lemmy_login(force=True)
        headers["Authorization"] = f"Bearer {new_jwt}"
        r = attempt_post()

    if not r.ok:
        raise RuntimeError(f"Lemmy post failed: {r.status_code} {r.text[:200]}")

    pid = r.json()["post_view"]["post"]["id"]
    log(f"✅ Posted '{post['title']}' (Lemmy ID={pid})")
    return pid

def mirror_comments(sub, post_id, comments, jwt):
    """Mirror Reddit comments."""
    if not comments:
        log("✅ No comments to mirror.")
        return

    url = f"{LEMMY_URL}/api/v3/comment"
    headers = {"Authorization": f"Bearer {jwt}"}

    for c in comments:
        if not hasattr(c, "body"):
            continue

        content = getattr(c, "body", "").strip()
        if not content:
            continue

        payload = {
            "content": content,
            "post_id": post_id,
        }

        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if r.status_code == 401:
                log("⚠️ Comment post 401, retrying with refreshed token…")
                new_jwt = lemmy_login(force=True)
                headers["Authorization"] = f"Bearer {new_jwt}"
                r = requests.post(url, json=payload, headers=headers, timeout=20)

            # Handle Lemmy rate limits gracefully
            if r.status_code == 400 and "rate_limit" in r.text:
                log("⏳ Rate limited — sleeping 10s before next comment…")
                time.sleep(10)
                continue

            if not r.ok:
                log(f"⚠️ Comment failed: {r.status_code} {r.text[:200]}")
                continue

            # Short sleep to prevent hammering Lemmy
            time.sleep(3)

        except Exception as e:
            log(f"⚠️ Error posting comment: {e}")
            continue

    log(f"✅ Mirrored {len(comments)} comments.")


# ─────────────────────────────────────────────
# MAIN MIRROR LOOP
# ─────────────────────────────────────────────
def mirror_once():
    jwt = get_valid_token()

    for reddit_sub, lemmy_comm in SUB_MAP.items():
        log(f"🔍 Checking r/{reddit_sub} → c/{lemmy_comm} @ {datetime.utcnow()}")

        try:
            comm_id = get_community_id(lemmy_comm, jwt)
        except Exception as e:
            log(f"❌ Skipping {reddit_sub}: {e}")
            continue

        if TEST_MODE:
            log("🧪 TEST_MODE active — posting sample content instead of real Reddit posts.")
            mock_post = {
                "title": "Example mirrored post",
                "url": f"https://reddit.com/r/{reddit_sub}/test",
                "permalink": f"/r/{reddit_sub}/comments/test",
                "selftext": "✅ Test successful: Reddit → Lemmy bridge is connected."
            }
            try:
                pid = create_lemmy_post(reddit_sub, mock_post, jwt, comm_id)
                if pid:
                    mirror_comments(reddit_sub, pid, [], jwt)
            except Exception as e:
                log(f"⚠️ Error creating test post: {e}")

        else:
            log(f"🔄 Live mode: Fetching from Reddit API (limit={os.getenv('REDDIT_LIMIT', 10)})…")
            # Import Reddit client only if needed
            import praw
            reddit = praw.Reddit(
                client_id=os.getenv("REDDIT_CLIENT_ID"),
                client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
                user_agent="reddit-lemmy-bridge"
            )
            subreddit_obj = reddit.subreddit(reddit_sub)
            for submission in subreddit_obj.new(limit=int(os.getenv("REDDIT_LIMIT", 10))):
                if submission.id in post_map:
                    log(f"⏭️ Skipping already mirrored post: {submission.title}")
                    continue
                post_data = {
                    "title": submission.title,
                    "url": submission.url,
                    "permalink": submission.permalink,
                    "selftext": submission.selftext,
                }
                try:
                    pid = create_lemmy_post(reddit_sub, post_data, jwt, comm_id)
                    if pid:
                        mirror_comments(reddit_sub, pid, submission.comments[:3], jwt)
                        # Save post mapping
                        post_map[submission.id] = {
                                "title": submission.title,
                                "lemmy_id": pid,
                                "timestamp": datetime.utcnow().isoformat()
                        }
                        save_json(POST_MAP_FILE, post_map)
                        log(f"💾 Saved post_map.json ({len(post_map)} total entries)")

                except Exception as e:
                    log(f"⚠️ Error creating post from Reddit: {e}")

    log(f"🕒 Sleeping {SLEEP_BETWEEN_CYCLES}s...")

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log("🔧 reddit → lemmy bridge starting…")
    while True:
        try:
            mirror_once()
        except Exception as e:
            log(f"❌ Mirror cycle failed: {e}")
        time.sleep(SLEEP_BETWEEN_CYCLES)
