#!/usr/bin/env python3
"""
Reddit → Lemmy Auto Mirror
Stable production-ready version + SQLite cache + JSON migration
----------------------------------------------------------
- Token reuse for 23h (prevents Lemmy duplicate-token bug)
- Auto refresh community map every 6h
- Rich formatting with Reddit permalinks and media
- Comment mirroring with persistent mapping
- Clean logging for Docker-based deployment
- NEW: SQLite cache at /app/data/bridge_cache.db
- NEW: One-time migration from post_map.json → SQLite (keeps JSON as backup)
"""

import os
import time
import json
import requests
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import sys
import re
from html import unescape
from job_queue import JobDB
from db_cache import DB
from comment_mirror import mirror_comment_to_lemmy
import asyncio
import logging
#from community_cache import map_subreddit_to_community

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ENV CONFIG
# ─────────────────────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER", "mirrorbot")
LEMMY_PASS = os.getenv("LEMMY_PASS", "password")

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

ENABLE_MEDIA_PREVIEW = os.getenv("ENABLE_MEDIA_PREVIEW", "true").lower() == "true"
EMBED_PERMALINK_FOOTER = os.getenv("EMBED_PERMALINK_FOOTER", "true").lower() == "true"
MAX_GALLERY_IMAGES = int(os.getenv("MAX_GALLERY_IMAGES", "10"))

POST_FETCH_LIMIT = os.getenv("POST_FETCH_LIMIT", "10")  # "10" or "all"

# ─────────────────────────────────────────────
# COMMUNITY CACHE HELPER
# ─────────────────────────────────────────────
from community_cache import get_cache, resolve_community_id

def map_subreddit_to_community(subreddit_name: str) -> str | None:
    """
    Maps a Reddit subreddit name to its Lemmy community name.
    Falls back to matching names directly (e.g., r/fosscad2 → c/fosscad2).
    """
    # Example: you can later extend this to handle custom mappings
    return subreddit_name.lower()

# ─────────────────────────────────────────────
# SUBREDDIT → COMMUNITY MAP
# ─────────────────────────────────────────────
SUB_MAP = {}
raw_sub_map = os.getenv("SUB_MAP", "fosscad2:fosscad2,3d2a:3D2A,FOSSCADtoo:FOSSCADtoo")
for pair in raw_sub_map.split(","):
    if ":" in pair:
        k, v = pair.split(":", 1)
        SUB_MAP[k.strip().lower()] = v.strip().lower()

TOKEN_FILE = DATA_DIR / "token.json"
COMMUNITY_MAP_FILE = DATA_DIR / "community_map.json"
POST_MAP_FILE = DATA_DIR / "post_map.json"  # legacy JSON (read for migration only)

TOKEN_REUSE_HOURS = 23
COMMUNITY_REFRESH_HOURS = 6
SLEEP_BETWEEN_CYCLES = 900  # 15 min between full cycles

token_state = {}

def log(msg: str):
    from datetime import datetime, timezone
    print(f"{datetime.now(timezone.utc).isoformat()} | {msg}", flush=True)

def reload_sub_map():
    """Reload SUB_MAP from .env file every refresh cycle."""
    global SUB_MAP
    try:
        from dotenv import dotenv_values
        env = dotenv_values("/opt/Reddit-Mirror-2-Lemmy/.env")  # adjust if path differs
        new_map_raw = env.get("SUB_MAP", "")
        if not new_map_raw:
            return

        new_map = {}
        for pair in new_map_raw.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                new_map[k.strip().lower()] = v.strip().lower()

        added = [k for k in new_map if k not in SUB_MAP]
        removed = [k for k in SUB_MAP if k not in new_map]
        SUB_MAP = new_map

        if added or removed:
            log(f"♻️ Reloaded SUB_MAP from .env (added={added}, removed={removed})")
        else:
            log("✅ SUB_MAP reloaded (no changes)")
    except Exception as e:
        log(f"⚠️ Failed to reload .env SUB_MAP: {e}")

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
    log(f"🔑 Attempting fresh login to {LEMMY_URL} as {LEMMY_USER}")

    # if another process just refreshed it within 60s, reuse it
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
        log("⏳ Lemmy rate-limited login — waiting 30s before retry...")
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

