#!/usr/bin/env python3
"""
Reddit → Lemmy Auto Mirror (Hybrid, quiet auto-refresh)
-------------------------------------------------------
- Token reuse for 23h (avoids duplicate-token bug)
- Quiet background auto-refresh: reload .env SUB_MAP + refresh Lemmy community cache every 6h
- Hot-load new subreddits from .env without restarting worker
- Robust media rendering (gallery, images, basic video link handling)
- Title sanitization + retry on invalid_post_title
- Rate-limit handling with exponential backoff
- SQLite + legacy JSON migration retained
"""

import os
import re
import sys
import json
import time
import math
import html
import queue
import errno
import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from html import unescape

import requests

from job_queue import JobDB
from db_cache import DB
from comment_mirror import mirror_comment_to_lemmy
from mirror_media import mirror_url

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ENV / PATHS
# ─────────────────────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Allow overriding .env path (default to repo root)
DOTENV_PATH = os.getenv("DOTENV_PATH", "/opt/Reddit-Mirror-2-Lemmy/.env")

LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER", "mirrorbot")
LEMMY_PASS = os.getenv("LEMMY_PASS", "password")

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

ENABLE_MEDIA_PREVIEW = os.getenv("ENABLE_MEDIA_PREVIEW", "true").lower() == "true"
EMBED_PERMALINK_FOOTER = os.getenv("EMBED_PERMALINK_FOOTER", "true").lower() == "true"
MAX_GALLERY_IMAGES = int(os.getenv("MAX_GALLERY_IMAGES", "10"))

POST_FETCH_LIMIT = os.getenv("POST_FETCH_LIMIT", "10")  # "10" or "all"
POST_COOLDOWN_SECS = int(os.getenv("POST_COOLDOWN_SECS", "10"))  # space between Lemmy posts

TOKEN_FILE = DATA_DIR / "token.json"
COMMUNITY_MAP_FILE = DATA_DIR / "community_map.json"
POST_MAP_FILE = DATA_DIR / "post_map.json"  # legacy JSON (read for migration only)

TOKEN_REUSE_HOURS = 23
COMMUNITY_REFRESH_HOURS = int(os.getenv("COMMUNITY_REFRESH_HOURS", "6"))
SLEEP_BETWEEN_CYCLES = int(os.getenv("SLEEP_BETWEEN_CYCLES", "900"))  # 15 min between full cycles
SUB_MAP_RELOAD_HOURS = int(os.getenv("SUB_MAP_RELOAD_HOURS", str(COMMUNITY_REFRESH_HOURS)))

# ─────────────────────────────────────────────
# SUBREDDIT → COMMUNITY MAP (boot value; will be hot-reloaded)
# ─────────────────────────────────────────────
def _parse_sub_map(raw: str) -> dict:
    mapping: dict[str, str] = {}
    for pair in (raw or "").split(","):
        if ":" in pair:
            k, v = pair.split(":", 1)
            mapping[k.strip().lower()] = v.strip().lower()
    return mapping

SUB_MAP: dict[str, str] = _parse_sub_map(os.getenv("SUB_MAP", "fosscad2:fosscad2,3d2a:3d2a,FOSSCADtoo:FOSSCADtoo"))

# ─────────────────────────────────────────────
# LOG SHORTCUT
# ─────────────────────────────────────────────
def log(msg: str):
    # Console-friendly timestamp + flush
    from datetime import timezone
    print(f"{datetime.now(timezone.utc).isoformat()} | {msg}", flush=True)

# ─────────────────────────────────────────────
# LEGACY POST MAP (for one-time migration)
# ─────────────────────────────────────────────
if POST_MAP_FILE.exists():
    try:
        legacy_post_map = json.loads(POST_MAP_FILE.read_text())
        log(f"🗂️ (legacy) Loaded {len(legacy_post_map)} posts from {POST_MAP_FILE}")
    except Exception as e:
        log(f"⚠️ Failed to read legacy post_map.json: {e}")
        legacy_post_map = {}
else:
    legacy_post_map = {}
    log("📂 No legacy post_map.json found (fresh start).")

