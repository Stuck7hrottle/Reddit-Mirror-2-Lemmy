
import os
import time
import json
import re
import praw
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone

# =========================================
# CONFIG / ENV
# =========================================
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "reddit-lemmy-bridge/1.0")

LEMMY_URL = os.getenv("LEMMY_URL", "https://your-lemmy.example.com").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER", "")
LEMMY_PASS = os.getenv("LEMMY_PASS", "")

# Comma-separated mapping: sub:community,sub2:community2
# Example: "fosscad2:fosscad2,FOSSCADtoo:FOSSCADtoo,3d2a:3D2A"
SUB_MAP = os.getenv("SUB_MAP", "example:example")
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))
REDDIT_LIMIT = int(os.getenv("REDDIT_LIMIT", "10"))
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "5"))
POST_RETRY_DELAY = int(os.getenv("POST_RETRY_DELAY", "30"))

MIRROR_COMMENTS = os.getenv("MIRROR_COMMENTS", "true").lower() == "true"
COMMENT_LIMIT = int(os.getenv("COMMENT_LIMIT", "3"))  # top-level limit per post
COMMENT_LIMIT_TOTAL = int(os.getenv("COMMENT_LIMIT_TOTAL", "500"))
COMMENT_SLEEP = float(os.getenv("COMMENT_SLEEP", "0.3"))
EDIT_CHECK_LIMIT = int(os.getenv("EDIT_CHECK_LIMIT", "50"))
EDIT_SLEEP = float(os.getenv("EDIT_SLEEP", "0.5"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
POSTS_FILE = DATA_DIR / "posts.json"            # reddit_id -> lemmy_post_id
COMMENTS_FILE = DATA_DIR / "comments.json"      # reddit_comment_id -> lemmy_comment_id
TOKEN_FILE = DATA_DIR / "token.json"            # {"jwt": "...", "ts": epoch, "last_login": epoch}
COMMUNITY_MAP_FILE = DATA_DIR / "community_map.json"  # {"_meta": {"refreshed_at": epoch}, "fosscad2": 3, "FOSSCADtoo":2, ...}

COMMUNITY_CACHE_TTL = int(os.getenv("COMMUNITY_CACHE_TTL_SECONDS", str(6 * 3600)))  # 6 hours
LOGIN_COOLDOWN = int(os.getenv("LOGIN_COOLDOWN_SECONDS", "600"))  # 10 minutes

INCLUDE_REDDIT_LINKS = os.getenv("INCLUDE_REDDIT_LINKS", "true").lower() == "true"

# =========================================
# HELPERS
# =========================================
def now_utc():
    return datetime.now(timezone.utc)

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        print(f"⚠️  Corrupted JSON at {path}, resetting...", flush=True)
        return default

def save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)

def parse_sub_map(raw: str):
    mapping = {}
    for pair in [p.strip() for p in raw.split(",") if p.strip()]:
        if ":" in pair:
            sub, comm = pair.split(":", 1)
            mapping[sub.strip()] = comm.strip()
    return mapping

SUB_TO_COMM = parse_sub_map(SUB_MAP)

# =========================================
# STATE
# =========================================
post_map = load_json(POSTS_FILE, {})
comment_map = load_json(COMMENTS_FILE, {})
token_state = load_json(TOKEN_FILE, {})

# =========================================
# REDDIT
# =========================================
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    username=REDDIT_USERNAME,
    password=REDDIT_PASSWORD,
    user_agent=REDDIT_USER_AGENT,
)

# =========================================
# LEMMY AUTH (COOLDOWN + CACHE)
# =========================================
def can_login():
    last = token_state.get("last_login", 0)
    since = time.time() - last
    return since >= LOGIN_COOLDOWN

def lemmy_login(force=False):
    global token_state
    # Use cached JWT if present & < 23h old
    if not force and "jwt" in token_state and (time.time() - token_state.get("ts", 0)) < 23*3600:
        print(f"🔁 Using cached Lemmy token (age={int(time.time()-token_state.get('ts',0))}s)", flush=True)
        return token_state["jwt"]

    if not can_login() and not force:
        wait = LOGIN_COOLDOWN - int(time.time() - token_state.get("last_login", 0))
        if wait > 0:
            print(f"🕒 Login cooldown active, waiting {wait}s before next Lemmy login…", flush=True)
            time.sleep(wait)

    print(f"🔑 Logging in to {LEMMY_URL}/api/v3/user/login as {LEMMY_USER}", flush=True)
    try:
        r = requests.post(f"{LEMMY_URL}/api/v3/user/login",
                          json={"username_or_email": LEMMY_USER, "password": LEMMY_PASS},
                          timeout=20)
    except requests.RequestException as e:
        print(f"⚠️  Network error on login: {e}", flush=True)
        time.sleep(LOGIN_COOLDOWN)
        raise

    if r.status_code == 400 and "duplicate key value" in r.text:
        # Lemmy duplicate-token bug: back off hard then retry once
        print("⚠️  Lemmy duplicate-token bug hit. Sleeping 45s then retrying…", flush=True)
        time.sleep(45)
        return lemmy_login(force=True)

    if not r.ok:
        raise RuntimeError(f"Lemmy login failed: {r.status_code} {r.text}")

    jwt = r.json()["jwt"]
    token_state = {"jwt": jwt, "ts": time.time(), "last_login": time.time()}
    save_json(TOKEN_FILE, token_state)
    print("✅ Logged into Lemmy (token cached)", flush=True)
    return jwt