# ─────────────────────────────────────────────
# UPDATE POSTS / MEDIA UTILS
# ─────────────────────────────────────────────
def get_cached_jwt():
    token_path = Path(DATA_DIR) / "token.json"
    if token_path.exists():
        try:
            import json
            with open(token_path, "r") as f:
                data = json.load(f)
            return data.get("jwt")
        except Exception:
            pass
    return token_state.get("jwt") if "token_state" in globals() else None

def md_escape(text: str) -> str:
    if not text:
        return ""
    return text.replace("|", r"\|").replace("<", "&lt;").replace(">", "&gt;")

def to_md(text: str) -> str:
    if not text:
        return ""
    return unescape(text)

def is_image_url(u: str) -> bool:
    return bool(re.search(r"\.(png|jpe?g|gif|webp)(\?.*)?$", u, re.I))

def guess_imgur_direct(u: str) -> str | None:
    m = re.match(r"https?://(www\.)?imgur\.com/([A-Za-z0-9]+)$", u, re.I)
    if m:
        return f"https://i.imgur.com/{m.group(2)}.jpg"
    return None

def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

def build_media_block_from_submission(sub) -> tuple[str, str | None]:
    # Leave objects as-is; we’ll read via _get
    body_parts, media_lines = [], []

    st = to_md(_get(sub, "selftext", "") or "")
    if st.strip():
        body_parts.append(st)

    # Gallery
    if bool(_get(sub, "is_gallery", False)) and _get(sub, "gallery_data") and _get(sub, "media_metadata"):
        try:
            items = _get(sub, "gallery_data", {}).get("items", [])[:MAX_GALLERY_IMAGES]
            media_meta = _get(sub, "media_metadata", {})
            for idx, it in enumerate(items, 1):
                media_id = it.get("media_id")
                meta = media_meta.get(media_id, {})
                src = None
                if isinstance(meta.get("s"), dict):
                    s = meta["s"]
                    src = s.get("u") or s.get("gif") or s.get("mp4")
                if not src and isinstance(meta.get("p"), list) and meta["p"]:
                    src = meta["p"][-1].get("u")
                caption = it.get("caption") or ""
                if src:
                    cap = f" — {md_escape(caption)}" if caption else ""
                    media_lines.append(f"![Image {idx}]({src}){cap}")
        except Exception:
            pass

    # Single image / imgur
    url = _get(sub, "url", "") or ""
    domain = _get(sub, "domain", "") or ""
    if (domain in ("i.redd.it", "i.imgur.com") or is_image_url(url) or guess_imgur_direct(url)):
        img = url
        if img and not is_image_url(img):
            guess = guess_imgur_direct(img)
            if guess:
                img = guess
        if img:
            media_lines.append(f"![Image]({img})")

    # Reddit hosted video
    if domain == "v.redd.it" and _get(sub, "media"):
        try:
            rv = _get(sub, "media", {}).get("reddit_video") or {}
            mp4 = rv.get("fallback_url")
            if mp4:
                media_lines.append(f"[🎬 View video on Reddit]({_get(sub, 'url', '')})")
        except Exception:
            pass

    # External link (non-self)
    if url and not _get(sub, "is_self", False) and not media_lines:
        media_lines.append(f"[External link]({url})")

    if media_lines:
        if st.strip():
            body_parts.append("\n---\n")
        body_parts += media_lines

    if EMBED_PERMALINK_FOOTER:
        permalink = _get(sub, "permalink", None)
        if permalink:
            if not permalink.startswith("http"):
                permalink = f"https://www.reddit.com{permalink}"
            body_parts.append("\n\n---\n")
            body_parts.append(f"🔗 **Source:** [View original post on Reddit]({permalink})")

    return ("\n".join(body_parts).strip(), None)