# ─────────────────────────────────────────────
# JSON UTIL
# ─────────────────────────────────────────────
def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default if default is not None else {}
    try:
        with open(p, "r") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def save_json(path, data):
    p = Path(path)
    tmp = Path(str(p) + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(p)

# ─────────────────────────────────────────────
# TOKEN MGMT (23h reuse)
# ─────────────────────────────────────────────
token_state = {}
if TOKEN_FILE.exists():
    try:
        token_state = json.load(open(TOKEN_FILE))
    except Exception as e:
        log(f"⚠️ Failed to read token cache: {e}")

def lemmy_login(force=False):
    """Return a valid Lemmy JWT, reusing cached token for up to 23h."""
    global token_state

    if not force and token_state.get("jwt"):
        age = time.time() - token_state.get("ts", 0)
        if age < TOKEN_REUSE_HOURS * 3600:
            log(f"🔁 Using cached Lemmy token (age={int(age)}s)")
            return token_state["jwt"]

    log(f"🔑 Attempting fresh login to {LEMMY_URL} as {LEMMY_USER}")

    # Reuse very freshly refreshed token by another proc
    if TOKEN_FILE.exists():
        age = time.time() - TOKEN_FILE.stat().st_mtime
        if age < 60:
            try:
                data = json.load(open(TOKEN_FILE))
                if data.get("jwt"):
                    log(f"♻️ Using recently refreshed token (age={int(age)}s)")
                    token_state.update(data)
                    return data["jwt"]
            except Exception:
                pass

    r = requests.post(
        f"{LEMMY_URL}/api/v3/user/login",
        json={"username_or_email": LEMMY_USER, "password": LEMMY_PASS},
        timeout=20,
    )
    if r.status_code == 400 and "rate_limit" in r.text:
        log("⏳ Lemmy rate-limited login — waiting 30s before retry…")
        time.sleep(30)
        return lemmy_login(force=True)
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

def get_cached_jwt():
    try:
        data = json.load(open(TOKEN_FILE))
        return data.get("jwt")
    except Exception:
        return token_state.get("jwt")

# ─────────────────────────────────────────────
# MEDIA HELPERS
# ─────────────────────────────────────────────
def md_escape(text: str) -> str:
    if not text:
        return ""
    return text.replace("|", r"\|").replace("<", "&lt;").replace(">", "&gt;")

def to_md(text: str) -> str:
    if not text:
        return ""
    return unescape(text)

def is_image_url(u: str) -> bool:
    return bool(re.search(r"\.(png|jpe?g|gif|webp)(\?.*)?$", u or "", re.I))

def guess_imgur_direct(u: str) -> str | None:
    m = re.match(r"https?://(www\.)?imgur\.com/([A-Za-z0-9]+)$", u or "", re.I)
    if m:
        return f"https://i.imgur.com/{m.group(2)}.jpg"
    return None

def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def build_media_block_from_submission(sub) -> tuple[str, str | None]:
    """
    Constructs a Lemmy post body by mirroring all Reddit media locally.
    Ensures no outbound links to Reddit remain for images or videos.
    """
    from mirror_media import mirror_url
    body_parts, media_lines = [], []

    # Extract and clean self-text
    st = to_md(_get(sub, "selftext", "") or "")
    if st.strip():
        body_parts.append(st)

    # 1. Handle Reddit Galleries
    if bool(_get(sub, "is_gallery", False)) and _get(sub, "gallery_data") and _get(sub, "media_metadata"):
        try:
            items = _get(sub, "gallery_data", {}).get("items", [])[:MAX_GALLERY_IMAGES]
            media_meta = _get(sub, "media_metadata", {})
            for idx, it in enumerate(items, 1):
                media_id = it.get("media_id")
                meta = media_meta.get(media_id, {})
                
                # Identify source URL
                src = None
                if isinstance(meta.get("s"), dict):
                    s = meta["s"]
                    src = s.get("u") or s.get("gif") or s.get("mp4")
                
                # Fallback to preview if primary source is missing
                if not src and isinstance(meta.get("p"), list) and meta["p"]:
                    src = meta["p"][-1].get("u")
                
                if src:
                    # Mirror to local infrastructure
                    mirrored_src = mirror_url(src.replace("&amp;", "&"))
                    if mirrored_src:
                        caption = it.get("caption") or ""
                        cap = f" — {md_escape(caption)}" if caption else ""
                        media_lines.append(f"![Image {idx}]({mirrored_src}){cap}")
        except Exception as e:
            log(f"⚠️ Gallery mirroring failed: {e}")

    # 2. Handle Single Images and Videos
    # Only process if not already handled as a gallery
    elif not bool(_get(sub, "is_gallery", False)):
        url = _get(sub, "url", "")
        if url:
            # Mirror the URL (mirror_url now handles video downloading/hosting)
            mirrored_url = mirror_url(url)
            if mirrored_url:
                # Check extension for appropriate Markdown embedding
                low_url = mirrored_url.lower()
                if any(ext in low_url for ext in (".mp4", ".webm", ".mov")):
                    media_lines.append(f"![Video Preview]({mirrored_url})")
                else:
                    media_lines.append(f"![Image]({mirrored_url})")

    # Combine text and mirrored media
    if media_lines:
        if st.strip():
            body_parts.append("\n---\n")
        body_parts += media_lines

    return ("\n".join(body_parts).strip(), None)

# ─────────────────────────────────────────────
# COMMUNITY CACHE + LOOKUP
# ─────────────────────────────────────────────
def refresh_community_map(jwt):
    headers = {"Authorization": f"Bearer {jwt}"}
    try:
        r = requests.get(f"{LEMMY_URL}/api/v3/community/list", headers=headers, timeout=20)
        if not r.ok:
            # Quiet mode: only warn on failure
            log(f"⚠️ Failed to fetch communities: {r.status_code} {r.text[:200]}")
            return
        data = r.json()
        mapping = {c["community"]["name"].lower(): c["community"]["id"] for c in data.get("communities", [])}
        mapping["_fetched_at"] = time.time()
        save_json(COMMUNITY_MAP_FILE, mapping)
        # quiet success (no log)
    except Exception as e:
        log(f"⚠️ Community map refresh error: {e}")

def get_community_id(name: str, jwt: str) -> int:
    """
    Prefer direct name lookup endpoint, fallback to cache.
    Caches successful lookups back into community_map.json.
    """
    name = name.lower().strip()
    headers = {"Authorization": f"Bearer {jwt}"}

    # 1) Direct name lookup
    try:
        r = requests.get(f"{LEMMY_URL}/api/v3/community", params={"name": name}, headers=headers, timeout=15)
        if r.ok:
            data = r.json()
            if "community_view" in data:
                cid = data["community_view"]["community"]["id"]
                mapping = load_json(COMMUNITY_MAP_FILE, {})
                mapping[name] = cid
                mapping["_fetched_at"] = time.time()
                save_json(COMMUNITY_MAP_FILE, mapping)
                # quiet success
                return cid
        else:
            log(f"⚠️ Lemmy lookup failed for '{name}': {r.status_code} {r.text[:120]}")
    except Exception as e:
        log(f"⚠️ Exception during community lookup for '{name}': {e}")

    # 2) Fallback to cached map (refresh if stale)
    mapping = load_json(COMMUNITY_MAP_FILE, {})
    if not mapping or time.time() - mapping.get("_fetched_at", 0) > COMMUNITY_REFRESH_HOURS * 3600:
        refresh_community_map(jwt)
        mapping = load_json(COMMUNITY_MAP_FILE, {})

    for k, v in mapping.items():
        if k.lower() == name:
            return v

    raise RuntimeError(f"community lookup error: could not resolve '{name}' (case-insensitive)")

# ─────────────────────────────────────────────
# .ENV HOT-RELOAD (quiet unless changes/errors)
# ─────────────────────────────────────────────
def reload_sub_map():
    """
    Reload SUB_MAP from DOTENV_PATH (quiet unless changed).
    If the .env is missing, silently no-op.
    """
    global SUB_MAP
    try:
        from dotenv import dotenv_values
    except Exception:
        # If python-dotenv not installed, bail quietly
        return

    try:
        if not DOTENV_PATH or not Path(DOTENV_PATH).exists():
            return
        env = dotenv_values(DOTENV_PATH)
        new_raw = env.get("SUB_MAP", "")
        if not new_raw:
            return

        new_map = _parse_sub_map(new_raw)

        # Compare and log only if changed
        added = [k for k in new_map if k not in SUB_MAP]
        removed = [k for k in SUB_MAP if k not in new_map]

        if added or removed:
            SUB_MAP = new_map
            log(f"♻️ Reloaded SUB_MAP from .env (added={added or []}, removed={removed or []})")
        else:
            # quiet if no changes
            pass
    except Exception as e:
        log(f"⚠️ Failed to reload .env SUB_MAP: {e}")

def start_auto_refresh(jwt):
    """
    Quiet background thread:
      - initial: reload_sub_map() + refresh_community_map(jwt)
      - every SUB_MAP_RELOAD_HOURS: reload_sub_map()
      - every COMMUNITY_REFRESH_HOURS: refresh_community_map()
    Logs only on changes or errors.
    """
    def loop():
        last_community_refresh = time.time()
        try:
            reload_sub_map()
            refresh_community_map(jwt)
        except Exception as e:
            log(f"⚠️ Auto-refresh init failed: {e}")

        while True:
            try:
                # Sleep for the SUB_MAP_RELOAD_HOURS interval
                time.sleep(SUB_MAP_RELOAD_HOURS * 3600)
                reload_sub_map()

                # Refresh community map if its interval has passed
                if time.time() - last_community_refresh >= COMMUNITY_REFRESH_HOURS * 3600:
                    refresh_community_map(jwt)
                    last_community_refresh = time.time()

            except Exception as e:
                log(f"⚠️ Auto-refresh cycle error: {e}")

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    # quiet start (no log)

# ─────────────────────────────────────────────
# POST CREATION (rate-limit + title sanitization)
# ─────────────────────────────────────────────
def _sanitize_title(title: str, subreddit_name: str) -> str:
    title = (title or "").strip()
    title = html.unescape(title).replace("\n", " ").replace("\r", " ")
    # Remove control chars / zero-width chars
    title = re.sub(r"[\x00-\x1F\x7F]", "", title)
    title = title.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    if not title or len(title) < 3:
        title = f"Post from r/{subreddit_name} ({datetime.utcnow().strftime('%Y-%m-%d')})"
    if len(title) > 180:
        title = title[:177] + "…"
    return title

def _maybe_wait_between_posts():
    if POST_COOLDOWN_SECS > 0:
        time.sleep(POST_COOLDOWN_SECS)

def create_lemmy_post(subreddit_name, post, jwt, community_id):
    """
    Creates a Lemmy post using a 'no-loss' strategy. 
    Media is embedded in the body to ensure local hosting and permanent accessibility.
    """
    headers = {"Authorization": f"Bearer {jwt}"}
    
    try:
        # Construct the body with mirrored media links
        # link_override is ignored to prevent outbound Reddit links
        body_md, _ = build_media_block_from_submission(post)
    except Exception as e:
        log(f"⚠️ build_media_block_from_submission failed: {e}")
        body_md = post.get("selftext", "")

    # Sanitize the title for Lemmy requirements
    title = _sanitize_title(post.get("title") or "Untitled", subreddit_name)

    # Construct the payload as a text post only
    payload = {
        "name": title, 
        "community_id": community_id,
        "body": body_md
    }

    url = f"{LEMMY_URL}/api/v3/post"

    # Retry loop with exponential backoff for rate limits
    backoff = 10
    max_wait = 90
    attempts = 0
    
    while True:
        attempts += 1
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)

            # Handle Authentication Expired
            if r.status_code == 401:
                log("⚠️ Lemmy returned 401, refreshing token...")
                new_jwt = lemmy_login(force=True)
                headers["Authorization"] = f"Bearer {new_jwt}"
                continue

            text = r.text or ""

            # Handle Title Sanity
            if r.status_code == 400 and "invalid_post_title" in text:
                log("⚠️ Lemmy rejected title — applying extra sanitization...")
                payload["name"] = _sanitize_title(payload["name"], subreddit_name) + " "
                continue

            # Handle Rate Limits
            if r.status_code == 400 and "rate_limit_error" in text:
                wait = min(backoff, max_wait)
                log(f"⏳ Lemmy rate-limited post — sleeping {wait}s (attempt {attempts})...")
                time.sleep(wait)
                backoff = int(backoff * 1.7) + 5
                continue

            if not r.ok:
                raise RuntimeError(f"Lemmy post failed: {r.status_code} {text[:200]}")

            # Successfully created
            pid = r.json()["post_view"]["post"]["id"]
            log(f"✅ Posted '{post.get('title','Untitled')}' → Lemmy ID={pid}")

            _maybe_wait_between_posts()
            return pid

        except requests.exceptions.RequestException as e:
            log(f"⚠️ Connection error to Lemmy: {e}")
            if attempts >= 3:
                raise
            time.sleep(5)

