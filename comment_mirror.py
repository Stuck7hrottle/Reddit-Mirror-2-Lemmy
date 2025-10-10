#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import requests
import praw
from praw.models import Comment
from dotenv import load_dotenv

# --------------------------
# Config / .env
# --------------------------
load_dotenv()

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "reddit-lemmy-bridge/1.0")

LEMMY_URL = os.getenv("LEMMY_URL", "https://example.com")
LEMMY_USER = os.getenv("LEMMY_USER_COMMENTS") or os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS_COMMENTS") or os.getenv("LEMMY_PASS")

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

POST_MAP_FILE = DATA_DIR / "post_map.json"
COMMENT_MAP_FILE = DATA_DIR / "comment_map.json"

COMMENT_SLEEP = float(os.getenv("COMMENT_SLEEP", "0.3"))
COMMENT_LIMIT_TOTAL = int(os.getenv("COMMENT_LIMIT_TOTAL", "500"))

from pathlib import Path
import os

TOKEN_FILE = Path(os.getenv("TOKEN_FILE_COMMENTS", "/app/data/token.json"))


# --------------------------
# CLI / REFRESH flag
# --------------------------
parser = argparse.ArgumentParser(description="Mirror Reddit comments to Lemmy.")
parser.add_argument(
    "--refresh",
    action="store_true",
    help="Resync all mapped posts and fill missing comments (also enabled by REFRESH=true env var).",
)
args, _unknown = parser.parse_known_args()
REFRESH = args.refresh or os.getenv("REFRESH", "false").lower() == "true"

print(f"{'🔁' if REFRESH else '▶️'} comment_mirror.py starting (refresh={REFRESH})")

# --------------------------
# Helpers
# --------------------------
def load_json(path: Path, default):
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Failed to read {path}: {e}. Using default.")
    return default

def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def sanitise_markdown(text: str) -> str:
    if text is None:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()

def author_label(comment: Comment) -> str:
    if getattr(comment, "author", None) is None:
        return "**u/[deleted]:**"
    name = getattr(comment.author, "name", None)
    return f"**u/{name}:**" if name else "**u/[deleted]:**"

def reddit_comment_signature(comment: Comment) -> str:
    a = "[deleted]" if comment.author is None else getattr(comment.author, "name", "[deleted]")
    body = getattr(comment, "body", "") or ""
    return f"{a}|{body[:120]}".strip()

# --------------------------
# Lemmy API
# --------------------------
def lemmy_login(force: bool = False) -> str:
    """
    Logs into Lemmy and caches the JWT token.
    Handles rate_limit_error gracefully with backoff and retry.
    """
    import time

    # ✅ Use cached token if available and not forced to refresh
    if not force and TOKEN_FILE.exists():
        data = load_json(TOKEN_FILE, {})
        jwt = data.get("jwt")
        if jwt:
            return jwt

    payload = {"username_or_email": LEMMY_USER, "password": LEMMY_PASS}
    url = f"{LEMMY_URL}/api/v3/user/login"

    # Retry loop with exponential backoff
    for attempt in range(5):
        # 🧩 Prevent rapid re-logins (avoid rate_limit_error)
        global LAST_LOGIN_TIME
        if "LAST_LOGIN_TIME" not in globals():
            LAST_LOGIN_TIME = 0

        elapsed = time.time() - LAST_LOGIN_TIME
        if elapsed < 60:
            wait = 60 - elapsed
            print(f"⏳ Too soon to log in again. Waiting {wait:.0f}s before retry...")
            time.sleep(wait)

        LAST_LOGIN_TIME = time.time()
        print(f"🔑 Logging in to {url} as {LEMMY_USER} (attempt {attempt + 1}/5)")
        r = requests.post(url, json=payload, timeout=30)

        # Handle rate limit gracefully
        if r.status_code == 400 and "rate_limit_error" in r.text:
            wait_time = 120 + attempt * 30
            print(f"⚠️ Lemmy rate limit hit. Waiting {wait_time}s before retry...")
            time.sleep(wait_time)
            continue

        if r.status_code != 200:
            raise RuntimeError(f"Lemmy login failed: {r.status_code} {r.text}")

        jwt = r.json().get("jwt")
        if not jwt:
            raise RuntimeError("No JWT returned by Lemmy login.")

        save_json(TOKEN_FILE, {"jwt": jwt, "cached_at": time.time()})
        print("✅ Logged into Lemmy (token cached)")

        # ✅ Now that we have a token, identify which user this is
        try:
            user_info = requests.get(
                f"{LEMMY_URL}/api/v3/site",
                headers={"Authorization": f"Bearer {jwt}"},
                timeout=15,
            )
            if user_info.ok:
                data = user_info.json()
                username = (
                    data.get("my_user", {})
                    .get("local_user_view", {})
                    .get("person", {})
                    .get("name", "(unknown)")
                )
                print(f"✅ Logged into Lemmy as: {username}")
            else:
                print(f"⚠️ Could not fetch user info: {user_info.status_code} {user_info.text[:120]}")
        except Exception as e:
            print(f"⚠️ Error verifying Lemmy user: {e}")

        return jwt  # ✅ Keep this — returns the working token

    raise RuntimeError("Lemmy login failed after multiple retries.")