# ─────────────────────────────────────────────
# COMMUNITY CACHE
# ─────────────────────────────────────────────
def refresh_community_map(jwt):
    log("🌐 Refreshing community map from Lemmy...")
    headers = {"Authorization": f"Bearer {jwt}"}
    r = requests.get(f"{LEMMY_URL}/api/v3/community/list", headers=headers, timeout=20)
    if not r.ok:
        log(f"⚠️ Failed to fetch communities: {r.status_code} {r.text[:200]}")
        return

    data = r.json()
    mapping = {c["community"]["name"].lower(): c["community"]["id"] for c in data.get("communities", [])}
    mapping["_fetched_at"] = time.time()
    save_json(COMMUNITY_MAP_FILE, mapping)
    log(f"✅ Saved {len(mapping)-1} communities to map.")

# ─────────────────────────────────────────────
# AUTO REFRESH TASK
# ─────────────────────────────────────────────
import threading

def start_auto_refresh(jwt):
    """Background thread to refresh Lemmy community map every few hours."""
    def loop():
        reload_sub_map()  # 🔄 Load once immediately
        refresh_community_map(jwt)
        while True:
            try:
                reload_sub_map()
                refresh_community_map(jwt)
            except Exception as e:
                log(f"⚠️ Auto-refresh failed: {e}")
            time.sleep(COMMUNITY_REFRESH_HOURS * 3600)

def get_community_id(name: str, jwt: str):
    """
    Look up a community by name using Lemmy's direct /api/v3/community?name= endpoint.
    Automatically refreshes the cached map if needed.
    """
    name = name.lower().strip()
    headers = {"Authorization": f"Bearer {jwt}"}

    # 1️⃣ Direct lookup (most reliable)
    try:
        r = requests.get(f"{LEMMY_URL}/api/v3/community", params={"name": name}, headers=headers, timeout=15)
        if r.ok:
            data = r.json()
            if "community_view" in data:
                cid = data["community_view"]["community"]["id"]
                # cache result locally for later
                mapping = load_json(COMMUNITY_MAP_FILE, {})
                mapping[name] = cid
                mapping["_fetched_at"] = time.time()
                save_json(COMMUNITY_MAP_FILE, mapping)
                log(f"🔎 Found existing Lemmy community: {name} → {cid}")
                return cid
        else:
            log(f"⚠️ Lemmy lookup failed for '{name}': {r.status_code} {r.text[:100]}")
    except Exception as e:
        log(f"⚠️ Exception during Lemmy community lookup for {name}: {e}")

    # 2️⃣ Fallback to cached map
    mapping = load_json(COMMUNITY_MAP_FILE, {})
    if not mapping or time.time() - mapping.get("_fetched_at", 0) > COMMUNITY_REFRESH_HOURS * 3600:
        refresh_community_map(jwt)
        mapping = load_json(COMMUNITY_MAP_FILE, {})

    for k, v in mapping.items():
        if k.lower() == name:
            return v

    raise RuntimeError(f"community lookup error: could not resolve '{name}' (case-insensitive)")

# ─────────────────────────────────────────────
# POST CREATION
# ─────────────────────────────────────────────
def create_lemmy_post(subreddit_name, post, jwt, community_id):
    headers = {"Authorization": f"Bearer {jwt}"}
    try:
        body_md, link_override = build_media_block_from_submission(post)
    except Exception as e:
        log(f"⚠️ build_media_block_from_submission failed: {e}")
        body_md, link_override = (post.get("selftext", ""), None)

    import html
    title = (post.get("title") or "Untitled").strip()
    title = html.unescape(title).replace("\n", " ").replace("\r", " ")
    if len(title) > 180:
        title = title[:177] + "…"

    payload = {"name": title, "community_id": community_id}
    if body_md:
        payload["body"] = body_md
    if link_override:
        payload["url"] = link_override

    url = f"{LEMMY_URL}/api/v3/post"
    r = requests.post(url, json=payload, headers=headers, timeout=20)

    if r.status_code == 401:
        log("⚠️ Lemmy returned 401, refreshing token once and retrying…")
        new_jwt = lemmy_login(force=True)
        headers["Authorization"] = f"Bearer {new_jwt}"
        r = requests.post(url, json=payload, headers=headers, timeout=20)

    if not r.ok:
        raise RuntimeError(f"Lemmy post failed: {r.status_code} {r.text[:200]}")

    pid = r.json()["post_view"]["post"]["id"]
    log(f"✅ Posted '{post['title']}' (Lemmy ID={pid})")
    return pid