def lemmy_headers(jwt: str = ""):
    if not jwt:
        jwt = token_state.get("jwt", "")
    return {"Authorization": f"Bearer {jwt}"} if jwt else {}

# =========================================
# COMMUNITY MAP (AUTO-REFRESH EVERY 6H)
# =========================================
def _community_cache_fresh(meta: dict):
    refreshed_at = meta.get("refreshed_at", 0)
    return (time.time() - refreshed_at) < COMMUNITY_CACHE_TTL

def refresh_community_map(jwt: str):
    print("🌐 Refreshing community map…", flush=True)
    url = f"{LEMMY_URL}/api/v3/community/list"
    r = requests.get(url, headers=lemmy_headers(jwt), timeout=20)
    if not r.ok:
        raise RuntimeError(f"community list failed: {r.status_code} {r.text[:160]}")
    data = r.json()
    mapping = {
        c["community"]["name"].lower(): c["community"]["id"]
        for c in data.get("communities", [])
    }
    payload = {"_meta": {"refreshed_at": time.time(), "source": url}, **mapping}
    save_json(COMMUNITY_MAP_FILE, payload)
    print(f"✅ Cached {len(mapping)} communities to {COMMUNITY_MAP_FILE}", flush=True)
    return mapping

def ensure_community_map(jwt: str):
    cache = load_json(COMMUNITY_MAP_FILE, {})
    meta = cache.get("_meta", {})
    if cache and meta and _community_cache_fresh(meta):
        return cache
    # refresh if empty or stale
    mapping = refresh_community_map(jwt)
    return {"_meta": {"refreshed_at": time.time()}, **mapping}

def get_community_id(comm_name: str, jwt: str):
    cache = ensure_community_map(jwt)
    # Try lower-case match
    cid = cache.get(comm_name.lower())
    if cid:
        return cid
    # Not found — force refresh once
    mapping = refresh_community_map(jwt)
    cid = mapping.get(comm_name.lower())
    if cid:
        return cid
    # Give a clear error
    raise RuntimeError(f"community lookup error: could not resolve '{comm_name}' (case-insensitive)")

# =========================================
# LEMMY POST / COMMENT HELPERS
# =========================================
def lemmy_post(payload: dict, retries: int = 3, retry_sleep: int = 10):
    """POST helper with token refresh & basic rate-limit handling."""
    url = f"{LEMMY_URL}/api/v3/post"
    jwt = token_state.get("jwt") or lemmy_login()
    payload = {**payload, "auth": jwt}

    for attempt in range(retries):
        r = requests.post(url, json=payload, timeout=30)
        if r.ok:
            return r.json()
        txt = r.text.lower()
        if r.status_code in (401, 403) or "incorrect_login" in txt:
            print("⚠️  Token invalid, refreshing…", flush=True)
            jwt = lemmy_login(force=True)
            payload["auth"] = jwt
            continue
        if "rate_limit_error" in txt:
            wait = retry_sleep * (2 ** attempt)
            print(f"⚠️  Lemmy rate limit — waiting {wait}s…", flush=True)
            time.sleep(wait)
            continue
        if "duplicate key value" in txt:
            print("⚠️  duplicate-token mid-post; re-login and retry…", flush=True)
            jwt = lemmy_login(force=True)
            payload["auth"] = jwt
            continue
        print(f"⚠️  Lemmy post error: {r.status_code} {r.text[:160]}", flush=True)
        time.sleep(retry_sleep)
    return None

def lemmy_comment(payload: dict, retries: int = 3, retry_sleep: int = 10):
    url = f"{LEMMY_URL}/api/v3/comment"
    jwt = token_state.get("jwt") or lemmy_login()
    payload = {**payload, "auth": jwt}

    for attempt in range(retries):
        r = requests.post(url, json=payload, timeout=30)
        if r.ok:
            return r.json()
        txt = r.text.lower()
        if r.status_code in (401, 403) or "incorrect_login" in txt:
            print("⚠️  Token invalid while commenting, refreshing…", flush=True)
            jwt = lemmy_login(force=True)
            payload["auth"] = jwt
            continue
        if "rate_limit_error" in txt:
            wait = retry_sleep * (2 ** attempt)
            print(f"⚠️  Comment rate-limit — waiting {wait}s…", flush=True)
            time.sleep(wait)
            continue
        print(f"⚠️  Lemmy comment error: {r.status_code} {r.text[:160]}", flush=True)
        time.sleep(retry_sleep)
    return None

