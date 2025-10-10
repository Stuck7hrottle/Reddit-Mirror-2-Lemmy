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
from pathlib import Path
from datetime import datetime, timedelta
import sys
import re
from html import unescape

from db_cache import DB  # ← NEW

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

# ─────────────────────────────────────────────
# SUBREDDIT → COMMUNITY MAP
# ─────────────────────────────────────────────
SUB_MAP = {}
raw_sub_map = os.getenv("SUB_MAP", "fosscad2:fosscad2,3d2a:3D2A,FOSSCADtoo:FOSSCADtoo")
for pair in raw_sub_map.split(","):
    if ":" in pair:
        k, v = pair.split(":", 1)
        SUB_MAP[k.strip()] = v.strip()

TOKEN_FILE = DATA_DIR / "token.json"
COMMUNITY_MAP_FILE = DATA_DIR / "community_map.json"
POST_MAP_FILE = DATA_DIR / "post_map.json"  # legacy JSON (read for migration only)

TOKEN_REUSE_HOURS = 23
COMMUNITY_REFRESH_HOURS = 6
SLEEP_BETWEEN_CYCLES = 900  # 15 min between full cycles

token_state = {}

def log(msg: str):
    print(f"{datetime.utcnow().isoformat()} | {msg}", flush=True)

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

def build_media_block_from_submission(sub) -> tuple[str, str | None]:
    if not isinstance(sub, dict):
        sub = getattr(sub, "__dict__", {})

    if not ENABLE_MEDIA_PREVIEW:
        base = to_md(getattr(sub, "selftext", "") or "")
        link_line = f"\n\n🔗 [View on Reddit](https://reddit.com{sub.permalink})"
        return (base + link_line if EMBED_PERMALINK_FOOTER else base, sub.url if getattr(sub, "url", None) else None)

    body_parts, media_lines = [], []
    st = to_md(getattr(sub, "selftext", "") or "")
    if st.strip():
        body_parts.append(st)

    if getattr(sub, "is_gallery", False) and hasattr(sub, "gallery_data") and hasattr(sub, "media_metadata"):
        try:
            items = sub.gallery_data.get("items", [])[:MAX_GALLERY_IMAGES]
            for idx, it in enumerate(items, 1):
                media_id = it.get("media_id")
                meta = sub.media_metadata.get(media_id, {})
                src = None
                if "s" in meta and isinstance(meta["s"], dict):
                    src = meta["s"].get("u") or meta["s"].get("gif") or meta["s"].get("mp4")
                if not src and "p" in meta and isinstance(meta["p"], list) and meta["p"]:
                    src = meta["p"][-1].get("u")
                caption = it.get("caption") or ""
                if src:
                    cap = f" — {md_escape(caption)}" if caption else ""
                    media_lines.append(f"![Image {idx}]({src}){cap}")
        except Exception:
            pass
    elif getattr(sub, "url", "") and (sub.domain in ("i.redd.it", "i.imgur.com") or is_image_url(sub.url) or guess_imgur_direct(sub.url)):
        img = sub.url
        if not is_image_url(img):
            guess = guess_imgur_direct(img)
            if guess:
                img = guess
        media_lines.append(f"![Image]({img})")
    elif getattr(sub, "domain", "") == "v.redd.it" and getattr(sub, "media", None):
        try:
            rv = sub.media.get("reddit_video") or {}
            mp4 = rv.get("fallback_url")
            if mp4:
                media_lines.append(f"[🎬 View video on Reddit]({sub.url})")
        except Exception:
            pass
    elif getattr(sub, "url", "") and not getattr(sub, "is_self", False):
        media_lines.append(f"[External link]({sub.url})")

    if media_lines:
        if st.strip():
            body_parts.append("\n---\n")
        body_parts += media_lines

    if EMBED_PERMALINK_FOOTER:
        permalink = sub.get("permalink") if isinstance(sub, dict) else getattr(sub, "permalink", None)
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

def get_community_id(name: str, jwt: str):
    mapping = load_json(COMMUNITY_MAP_FILE, {})
    if not mapping or time.time() - mapping.get("_fetched_at", 0) > COMMUNITY_REFRESH_HOURS * 3600:
        refresh_community_map(jwt)
        mapping = load_json(COMMUNITY_MAP_FILE, {})
    for k, v in mapping.items():
        if k.lower() == name.lower():
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

        payload = {"content": content, "post_id": post_id}
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

def mirror_once(db: DB):
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
            except Exception as e:
                log(f"⚠️ Error creating test post: {e}")
        else:
            log(f"🔄 Live mode: Fetching from Reddit API (limit={os.getenv('REDDIT_LIMIT', 10)})…")
            import praw
            reddit = praw.Reddit(
                client_id=os.getenv("REDDIT_CLIENT_ID"),
                client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
                user_agent="reddit-lemmy-bridge",
            )
            subreddit_obj = reddit.subreddit(reddit_sub)
            for submission in subreddit_obj.new(limit=int(os.getenv("REDDIT_LIMIT", 10))):
                if db.get_lemmy_post_id(submission.id):
                    log(f"⏭️ Skipping already mirrored post (cached): {submission.title}")
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
                        # Optional: mirror a few top-level comments (unchanged behavior)
                        try:
                            mirror_comments(reddit_sub, pid, submission.comments[:3], jwt)
                        except Exception as e:
                            log(f"⚠️ Comment mirroring error (non-fatal): {e}")

                        # Save to SQLite cache
                        db.save_post(submission.id, str(pid), reddit_sub)
                        log(f"💾 Cached mapping: Reddit {submission.id} → Lemmy {pid}")
                except Exception as e:
                    log(f"⚠️ Error creating post from Reddit: {e}")

    log(f"🕒 Sleeping {SLEEP_BETWEEN_CYCLES}s...")