# ─────────────────────────────────────────────
# COMMENTS
# ─────────────────────────────────────────────
def mirror_comments(sub, post_id, comments, jwt):
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

        payload = {"content": content, "post_id": int(post_id)}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            if r.status_code == 401:
                log("⚠️ Comment post 401, retrying with refreshed token…")
                new_jwt = lemmy_login(force=True)
                headers["Authorization"] = f"Bearer {new_jwt}"
                r = requests.post(url, json=payload, headers=headers, timeout=20)

            if r.status_code == 400 and "rate_limit" in r.text:
                time.sleep(10)
                continue

            if not r.ok:
                log(f"⚠️ Comment failed: {r.status_code} {r.text[:200]}")
                continue

            time.sleep(3)
        except Exception as e:
            log(f"⚠️ Error posting comment: {e}")
            continue

    log(f"✅ Mirrored {len(comments)} comments.")

# ─────────────────────────────────────────────
# ALT POST BODY (used by update_existing_posts)
# ─────────────────────────────────────────────
def build_post_body(sub):
    """
    Standardizes the post body construction for both new mirrors and updates.
    Ensures that all media is hosted locally and outbound Reddit links are stripped.
    """
    # Simply delegate to our primary mirroring logic to ensure consistency
    # across the entire application.
    content, _ = build_media_block_from_submission(sub)
    return content

