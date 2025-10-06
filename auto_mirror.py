#!/usr/bin/env python3
import os
import time
import json
import requests
from datetime import datetime

# ==============================
# 🔧 Configuration
# ==============================
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "5"))
POST_RETRY_DELAY = int(os.getenv("POST_RETRY_DELAY", "30"))
TOKEN_CACHE = "data/token.json"
COOLDOWN_FILE = "data/last_login.txt"
COOLDOWN_SECONDS = 600         # 10 min cooldown
TOKEN_REUSE_HOURS = 6          # reuse token 6 h
SLEEP_SECONDS = 900            # sleep 15 min between cycles

# ==============================
# 🔑 Lemmy Login
# ==============================
def lemmy_login():
    """Log in to Lemmy, caching JWT and respecting cooldown."""
    os.makedirs("data", exist_ok=True)

    # Reuse token if young enough
    if os.path.exists(TOKEN_CACHE):
        age = time.time() - os.path.getmtime(TOKEN_CACHE)
        if age < TOKEN_REUSE_HOURS * 3600:
            try:
                with open(TOKEN_CACHE) as f:
                    data = json.load(f)
                if "jwt" in data:
                    print(f"🔁 Using recent Lemmy token (age={int(age)} s)")
                    return data["jwt"]
            except Exception:
                pass

    # Enforce login cooldown
    if os.path.exists(COOLDOWN_FILE):
        since = time.time() - os.path.getmtime(COOLDOWN_FILE)
        if since < COOLDOWN_SECONDS:
            wait_left = int(COOLDOWN_SECONDS - since)
            print(f"🕒 Cooldown active, waiting {wait_left}s before next login…")
            time.sleep(wait_left)

    # Perform login
    creds = {
        "username_or_email": os.getenv("LEMMY_USER"),
        "password": os.getenv("LEMMY_PASSWORD"),
    }
    url = os.getenv("LEMMY_URL").rstrip("/") + "/api/v3/user/login"
    print(f"🔑 Logging in to {url} as {creds['username_or_email']}")
    r = requests.post(url, json=creds)
    if r.status_code != 200:
        print(f"❌ Login failed ({r.status_code}): {r.text}")
        raise SystemExit(1)

    data = r.json()
    if "jwt" not in data:
        print(f"⚠️ Unexpected login response: {data}")
        raise SystemExit(1)

    with open(TOKEN_CACHE, "w") as f:
        json.dump(data, f)
    open(COOLDOWN_FILE, "w").close()
    print("✅ Logged into Lemmy (new token cached)")
    return data["jwt"]

# ==============================
# 📤 Post to Lemmy
# ==============================
def post_to_lemmy(post):
    """Send one Reddit post dict to Lemmy."""
    jwt = lemmy_login()
    payload = {
        "name": post["title"],
        "url": post["url"],
        "community_id": post["community_id"],
        "body": post.get("selftext", "")
    }
    headers = {"Authorization": f"Bearer {jwt}"}
    url = os.getenv("LEMMY_URL").rstrip("/") + "/api/v3/post"

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code == 200:
            print(f"✅ Posted submission: {post['title']}")
            return True
        else:
            print(f"⚠️ Post failed ({r.status_code}): {r.text}")
            return False
    except requests.RequestException as e:
        print(f"⚠️ Network error during post: {e}")
        return False

# ==============================
# 🔁 Main Loop (mock example)
# ==============================
def fetch_new_reddit_posts():
    """Placeholder for your Reddit fetching logic."""
    # Replace with your actual Reddit mirror fetcher
    return [
        {"title": f"Example Post {i+1}",
         "url": f"https://reddit.com/example{i+1}",
         "community_id": int(os.getenv("LEMMY_COMMUNITY_ID", "2")),
         "selftext": ""}
        for i in range(3)
    ]

def main():
    while True:
        print(f"\n🔍 Checking new posts in {os.getenv('REDDIT_SOURCE', 'r/example')} at {datetime.utcnow()} UTC…")
        new_posts = fetch_new_reddit_posts()
        count = 0

        for post in new_posts:
            if count >= MAX_POSTS_PER_RUN:
                print(f"🛑 Reached MAX_POSTS_PER_RUN={MAX_POSTS_PER_RUN}, stopping early this cycle.")
                break
            success = post_to_lemmy(post)
            if success:
                count += 1
            else:
                print(f"⚠️ Waiting {POST_RETRY_DELAY}s before next attempt…")
                time.sleep(POST_RETRY_DELAY)

        print(f"🕒 Sleeping {SLEEP_SECONDS}s before next cycle…")
        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main()
