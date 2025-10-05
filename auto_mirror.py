import os
import time
import json
import praw
import requests
from pathlib import Path

# ===========================
# CONFIGURATION
# ===========================
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "reddit2lemmy-auto/0.7")

LEMMY_URL = os.getenv("LEMMY_URL", "https://lemmy.world")
LEMMY_USERNAME = os.getenv("LEMMY_USERNAME")
LEMMY_PASSWORD = os.getenv("LEMMY_PASSWORD")

SUBREDDITS = [s.strip() for s in os.getenv("SUBREDDITS", "technology").split(",")]
LEMMY_COMMUNITY = os.getenv("LEMMY_COMMUNITY", "technology")
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "600"))  # default 10min
RATE_LIMIT_SLEEP = int(os.getenv("RATE_LIMIT_SLEEP", "60"))  # base cooldown (seconds)

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(exist_ok=True)
POSTS_FILE = DATA_DIR / "posts.json"
COMMENTS_FILE = DATA_DIR / "comments.json"
TOKEN_FILE = DATA_DIR / "token.json"

# ===========================
# REDDIT AUTHENTICATION
# ===========================
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    username=REDDIT_USERNAME,
    password=REDDIT_PASSWORD,
    user_agent=REDDIT_USER_AGENT,
)

# ===========================
# UTILITIES
# ===========================
def load_json(path):
    if path.exists():
        try:
            with open(path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

post_map = load_json(POSTS_FILE)
comment_cache = load_json(COMMENTS_FILE)

def persist_state():
    save_json(POSTS_FILE, post_map)
    save_json(COMMENTS_FILE, comment_cache)

# ===========================
# LEMMY LOGIN
# ===========================
def lemmy_login(force=False):
    """Login to Lemmy and cache token."""
    if not force and TOKEN_FILE.exists():
        try:
            data = json.load(open(TOKEN_FILE))
            if "jwt" in data and time.time() - data.get("timestamp", 0) < 23 * 3600:
                print("🔁 Using cached Lemmy token", flush=True)
                return data["jwt"]
        except Exception:
            pass

    while True:
        r = requests.post(f"{LEMMY_URL}/api/v3/user/login", json={
            "username_or_email": LEMMY_USERNAME,
            "password": LEMMY_PASSWORD
        })
        if r.ok:
            token = r.json()["jwt"]
            json.dump({"jwt": token, "timestamp": time.time()}, open(TOKEN_FILE, "w"))
            print("✅ Logged into Lemmy (new token cached)", flush=True)
            return token
        elif "rate_limit_error" in r.text:
            print("⚠️ Lemmy rate limit hit, waiting 60s before retry...", flush=True)
            time.sleep(60)
        else:
            print(f"❌ Lemmy login failed: {r.text}", flush=True)
            time.sleep(30)

jwt = lemmy_login()

# ===========================
# LEMMY POSTING HELPERS
# ===========================
def exponential_backoff(base, attempt):
    """Return sleep time increasing exponentially up to 10x base."""
    return min(base * (2 ** attempt), base * 10)

def post_to_lemmy(submission, attempt=0):
    """Post a Reddit submission to Lemmy, with token refresh and backoff."""
    global jwt
    body = submission.selftext.strip() if submission.selftext else submission.url
    payload = {
        "name": submission.title[:200],
        "body": body,
        "community_name": LEMMY_COMMUNITY,
        "auth": jwt,
    }
    r = requests.post(f"{LEMMY_URL}/api/v3/post", json=payload)
    if r.ok:
        post_id = r.json()["post_view"]["post"]["id"]
        print(f"✅ Posted submission: {submission.title}", flush=True)
        return post_id
    elif "incorrect_login" in r.text:
        print("⚠️ Lemmy token expired, refreshing...", flush=True)
        jwt = lemmy_login(force=True)
        return post_to_lemmy(submission, attempt)
    elif "rate_limit_error" in r.text:
        wait = exponential_backoff(RATE_LIMIT_SLEEP, attempt)
        print(f"⚠️ Rate limit hit (post). Waiting {wait}s...", flush=True)
        time.sleep(wait)
        return post_to_lemmy(submission, attempt + 1)
    else:
        print(f"⚠️ Failed to post submission: {r.text}", flush=True)
        return None

def post_comment_to_lemmy(post_id, body, parent_id=None, attempt=0):
    """Post a comment to Lemmy with threading and rate-limit backoff."""
    global jwt
    payload = {"content": body[:5000], "post_id": post_id, "auth": jwt}
    if parent_id:
        payload["parent_id"] = parent_id
    r = requests.post(f"{LEMMY_URL}/api/v3/comment", json=payload)
    if r.ok:
        return r.json()["comment_view"]["comment"]["id"]
    elif "incorrect_login" in r.text:
        print("⚠️ Lemmy token expired (comment), refreshing...", flush=True)
        jwt = lemmy_login(force=True)
        return post_comment_to_lemmy(post_id, body, parent_id, attempt)
    elif "rate_limit_error" in r.text:
        wait = exponential_backoff(RATE_LIMIT_SLEEP, attempt)
        print(f"⚠️ Rate limit hit (comment). Waiting {wait}s...", flush=True)
        time.sleep(wait)
        return post_comment_to_lemmy(post_id, body, parent_id, attempt + 1)
    else:
        print(f"⚠️ Failed to post comment: {r.text}", flush=True)
        return None

# ===========================
# COMMENT SYNC (THREADED)
# ===========================
def sync_comments(submission, lemmy_post_id):
    """Mirror Reddit comments to Lemmy with nesting."""
    submission.comments.replace_more(limit=0)
    comment_map_local = {}

    for c in submission.comments.list():
        if c.id in comment_cache:
            continue
        if not c.body or c.body.strip() == "[deleted]":
            continue

        parent_lemmy_id = None
        if c.parent_id.startswith("t1_"):
            parent_reddit_id = c.parent_id.split("_", 1)[1]
            parent_lemmy_id = comment_cache.get(parent_reddit_id) or comment_map_local.get(parent_reddit_id)

        author = f"u/{c.author}" if c.author else "[deleted]"
        body = f"{author} said:\n\n{c.body}"
        lemmy_comment_id = post_comment_to_lemmy(lemmy_post_id, body, parent_lemmy_id)
        if lemmy_comment_id:
            comment_cache[c.id] = lemmy_comment_id
            comment_map_local[c.id] = lemmy_comment_id
            print(f"💬 Mirrored comment from {author}", flush=True)
            persist_state()
        time.sleep(1)

# ===========================
# MAIN LOOP
# ===========================
while True:
    try:
        for sub in SUBREDDITS:
            subreddit = reddit.subreddit(sub)
            print(f"🔍 Checking new posts in r/{sub} ...", flush=True)
            for submission in subreddit.new(limit=5):
                if submission.id not in post_map:
                    lemmy_post_id = post_to_lemmy(submission)
                    if lemmy_post_id:
                        post_map[submission.id] = lemmy_post_id
                        persist_state()
                        sync_comments(submission, lemmy_post_id)
                else:
                    sync_comments(submission, post_map[submission.id])

        persist_state()
        print(f"🕒 Sleeping {SLEEP_SECONDS}s...", flush=True)
        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        print(f"❌ Error: {e}", flush=True)
        print("🔁 Reauthenticating Lemmy...", flush=True)
        jwt = lemmy_login(force=True)
        time.sleep(30)