import threading

_last_refresh_time = 0
_jwt_cache = None
_token_lock = threading.Lock()

def get_or_refresh_jwt(force: bool = False) -> str:
    """
    Returns a cached JWT if valid; refreshes it if expired or rejected by Lemmy.
    """
    global _jwt_cache
    with _token_lock:
        now = time.time()

        # Ensure _jwt_cache always has the expected structure
        if not isinstance(_jwt_cache, dict):
            _jwt_cache = {"token": None, "timestamp": 0}

        # Use cached token if valid
        if (
            not force
            and _jwt_cache.get("token")
            and (now - _jwt_cache.get("timestamp", 0)) < 3600
        ):
            # ✅ Test cached token before returning
            test = requests.get(
                f"{LEMMY_URL}/api/v3/site",
                headers={"Authorization": f"Bearer {_jwt_cache['token']}"},
                timeout=15,
            )
            if test.ok and "my_user" in test.text:
                return _jwt_cache["token"]
            else:
                print("⚠️ Cached JWT invalid — forcing refresh")

        # ♻️ Refresh if forced or token invalid
        print("♻️ Refreshing Lemmy JWT (scheduled or forced)...")
        jwt = lemmy_login(force=True)

        # ✅ Double-check token validity right after login
        verify = requests.get(
            f"{LEMMY_URL}/api/v3/site",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=15,
        )
        if not verify.ok or "my_user" not in verify.text:
            print("❌ New JWT failed verification, retrying login...")
            time.sleep(3)
            jwt = lemmy_login(force=True)

        _jwt_cache = {"token": jwt, "timestamp": now}
        save_json(TOKEN_FILE, {"jwt": jwt, "cached_at": now})
        print("✅ JWT refreshed and verified")
        return jwt

def get_existing_lemmy_comments(post_id: int) -> Dict[str, int]:
    url = f"{LEMMY_URL}/api/v3/comment/list"
    params = {"post_id": post_id, "sort": "Oldest", "limit": 50, "page": 1}
    existing: Dict[str, int] = {}
    while True:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            print(f"⚠️ Failed to list comments for post {post_id}: {r.status_code} {r.text}")
            break
        data = r.json()
        comments = data.get("comments", [])
        if not comments:
            break
        for c in comments:
            cid = c.get("comment", {}).get("id")
            content = c.get("comment", {}).get("content", "") or ""
            sig = (content[:120]).strip()
            if cid and sig:
                existing[sig] = cid
        if len(comments) < params["limit"]:
            break
        params["page"] += 1
    return existing

def post_lemmy_comment(jwt: str, post_id: int, content: str, parent_id: Optional[int]) -> Optional[int]:
    """
    Post a comment to Lemmy with smart retry logic:
    - Sends JWT via Authorization header (modern API requirement)
    - Refreshes JWT if expired
    - Handles rate limits and transient errors gracefully
    """
    url = f"{LEMMY_URL}/api/v3/comment"
    payload = {"post_id": post_id, "content": content}
    if parent_id is not None:
        payload["parent_id"] = parent_id

    headers = {"Authorization": f"Bearer {jwt}"}

    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=30)

            # 🟢 Success
            if r.status_code == 200:
                comment_id = (
                    r.json()
                    .get("comment_view", {})
                    .get("comment", {})
                    .get("id")
                )
                if comment_id:
                    print(f"✅ Comment posted (Lemmy ID={comment_id})")
                else:
                    print(f"⚠️ Lemmy responded 200 but no comment ID returned: {r.text[:180]}")
                return comment_id

            # 🔑 Expired/invalid JWT
            if r.status_code == 401 or "jwt" in r.text.lower() or "login" in r.text.lower():
                print("🔄 JWT appears invalid — refreshing once...")
                jwt = get_or_refresh_jwt(force=True)
                headers = {"Authorization": f"Bearer {jwt}"}
                time.sleep(2)
                continue

            # ⏳ Lemmy rate limit
            if r.status_code in (400, 429, 502, 503):
                if "rate_limit_error" in r.text:
                    wait_time = 60 * attempt
                    print(f"⚠️ Lemmy rate limit hit (attempt {attempt}/3). Waiting {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                print(f"⚠️ Lemmy responded {r.status_code}: {r.text[:180]}")
                time.sleep(5)
                continue

            # ❌ Other errors
            print(f"⚠️ Lemmy responded {r.status_code}: {r.text[:180]}")
            return None

        except requests.RequestException as e:
            print(f"⚠️ Network error posting comment: {e} (attempt {attempt}/3)")
            time.sleep(3)

    print("❌ All attempts failed posting a comment.")
    return None

# --------------------------
# Reddit + mapping helpers
# --------------------------
def reddit_client():
    missing = [k for k, v in [
        ("REDDIT_CLIENT_ID", REDDIT_CLIENT_ID),
        ("REDDIT_CLIENT_SECRET", REDDIT_CLIENT_SECRET),
        ("REDDIT_USERNAME", REDDIT_USERNAME),
        ("REDDIT_PASSWORD", REDDIT_PASSWORD),
    ] if not v]
    if missing:
        raise RuntimeError(f"Missing Reddit credentials in .env: {', '.join(missing)}")
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
        user_agent=REDDIT_USER_AGENT,
        ratelimit_seconds=5,
    )