# =========================================
# CONTENT BUILDERS
# =========================================
def reddit_permalink(submission):
    return f"https://www.reddit.com{submission.permalink}"

def build_post_body(submission):
    parts = []
    if submission.is_self and submission.selftext:
        parts.append(submission.selftext)
    # Media / gallery hints
    if getattr(submission, "url", None) and not submission.is_self:
        parts.append(f"Source: {submission.url}")
    # Include Reddit permalink for context
    if INCLUDE_REDDIT_LINKS:
        parts.append(f"\n—\nMirrored from Reddit: {reddit_permalink(submission)}")
    body = "\n\n".join([p for p in parts if p])
    return body if body.strip() else None

# =========================================
# MIRROR LOGIC
# =========================================
def mirror_post(submission, community_id: int):
    title = submission.title[:300]
    body = build_post_body(submission)
    url = submission.url if (getattr(submission, "url", None) and not submission.is_self) else None

    payload = {
        "name": title,
        "community_id": community_id,
    }
    if body:
        payload["body"] = body
    if url and not body:
        # Lemmy accepts either url or body (or both since 0.19, but some setups prefer one)
        payload["url"] = url

    res = lemmy_post(payload)
    if not res:
        print("❌ Failed to create Lemmy post", flush=True)
        return None

    pid = res.get("post_view", {}).get("post", {}).get("id")
    if not pid:
        print(f"⚠️  Unexpected Lemmy response: {res}", flush=True)
        return None
    return pid

def mirror_comments(submission, lemmy_post_id: int):
    if not MIRROR_COMMENTS:
        return 0
    mirrored = 0
    submission.comments.replace_more(limit=0)
    flat = submission.comments.list()
    # Hard cap to avoid runaway
    flat = flat[:COMMENT_LIMIT_TOTAL]
    for c in flat:
        # skip removed/deleted
        if not getattr(c, "body", None) or c.body.strip() in ("[deleted]", "[removed]"):
            continue
        rid = c.id
        if rid in comment_map:
            continue
        author = f"u/{c.author}" if c.author else "[deleted]"
        text = f"{author} said:\n\n{c.body}"
        parent_lemmy_id = None
        if c.parent_id.startswith("t1_"):
            parent_rid = c.parent_id.split("_", 1)[1]
            parent_lemmy_id = comment_map.get(parent_rid)

        payload = {
            "content": text[:10000],
            "post_id": lemmy_post_id,
        }
        if parent_lemmy_id:
            payload["parent_id"] = parent_lemmy_id

        res = lemmy_comment(payload)
        if res and "comment_view" in res:
            lemmy_cid = res["comment_view"]["comment"]["id"]
            comment_map[rid] = lemmy_cid
            mirrored += 1
            if mirrored % 10 == 0:
                save_json(COMMENTS_FILE, comment_map)
        else:
            print(f"⚠️  Comment post failed for {rid}", flush=True)
        time.sleep(COMMENT_SLEEP)
    # persist at end
    save_json(COMMENTS_FILE, comment_map)
    return mirrored

# =========================================
# MAIN LOOP
# =========================================
def run_once():
    jwt = token_state.get("jwt") or lemmy_login()
    # refresh community cache if stale
    ensure_community_map(jwt)

    mirrored_count = 0
    for sub, community in SUB_TO_COMM.items():
        print(f"🔍 Checking r/{sub} → c/{community} @ {now_utc().strftime('%Y-%m-%d %H:%M:%S %z')}", flush=True)
        # Resolve community id
        try:
            cid = get_community_id(community, jwt)
        except Exception as e:
            print(f"❌ Skipping {sub}: {e}", flush=True)
            continue

        # Fetch & mirror
        try:
            for submission in reddit.subreddit(sub).new(limit=REDDIT_LIMIT):
                if submission.id in post_map:
                    # Already mirrored; optionally sync comments
                    mirror_comments(submission, post_map[submission.id])
                    continue

                if mirrored_count >= MAX_POSTS_PER_RUN:
                    break

                pid = mirror_post(submission, cid)
                if pid:
                    post_map[submission.id] = pid
                    save_json(POSTS_FILE, post_map)
                    c = mirror_comments(submission, pid)
                    print(f"✅ Posted '{submission.title[:60]}' (Lemmy ID={pid}) + {c} comments", flush=True)
                    mirrored_count += 1
                else:
                    print(f"⚠️  Skipped '{submission.title[:60]}' (post failed)", flush=True)
                    time.sleep(POST_RETRY_DELAY)
        except Exception as e:
            print(f"❌ Error processing r/{sub}: {e}", flush=True)

    # Save state
    save_json(POSTS_FILE, post_map)
    save_json(COMMENTS_FILE, comment_map)

if __name__ == "__main__":
    print("🔧 reddit → lemmy bridge starting…", flush=True)
    while True:
        run_once()
        print(f"🕒 Sleeping {SLEEP_SECONDS}s...", flush=True)
        time.sleep(SLEEP_SECONDS)