# ─────────────────────────────────────────────
# UPDATE EXISTING POSTS (unchanged)
# ─────────────────────────────────────────────
def fetch_reddit_submission(submission_id):
    import requests
    user_agent = os.getenv("REDDIT_USER_AGENT", "reddit-lemmy-bridge-updater/1.0")
    base_url = f"https://www.reddit.com/comments/{submission_id}.json"
    headers = {"User-Agent": user_agent}
    try:
        r = requests.get(base_url, headers=headers, timeout=15)
        if not r.ok:
            log(f"⚠️ Reddit fetch failed for {submission_id}: {r.status_code}")
            return None
        data = r.json()
        if not data or not isinstance(data, list):
            log(f"⚠️ Unexpected Reddit JSON for {submission_id}")
            return None
        post = data[0]["data"]["children"][0]["data"]
        sub_data = {
            "id": post.get("id"),
            "title": post.get("title", ""),
            "url": post.get("url_overridden_by_dest") or post.get("url"),
            "selftext": post.get("selftext", ""),
            "subreddit": post.get("subreddit", ""),
            "permalink": f"https://www.reddit.com{post.get('permalink', '')}",
            "is_self": post.get("is_self", False),
            "domain": post.get("domain", ""),
            "thumbnail": post.get("thumbnail") if post.get("thumbnail", "").startswith("http") else "",
            "is_gallery": post.get("is_gallery", False),
            "media_metadata": post.get("media_metadata", {}),
        }
        if post.get("is_video") and "media" in post:
            video_info = post["media"].get("reddit_video", {})
            sub_data["video_url"] = video_info.get("fallback_url")
            sub_data["is_video"] = True
        return sub_data
    except Exception as e:
        log(f"⚠️ Failed fetching Reddit post {submission_id}: {e}")
        return None

def update_existing_posts():
    post_map_path = DATA_DIR / "post_map.json"
    if not post_map_path.exists():
        log("❌ No post_map.json found.")
        return
    try:
        post_map = json.loads(post_map_path.read_text())
    except Exception as e:
        log(f"❌ Failed to load post_map.json: {e}")
        return

    log(f"🗂️ Loaded {len(post_map)} mirrored posts from {post_map_path}")
    log(f"🔄 Updating {len(post_map)} existing Lemmy posts with new media embeds...")

    jwt = get_cached_jwt() or lemmy_login(force=True)
    headers = {"Authorization": f"Bearer {jwt}"}
    success = 0

    for reddit_id, item in post_map.items():
        try:
            if isinstance(item, str):
                post_id = None
                reddit_id = item
            elif isinstance(item, dict):
                post_id = item.get("lemmy_id")
                reddit_id = item.get("reddit_id") or reddit_id
            else:
                log(f"⚠️ Unexpected entry type: {type(item)}")
                continue

            if not reddit_id or not post_id:
                log(f"⚠️ Skipping invalid entry: {item}")
                continue

            sub_data = fetch_reddit_submission(reddit_id)
            if not sub_data:
                log(f"⚠️ Unable to fetch Reddit data for {reddit_id}")
                continue

            for key in ["is_gallery", "media_metadata", "thumbnail"]:
                sub_data.setdefault(key, None)

            new_body = build_post_body(sub_data)

            update_url = f"{LEMMY_URL}/api/v3/post"
            payload = {"post_id": post_id, "body": new_body}
            r = requests.put(update_url, json=payload, headers=headers, timeout=20)

            if r.status_code == 404:
                log(f"⚠️ Lemmy 404 updating post {reddit_id} (ID={post_id}) — check if deleted/unlisted.")
            elif not r.ok:
                log(f"⚠️ Failed updating {reddit_id} (Lemmy ID={post_id}): {r.status_code} {r.text[:120]}")
            else:
                success += 1
                log(f"✅ Updated '{sub_data.get('title', 'Untitled')}' (Lemmy ID={post_id})")

            time.sleep(1.5)
        except Exception as e:
            log(f"⚠️ Exception updating {reddit_id}: {e}")
            continue

    log(f"✨ Done — updated {success}/{len(post_map)} posts.")

# ─────────────────────────────────────────────
# UPDATE TRIGGER
# ─────────────────────────────────────────────
if len(sys.argv) > 1 and sys.argv[1] == "--update-existing":
    update_existing_posts()
    sys.exit(0)

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log("🔧 reddit → lemmy bridge starting…")
    db = DB()  # init DB and connection
    migrate_legacy_json_to_sqlite(db)
    while True:
        try:
            mirror_once(db)
        except Exception as e:
            log(f"❌ Mirror cycle failed: {e}")
        time.sleep(SLEEP_BETWEEN_CYCLES)