# ─────────────────────────────────────────────
# COMMENTS (unchanged behavior)
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
                log("⏳ Rate limited — sleeping 10s before next comment…")
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
# ENHANCED POST BODY (kept from previous version)
# ─────────────────────────────────────────────
def build_post_body(sub):
    if not isinstance(sub, dict):
        sub = getattr(sub, "__dict__", {})

    body = sub.get("selftext", "") or ""
    url = sub.get("url", "")
    domain = sub.get("domain", "")
    title = sub.get("title", "Untitled Post")

    video_domains = ("youtube.com", "youtu.be", "rumble.com", "odysee.com", "streamable.com", "v.redd.it")
    image_domains = ("i.redd.it", "i.imgur.com", "imgur.com", "redd.it")
    parts = []

    parts.append(f"### {title}\n")

    if any(d in url for d in video_domains):
        if "youtu" in url:
            embed = url.replace("watch?v=", "embed/").replace("youtu.be/", "youtube.com/embed/")
            parts.append(f'<iframe width="560" height="315" src="{embed}" frameborder="0" allowfullscreen></iframe>\n')
        elif "rumble.com" in url:
            parts.append(f"[▶️ Watch on Rumble]({url})\n")
        elif "odysee.com" in url:
            parts.append(f"[▶️ Watch on Odysee]({url})\n")
        elif "streamable.com" in url:
            parts.append(f"[▶️ Watch on Streamable]({url})\n")
        elif "v.redd.it" in url:
            thumb = sub.get("thumbnail", "")
            if thumb and thumb.startswith("http"):
                parts.append(f"![Video thumbnail]({thumb})\n")
            parts.append(f"[🎥 Watch Reddit Video]({url})\n")
        elif url.lower().endswith((".mp4", ".webm", ".mov")):
            parts.append(
                f"<video controls width='100%'><source src='{url}' type='video/mp4'>Your browser does not support HTML5 video.</video>\n"
            )
        else:
            parts.append(f"[▶️ Watch Video]({url})\n")
    elif sub.get("is_gallery") and "media_metadata" in sub:
        gallery = sub["media_metadata"]
        parts.append("🖼️ **Gallery Preview:**\n\n")
        count = 0
        for item_id, meta in gallery.items():
            src = meta.get("s", {}).get("u") or meta.get("p", [{}])[-1].get("u", "")
            caption = meta.get("caption", "")
            if src:
                parts.append(f"![{caption}]({src})\n")
                if caption:
                    parts.append(f"*{caption}*\n")
            count += 1
            if count % 2 == 0:
                parts.append("\n")
        parts.append(f"\n*View full gallery on [Reddit]({sub.get('permalink', '')})*\n")
    elif any(d in url for d in image_domains):
        parts.append(f"![media preview]({url})\n")
        if "gallery" in url or "album" in url:
            parts.append(f"*Gallery:* [View full post on Reddit]({sub.get('permalink', '')})\n")
    elif url and not sub.get("is_self"):
        parts.append(f"[Original Link]({url})\n")

    if body.strip():
        parts.append("\n---\n")
        parts.append(body.strip())

    permalink = sub.get("permalink") if isinstance(sub, dict) else getattr(sub, "permalink", None)
    if permalink:
        if not permalink.startswith("http"):
            permalink = f"https://www.reddit.com{permalink}"
        parts.append(f"\n\n---\n[View original post on Reddit]({permalink})")

    return "\n".join(parts).strip()

# ─────────────────────────────────────────────
# MAIN MIRROR LOOP + SQLite migration
# ─────────────────────────────────────────────
def migrate_legacy_json_to_sqlite(db: DB):
    """One-time import of legacy JSON post_map into SQLite (keeps JSON intact)."""
    if not legacy_post_map:
        log("ℹ️ No legacy post_map.json entries to migrate.")
        return

    imported = skipped = 0
    for reddit_id, val in legacy_post_map.items():
        try:
            # support dict-based entries {"title":..., "lemmy_id":..., "timestamp":...}
            if isinstance(val, dict):
                lemmy_id = str(val.get("lemmy_id") or val.get("lemmy_post_id") or "")
                subreddit = val.get("subreddit") or None
            elif isinstance(val, int):
                lemmy_id = str(val)
                subreddit = None
            elif isinstance(val, str):
                # Some older maps may store lemmy id as string
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

    log(f"📦 Migration complete: imported={imported}, skipped(existing)={skipped} (JSON kept as backup).")