# ─────────────────────────────────────────────
# MIGRATION (legacy JSON → SQLite)
# ─────────────────────────────────────────────
def migrate_legacy_json_to_sqlite(db: DB):
    if not legacy_post_map:
        return
    imported = skipped = 0
    for reddit_id, val in legacy_post_map.items():
        try:
            if isinstance(val, dict):
                lemmy_id = str(val.get("lemmy_id") or val.get("lemmy_post_id") or "")
                subreddit = val.get("subreddit") or None
            elif isinstance(val, int):
                lemmy_id = str(val)
                subreddit = None
            elif isinstance(val, str):
                lemmy_id = val
                subreddit = None
            else:
                continue
            if not lemmy_id:
                continue
            if db.get_lemmy_post_id(reddit_id):
                skipped += 1
                continue
            db.save_post(reddit_id, lemmy_id, subreddit)
            imported += 1
        except Exception as e:
            log(f"⚠️ Migration error for reddit_id={reddit_id}: {e}")
    log(f"📦 Migration complete: imported={imported}, skipped(existing)={skipped}.")

# ─────────────────────────────────────────────
# FETCH ONE SUBMISSION (used by mirror_post_to_lemmy & updater)
# ─────────────────────────────────────────────
def fetch_reddit_submission(submission_id: str):
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = "RedditToLemmyBridge/1.1.0 (by u/YourBotName)"

    base_url = f"https://www.reddit.com/comments/{submission_id}.json"
    headers = {"User-Agent": user_agent}

    # OAuth if creds provided
    if client_id and client_secret:
        token_url = "https://www.reddit.com/api/v1/access_token"
        auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
        data = {"grant_type": "client_credentials"}
        token_res = requests.post(token_url, auth=auth, data=data, headers=headers, timeout=15)
        if token_res.ok:
            token = token_res.json().get("access_token")
            headers["Authorization"] = f"bearer {token}"
            base_url = f"https://oauth.reddit.com/by_id/t3_{submission_id}.json"

    # Retry (429 backoff)
    for attempt in range(3):
        r = requests.get(base_url, headers=headers, timeout=15)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        if not r.ok:
            log(f"⚠️ Reddit fetch failed for {submission_id}: {r.status_code}")
            return None
        break

    time.sleep(2)

    try:
        data = r.json()
    except Exception as e:
        log(f"⚠️ Failed to parse Reddit JSON for {submission_id}: {e}")
        return None

    if isinstance(data, dict) and data.get("data") and data["data"].get("children"):
        return data["data"]["children"][0]["data"]
    # Alternate shape from /comments/ endpoint
    if isinstance(data, list) and data and isinstance(data[0], dict):
        listing = data[0].get("data", {}).get("children", [])
        if listing:
            return listing[0].get("data", None)

    log(f"⚠️ No Reddit data returned for {submission_id}")
    return None