def load_post_map() -> Dict[str, Any]:
    m = load_json(POST_MAP_FILE, {})
    if not m:
        print(f"ℹ️ No map for posts at {POST_MAP_FILE}. Run auto_mirror.py first.")
    return m

def load_comment_map() -> Dict[str, Dict[str, int]]:
    return load_json(COMMENT_MAP_FILE, {})

def save_comment_map(comment_map: Dict[str, Dict[str, int]]) -> None:
    save_json(COMMENT_MAP_FILE, comment_map)

def get_lemmy_post_id(entry: Any) -> Optional[int]:
    if isinstance(entry, int):
        return entry
    if isinstance(entry, dict):
        val = (
            entry.get("lemmy_id")
            or entry.get("lemmy_post_id")
            or entry.get("lemmyPostId")
        )
        if isinstance(val, int):
            return val
    return None

# --------------------------
# Mirroring logic
# --------------------------
def compose_comment_body(c: Comment) -> str:
    body = getattr(c, "body", "") or ""
    if body in ("[deleted]", "[removed]"):
        body = ""
    return f"{author_label(c)}\n\n{sanitise_markdown(body)}".strip()

def mirror_comments_for_post(reddit, jwt, reddit_post_id, lemmy_post_id, comment_map):
    per_post_map = comment_map.setdefault(reddit_post_id, {})
    existing_lemmy_sig = get_existing_lemmy_comments(lemmy_post_id) if REFRESH else {}

    submission = reddit.submission(id=reddit_post_id)
    submission.comments.replace_more(limit=None)

    def parent_reddit_id(c):
        pid = getattr(c, "parent_id", None) or ""
        return pid[3:] if pid.startswith("t1_") else None

    temp_parent_map = {**per_post_map}
    mirrored = skipped = total_processed = 0

    for c in submission.comments.list():
        if total_processed >= COMMENT_LIMIT_TOTAL:
            print(f"⏹️ Reached COMMENT_LIMIT_TOTAL={COMMENT_LIMIT_TOTAL} for {reddit_post_id}")
            break
        rid = getattr(c, "id", None)
        if not rid:
            continue
        total_processed += 1

        if rid in per_post_map and not REFRESH:
            skipped += 1
            continue

        content = compose_comment_body(c)
        content_sig = (content[:120]).strip()
        if REFRESH and rid not in per_post_map and content_sig in existing_lemmy_sig:
            per_post_map[rid] = existing_lemmy_sig[content_sig]
            skipped += 1
            continue

        parent_lemmy_id = temp_parent_map.get(parent_reddit_id(c))
        cid = post_lemmy_comment(jwt, lemmy_post_id, content, parent_lemmy_id)
        time.sleep(2)  # 🧩 Slow down comment posting to avoid Lemmy rate limits
        if cid:
            per_post_map[rid] = cid
            temp_parent_map[rid] = cid
            mirrored += 1
            time.sleep(COMMENT_SLEEP)
        else:
            print(f"⚠️ Failed to mirror comment {rid} on post {reddit_post_id}")

    return mirrored, skipped

# --------------------------
# Main
# --------------------------
def main():
    reddit = reddit_client()
    jwt = get_or_refresh_jwt()

    post_map = load_post_map()
    comment_map = load_comment_map()

    if not post_map:
        print("🕒 Finished comment mirror (no posts mapped yet).")
        return

    total_mirrored = total_skipped = 0
    for rp_id, entry in post_map.items():
        lemmy_post_id = get_lemmy_post_id(entry)
        if not lemmy_post_id:
            print(f"ℹ️ Skipping Reddit post {rp_id}: no Lemmy post ID in map.")
            continue

        print(f"🧵 Mirroring comments for Reddit post {rp_id} → Lemmy post {lemmy_post_id} (refresh={REFRESH})")
        m, s = mirror_comments_for_post(reddit, jwt, rp_id, lemmy_post_id, comment_map)
        total_mirrored += m
        total_skipped += s
        save_comment_map(comment_map)

    print(f"🧮 Comment mirror complete. Mirrored: {total_mirrored}, Skipped: {total_skipped}")

if __name__ == "__main__":
    main()