def mirror_once(subreddit_name: str, test_mode: bool = False):
    """
    Mirrors posts from a single subreddit into its mapped Lemmy community.
    Supports pagination to fetch >100 posts when POST_FETCH_LIMIT='all'.
    """
    print(f"▶️ comment_mirror.py starting (refresh=False)")
    print(f"🔁 Fetching subreddit: r/{subreddit_name}")

    db = JobDB()
    community_name = map_subreddit_to_community(subreddit_name)
    if not community_name:
        print(f"⚠️ No community mapping found for r/{subreddit_name}")
        return

    # Determine fetch limit
    limit_str = str(POST_FETCH_LIMIT).lower()
    fetch_all = limit_str in ("all", "none", "0")
    per_page = 100 if fetch_all else int(POST_FETCH_LIMIT)
    max_batches = 10  # safety guard: up to ~1000 posts total

    print(f"🔄 Live mode: Fetching from Reddit API "
          f"(limit={'all' if fetch_all else per_page})…")

    after = None
    fetched = 0

    while True:
        params = {"limit": per_page}
        if after:
            params["after"] = after

        url = f"https://www.reddit.com/r/{subreddit_name}/new.json"
        headers = {"User-Agent": "RedditToLemmyBridge/1.1 (by u/YourBotName)"}
        r = requests.get(url, params=params, headers=headers, timeout=20)

        if not r.ok:
            print(f"⚠️ Reddit API error {r.status_code} for r/{subreddit_name}")
            break

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

        # Safety limit: prevent infinite loops
        if not fetch_all or fetched >= per_page * max_batches:
            break

        print(f"➡️ Fetched {fetched} posts so far — continuing to next page…")
        time.sleep(2)  # polite delay for Reddit API rate limits

    print(f"✨ Done — processed {fetched} posts from r/{subreddit_name}.")

def mirror_loop(db: JobDB):
    """Continuously mirror Reddit posts/comments to Lemmy communities."""
    import praw

    jwt = get_valid_token()
    start_auto_refresh(jwt)
    reddit = praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent="reddit-lemmy-bridge",
    )

    while True:
        log("🔁 Running refresh cycle...")
        for reddit_sub, lemmy_comm in SUB_MAP.items():
            try:
                mirror_once(subreddit_name=reddit_sub, test_mode=TEST_MODE)
            except Exception as e:
                log(f"⚠️ Error while mirroring r/{reddit_sub} → c/{lemmy_comm}: {e}")

        log(f"🕒 Sleeping {SLEEP_BETWEEN_CYCLES}s...")
        time.sleep(SLEEP_BETWEEN_CYCLES)

# ─────────────────────────────────────────────
# UPDATE EXISTING POSTS (unchanged)
# ─────────────────────────────────────────────
def fetch_reddit_submission(submission_id: str):
    """
    Fetches a single Reddit submission's JSON data safely with rate limit handling.
    Supports both authenticated (via Reddit API keys) and unauthenticated modes.
    """

    import os
    import time
    import requests

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = "RedditToLemmyBridge/1.1.0 (by u/YourBotName)"