# ─────────────────────────────────────────────
# UPDATE EXISTING POSTS (optional maintenance)
# ─────────────────────────────────────────────
def update_existing_posts():
    db = DB()
    start_time = time.time()

    post_map_path = DATA_DIR / "post_map.json"
    legacy_entries = {}
    if post_map_path.exists():
        try:
            legacy_entries = json.loads(post_map_path.read_text())
            log(f"🗂️ Loaded {len(legacy_entries)} legacy entries from post_map.json")
        except Exception as e:
            log(f"⚠️ Failed to read legacy post_map.json: {e}")

    db_path = Path("/opt/Reddit-Mirror-2-Lemmy/data") / "jobs.db"
    if not db_path.exists():
        log(f"❌ jobs.db not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")]
    if "posts" not in tables:
        log("❌ No 'posts' table found in jobs.db")
        conn.close()
        return

    #cur = conn.execute("SELECT reddit_post_id, lemmy_post_id FROM posts")
    cur = conn.execute("SELECT reddit_id, lemmy_id FROM posts")
    rows = cur.fetchall()
    conn.close()

    all_entries = {r[0]: r[1] for r in rows}
    all_entries.update(legacy_entries)

    if not all_entries:
        log("❌ No mirrored posts found in jobs.db or JSON.")
        return

    log(f"🔄 Updating {len(all_entries)} existing Lemmy posts with new media embeds…")

    jwt = get_cached_jwt() or lemmy_login(force=True)
    headers = {"Authorization": f"Bearer {jwt}"}
    success = 0

    for reddit_id, post_id in all_entries.items():
        try:
            sub_data = fetch_reddit_submission(reddit_id)
            if not sub_data:
                log(f"⚠️ Unable to fetch Reddit data for {reddit_id}")
                continue

            # Ensure the ID is an integer
            try:
                clean_id = int(str(post_id).strip())
            except ValueError:
                log(f"❌ Skipping {reddit_id}: Lemmy ID '{post_id}' is not a valid number.")
                continue

            # FIX: Get the Title and the new Body
            reddit_title = sub_data.get("title") or "Untitled"
            # Sanitize title to meet Lemmy's requirements
            clean_title = _sanitize_title(reddit_title, sub_data.get("subreddit", "mirror"))
            new_body = build_post_body(sub_data)

            # FIX: Updated URL to use /post/update
            update_url = f"{LEMMY_URL}/api/v3/post"

            # FIX: Added "name" field to the payload
            payload = {
                "post_id": clean_id, 
                "name": clean_title,  # This fixes the 'missing field name' error
                "body": new_body
            }

            # Using PUT for update as per Lemmy v3 API
            r = requests.put(update_url, json=payload, headers=headers, timeout=20)

            if r.status_code in (500, 502, 503):
                time.sleep(5)
                continue

            if r.status_code == 404:
                log(f"⚠️ Lemmy 404 updating post {reddit_id} (ID={post_id}).")
            elif not r.ok:
                log(f"⚠️ Failed updating {reddit_id} (Lemmy ID={post_id}): {r.status_code} {r.text[:120]}")
            else:
                success += 1

            time.sleep(1.5)
            if success and success % 25 == 0:
                time.sleep(5)

        except Exception as e:
            log(f"⚠️ Exception updating {reddit_id}: {e}")
            continue

    duration = time.time() - start_time
    log(f"✨ Done — updated {success}/{len(all_entries)} posts in {duration:.1f}s.")
# ─────────────────────────────────────────────
# MIRROR CORE (called by worker)
# ─────────────────────────────────────────────
async def mirror_post_to_lemmy(payload: dict):
    """
    Accepts {'reddit_id' or 'reddit_post_id': 'abc123'} and mirrors to Lemmy.
    Returns {'lemmy_id': int}
    """
    reddit_id = payload.get("reddit_id") or payload.get("reddit_post_id")
    if not reddit_id:
        raise ValueError(f"Missing reddit_id in payload: {payload}")

    db = DB()
    post_data = fetch_reddit_submission(reddit_id)
    if not post_data:
        # Gracefully skip missing/deleted/private Reddit posts
        from pathlib import Path as _Path
        db_path = _Path(__file__).parent / "data" / "jobs.db"
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE jobs SET status='skipped', updated_at=datetime('now') "
                "WHERE type='mirror_post' AND json_extract(payload, '$.reddit_post_id') = ?",
                (reddit_id,),
            )
            conn.commit()
            conn.close()
            log(f"🚫 Skipping missing Reddit post {reddit_id} — marked as skipped in DB.")
        except Exception as e:
            log(f"⚠️ Failed to mark missing post {reddit_id} as skipped: {e}")
        return {"lemmy_id": None}

    jwt = get_valid_token()
    subreddit = post_data.get("subreddit")
    if not subreddit:
        raise RuntimeError("Missing subreddit info in Reddit post")

    # Map subreddit → community (from hot-reloaded SUB_MAP if present; else same name)
    community_name = SUB_MAP.get(subreddit.lower(), subreddit.lower())

    comm_id = get_community_id(community_name, jwt)
    lemmy_id = create_lemmy_post(subreddit, post_data, jwt, comm_id)
    db.save_post(reddit_id, str(lemmy_id), subreddit)

    log(f"✅ Background mirror success: Reddit {reddit_id} → Lemmy {lemmy_id}")

    # Enqueue background comment mirror job
    try:
        from pathlib import Path as _Path
        db_path = _Path(__file__).parent / "data" / "jobs.db"
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT 1 FROM jobs WHERE type='mirror_comment' AND json_extract(payload, '$.reddit_id') = ?",
            (reddit_id,),
        )
        if not cur.fetchone():
            payload2 = {
                "reddit_id": reddit_id,
                "reddit_comment_id": f"auto_{reddit_id}",
                "lemmy_post_id": lemmy_id,
            }
            conn.execute(
                "INSERT INTO jobs (type, payload, status, retries, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "mirror_comment",
                    json.dumps(payload2),
                    "queued",
                    0,
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        conn.close()
    except Exception as e:
        log(f"⚠️ Failed to enqueue comment mirror for {reddit_id}: {e}")

    return {"lemmy_id": lemmy_id}

# ─────────────────────────────────────────────
# POLLER (enqueue jobs from subreddits)
# ─────────────────────────────────────────────
def map_subreddit_to_community(subreddit_name: str) -> str | None:
    return subreddit_name.lower()

def mirror_once(subreddit_name: str, test_mode: bool = False):
    print(f"▶️ comment_mirror.py starting (refresh=False)")
    print(f"🔁 Fetching subreddit: r/{subreddit_name}")

    db = JobDB()
    community_name = map_subreddit_to_community(subreddit_name)
    if not community_name:
        print(f"⚠️ No community mapping found for r/{subreddit_name}")
        return

    limit_str = str(POST_FETCH_LIMIT).lower()
    fetch_all = limit_str in ("all", "none", "0")
    per_page = 100 if fetch_all else int(POST_FETCH_LIMIT)
    max_batches = 10

    print(f"🔄 Live mode: Fetching from Reddit API (limit={'all' if fetch_all else per_page})…")

    after = None
    fetched = 0

    while True:
        params = {"limit": per_page}
        if after:
            params["after"] = after

        url = f"https://www.reddit.com/r/{subreddit_name}/new.json"
        headers = {"User-Agent": "RedditToLemmyBridge/1.1 (by u/YourBotName)"}
        # --- enhanced rate-limit handling ---
        for attempt in range(5):
            r = requests.get(url, params=params, headers=headers, timeout=20)

            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "10"))
                wait = min(retry_after, 60)  # cap at 1 min
                print(f"⚠️ Reddit API rate-limited r/{subreddit_name} — waiting {wait}s before retry ({attempt+1}/5)…")
                time.sleep(wait)
                continue

            if not r.ok:
                print(f"⚠️ Reddit API error {r.status_code} for r/{subreddit_name}")
                break

            # success, exit retry loop
            break
        # --- end patch ---

        data = r.json().get("data", {})
        children = data.get("children", [])
        if not children:
            break

        for item in children:
            submission = item["data"]
            reddit_post_id = submission["id"]
            title = submission.get("title", "[untitled]")

            print(f"🪶 Found Reddit post {reddit_post_id}: {title}")

            cur = db.conn.execute(
                "SELECT id FROM jobs WHERE type='mirror_post' "
                "AND json_extract(payload, '$.reddit_post_id') = ?",
                (reddit_post_id,),
            )
            existing_post_job = cur.fetchone()

            if not existing_post_job:
                if test_mode:
                    print(f"🧪 [TEST MODE] Would enqueue mirror_post for Reddit {reddit_post_id}")
                else:
                    print(f"🪶 Enqueuing mirror_post job for Reddit {reddit_post_id}")
                    db.enqueue(
                        "mirror_post",
                        {
                            "reddit_post_id": reddit_post_id,
                            "community_name": community_name,
                        },
                    )
            else:
                print(f"⏭️ mirror_post job already exists for Reddit {reddit_post_id}")

            fetched += 1

        after = data.get("after")
        if not after:
            break

        if not fetch_all or fetched >= per_page * max_batches:
            break

        print(f"➡️ Fetched {fetched} posts so far — continuing to next page…")
        time.sleep(2)

    print(f"✨ Done — processed {fetched} posts from r/{subreddit_name}.")