#    base_url = f"https://www.reddit.com/by_id/t3_{submission_id}.json"
    base_url = f"https://www.reddit.com/comments/{submission_id}.json"
    headers = {"User-Agent": user_agent}

    # 🔐 Prefer Reddit OAuth API if credentials exist
    if client_id and client_secret:
        token_url = "https://www.reddit.com/api/v1/access_token"
        auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
        data = {"grant_type": "client_credentials"}
        token_res = requests.post(token_url, auth=auth, data=data, headers=headers, timeout=15)

        if token_res.ok:
            token = token_res.json().get("access_token")
            headers["Authorization"] = f"bearer {token}"
            base_url = f"https://oauth.reddit.com/by_id/t3_{submission_id}.json"
        else:
            print("⚠️ Failed to get Reddit OAuth token, falling back to public mode.")

    # 🚦 Fetch submission with retry & cooldown
    for attempt in range(3):
        r = requests.get(base_url, headers=headers, timeout=15)
        if r.status_code == 429:
            print(f"⏳ Reddit rate limited (429) — sleeping 5s (attempt {attempt + 1}/3)...")
            time.sleep(5 * (attempt + 1))
            continue  # retry
        if not r.ok:
            print(f"⚠️ Reddit fetch failed for {submission_id}: {r.status_code}")
            return None

        # ✅ Success
        break

    # 🌙 Gentle delay between requests to avoid hammering the API
    time.sleep(2)

    try:
        data = r.json()
    except Exception as e:
        print(f"⚠️ Failed to parse Reddit JSON for {submission_id}: {e}")
        return None

    if not data.get("data") or not data["data"].get("children"):
        print(f"⚠️ No Reddit data returned for {submission_id}")
        return None

    return data["data"]["children"][0]["data"]

def update_existing_posts():
    """
    Update existing mirrored posts using the jobs.db database.
    This rebuilds Lemmy post bodies to include new gallery/video embeds
    without re-mirroring or duplicating posts.
    """
    import sqlite3
    import time
    from db_cache import DB
    db = DB()

    start_time = time.time()

    # Try legacy JSON first (backward compatibility)
    post_map_path = DATA_DIR / "post_map.json"
    legacy_entries = {}
    if post_map_path.exists():
        try:
            legacy_entries = json.loads(post_map_path.read_text())
            log(f"🗂️ Loaded {len(legacy_entries)} legacy entries from post_map.json")
        except Exception as e:
            log(f"⚠️ Failed to read legacy post_map.json: {e}")

    # Load entries from jobs.db instead of bridge_cache.db
    db_path = Path("/opt/Reddit-Mirror-2-Lemmy/data") / "jobs.db"
    if not db_path.exists():
        log(f"❌ jobs.db not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)

    # Verify that the table exists and fetch Reddit→Lemmy mappings
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")]
    if "posts" not in tables:
        log("❌ No 'posts' table found in jobs.db")
        conn.close()
        return

    cur = conn.execute("SELECT reddit_post_id, lemmy_post_id FROM posts")
    rows = cur.fetchall()
    conn.close()

    all_entries = {r[0]: r[1] for r in rows}
    all_entries.update(legacy_entries)

    if not all_entries:
        log("❌ No mirrored posts found in jobs.db or JSON.")
        return

    log(f"🔄 Updating {len(all_entries)} existing Lemmy posts with new media embeds...")

    jwt = get_cached_jwt() or lemmy_login(force=True)
    headers = {"Authorization": f"Bearer {jwt}"}
    success = 0

    for reddit_id, post_id in all_entries.items():
        try:
            sub_data = fetch_reddit_submission(reddit_id)
            if not sub_data:
                log(f"⚠️ Unable to fetch Reddit data for {reddit_id}")
                continue

            # Ensure gallery and media keys exist
            for key in ["is_gallery", "media_metadata", "thumbnail"]:
                sub_data.setdefault(key, None)

            new_body = build_post_body(sub_data)

            update_url = f"{LEMMY_URL}/api/v3/post"
            payload = {"post_id": post_id, "body": new_body}
            r = requests.put(update_url, json=payload, headers=headers, timeout=20)

            # 🧩 Improvement #1 — retry on transient server errors
            if r.status_code in (500, 502, 503):
                log(f"⚠️ Lemmy temporarily unavailable (status={r.status_code}), retrying after 5s…")
                time.sleep(5)
                continue

            if r.status_code == 404:
                log(f"⚠️ Lemmy 404 updating post {reddit_id} (ID={post_id}) — may have been deleted/unlisted.")
            elif not r.ok:
                log(f"⚠️ Failed updating {reddit_id} (Lemmy ID={post_id}): {r.status_code} {r.text[:120]}")
            else:
                success += 1
                log(f"✅ Updated '{sub_data.get('title', 'Untitled')}' (Lemmy ID={post_id})")

            # 🧩 Improvement #2 — small rate-limit buffer
            time.sleep(1.5)
            if success % 25 == 0:
                log("⏳ Taking a short 5-second pause to respect API rate limits…")
                time.sleep(5)

        except Exception as e:
            log(f"⚠️ Exception updating {reddit_id}: {e}")
            continue

    # 🧩 Improvement #3 — log total duration
    duration = time.time() - start_time
    log(f"✨ Done — updated {success}/{len(all_entries)} posts in {duration:.1f}s.")

# ─────────────────────────────────────────────
# UPDATE TRIGGER
# ─────────────────────────────────────────────
if len(sys.argv) > 1 and sys.argv[1] == "--update-existing":
    update_existing_posts()
    sys.exit(0)

async def mirror_post_to_lemmy(payload: dict):

    """
    Background-safe synchronous function.
    Accepts {'reddit_id': 't3_abc123'} and mirrors that post to Lemmy.
    Returns {'lemmy_id': int}.
    """

    from db_cache import DB

    reddit_id = payload.get("reddit_id") or payload.get("reddit_post_id")
    if not reddit_id:
        raise ValueError(f"Missing reddit_id in payload: {payload}")

    db = DB()
    post_data = fetch_reddit_submission(reddit_id)
    if not post_data:
        raise RuntimeError(f"Failed to fetch Reddit submission {reddit_id}")

    jwt = get_valid_token()
    subreddit = post_data.get("subreddit")
    if not subreddit:
        raise RuntimeError("Missing subreddit info in Reddit post")

    community_name = SUB_MAP.get(subreddit.lower())
    if not community_name:
        raise RuntimeError(f"No mapped community for subreddit {subreddit}")

    comm_id = get_community_id(community_name, jwt)
    lemmy_id = create_lemmy_post(subreddit, post_data, jwt, comm_id)
    db.save_post(reddit_id, str(lemmy_id), subreddit)

    log(f"✅ Background mirror success: Reddit {reddit_id} → Lemmy {lemmy_id}")

    log(f"✅ Background mirror success: Reddit {reddit_id} → Lemmy {lemmy_id}")

    # ----------------------------------------------------
    # Enqueue background job for mirroring comments
    # ----------------------------------------------------
    try:
        import sqlite3
        from datetime import datetime
        from pathlib import Path
        import json

        db_path = Path(__file__).parent / "data" / "jobs.db"
        conn = sqlite3.connect(db_path)

        # Prevent duplicate comment jobs for the same Reddit post
        cur = conn.execute(
            "SELECT 1 FROM jobs WHERE type='mirror_comment' AND json_extract(payload, '$.reddit_id') = ?",
            (reddit_id,),
        )
        if cur.fetchone():
            log(f"⏭️ Comment mirror job already exists for Reddit {reddit_id}, skipping enqueue.")
        else:
            payload = {
                "reddit_id": reddit_id,
                "reddit_comment_id": f"auto_{reddit_id}",  # marker for job tracking
                "lemmy_post_id": lemmy_id,
            }
            conn.execute(
                "INSERT INTO jobs (type, payload, status, retries, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "mirror_comment",
                    json.dumps(payload),
                    "queued",
                    0,
                    datetime.utcnow().isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
            log(f"🪶 Enqueued comment mirror job for Reddit {reddit_id} → Lemmy {lemmy_id}")

        conn.close()

    except Exception as e:
        log(f"⚠️ Failed to enqueue comment mirror for {reddit_id}: {e}")

    return {"lemmy_id": lemmy_id}

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log("🔧 reddit → lemmy bridge starting…")

    import sqlite3
    conn = sqlite3.connect("data/jobs.db")  # ✅ open DB connection
    db = JobDB(conn)

    migrate_legacy_json_to_sqlite(DB())  # still migrate legacy post_map.json

    try:
        mirror_loop(db)
    except Exception as e:
        log(f"❌ Mirror loop failed: {e}")