def mirror_loop(db: JobDB):
    import praw

    jwt = get_valid_token()
    start_auto_refresh(jwt)  # quiet background refresher

    # (praw used only for auth UA baseline; polling uses public JSON here)
    reddit = praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent="reddit-lemmy-bridge",
    )

    while True:
        log("🔁 Running refresh cycle…")
        # NOTE: SUB_MAP may be updated silently by the background thread
        items = list(SUB_MAP.items())  # snapshot to avoid mid-iteration mutation
        for reddit_sub, lemmy_comm in items:
            try:
                mirror_once(subreddit_name=reddit_sub, test_mode=TEST_MODE)
            except Exception as e:
                log(f"⚠️ Error while mirroring r/{reddit_sub} → c/{lemmy_comm}: {e}")

        log(f"🕒 Sleeping {SLEEP_BETWEEN_CYCLES}s…")
        import random
        jitter = random.randint(-60, 60)
        time.sleep(max(60, SLEEP_BETWEEN_CYCLES + jitter))


# ─────────────────────────────────────────────
# CLI: update-existing
# ─────────────────────────────────────────────
if len(sys.argv) > 1 and sys.argv[1] == "--update-existing":
    update_existing_posts()
    sys.exit(0)

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log("🔧 reddit → lemmy bridge starting…")

    conn = sqlite3.connect("data/jobs.db")
    db = JobDB(conn)

    migrate_legacy_json_to_sqlite(DB())

    try:
        mirror_loop(db)
    except Exception as e:
        log(f"❌ Mirror loop failed: {e}")
