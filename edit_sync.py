#!/usr/bin/env python3
import os, time, json, requests
from datetime import datetime, timezone

LEMMY_INSTANCE = os.getenv("LEMMY_INSTANCE", "http://lemmy:8536").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")
SUB_MAP = os.getenv("SUB_MAP", "")
MIRROR_EDITS = os.getenv("MIRROR_EDITS", "true").lower() == "true"
EDIT_CHECK_LIMIT = int(os.getenv("EDIT_CHECK_LIMIT", "50"))
EDIT_SLEEP = float(os.getenv("EDIT_SLEEP", "0.5"))

DATA_DIR = "data"
TOKEN_CACHE = os.path.join(DATA_DIR, "token.json")
os.makedirs(DATA_DIR, exist_ok=True)

def log(msg): print(msg, flush=True)
def load_json(path, default=None):
    if os.path.exists(path):
        try: return json.load(open(path))
        except: pass
    return default if default is not None else {}
def save_json(path, data): json.dump(data, open(path, "w"))

def get_lemmy_token():
    cache = load_json(TOKEN_CACHE)
    if cache and "jwt" in cache and (time.time() - cache.get("timestamp", 0) < 3600):
        return cache["jwt"]
    log(f"🔑 Logging in to {LEMMY_INSTANCE}/api/v3/user/login as {LEMMY_USER}")
    r = requests.post(f"{LEMMY_INSTANCE}/api/v3/user/login",
                      json={"username_or_email": LEMMY_USER, "password": LEMMY_PASS}, timeout=10)
    if r.status_code != 200 or "jwt" not in r.json():
        raise SystemExit(f"❌ Lemmy login failed: {r.text}")
    jwt = r.json()["jwt"]
    save_json(TOKEN_CACHE, {"jwt": jwt, "timestamp": time.time()})
    log("✅ Logged into Lemmy (new token cached)")
    return jwt

REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "reddit-lemmy-bridge")
_reddit_token = {"value": None, "expires": 0.0}

def get_reddit_token():
    """App-only OAuth token, cached until shortly before it expires.
    Unauthenticated www.reddit.com requests are blocked from most VPS IPs."""
    if _reddit_token["value"] and time.time() < _reddit_token["expires"]:
        return _reddit_token["value"]
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    if not (client_id and client_secret):
        log("⚠️ REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set — cannot use Reddit OAuth")
        return None
    r = requests.post("https://www.reddit.com/api/v1/access_token",
                      auth=requests.auth.HTTPBasicAuth(client_id, client_secret),
                      data={"grant_type": "client_credentials"},
                      headers={"User-Agent": REDDIT_USER_AGENT}, timeout=15)
    if not r.ok:
        log(f"⚠️ Reddit token request failed ({r.status_code})")
        return None
    body = r.json()
    _reddit_token["value"] = body.get("access_token")
    _reddit_token["expires"] = time.time() + int(body.get("expires_in", 3600)) - 60
    return _reddit_token["value"]

def fetch_reddit_post(subreddit, post_id):
    token = get_reddit_token()
    if not token:
        return None
    url = f"https://oauth.reddit.com/r/{subreddit}/comments/{post_id}.json"
    headers = {"User-Agent": REDDIT_USER_AGENT, "Authorization": f"bearer {token}"}
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code == 401:  # token expired early — refresh once
        _reddit_token["value"] = None
        token = get_reddit_token()
        if not token:
            return None
        headers["Authorization"] = f"bearer {token}"
        r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        log(f"⚠️ Reddit fetch for {post_id} failed ({r.status_code})")
        return None
    data = r.json()[0]["data"]["children"][0]["data"]
    return data

def update_lemmy_post(post_id, body, title, token): # Added title param
    r = requests.put(f"{LEMMY_INSTANCE}/api/v3/post",
                     headers={"Authorization": f"Bearer {token}"},
                     json={
                         "post_id": post_id, 
                         "body": body,
                         "name": title # This fixes the 'missing field name' error
                     }, timeout=10)
    if r.status_code == 200:
        log(f"✅ Updated Lemmy post {post_id}")
    else:
        log(f"⚠️ Lemmy post update failed ({r.status_code}): {r.text}")

def update_lemmy_comment(comment_id, body, token):
    r = requests.put(f"{LEMMY_INSTANCE}/api/v3/comment/update",
                     headers={"Authorization": f"Bearer {token}"},
                     json={"comment_id": comment_id, "content": body}, timeout=10)
    if r.status_code == 200:
        log(f"✅ Updated Lemmy comment {comment_id}")
    else:
        log(f"⚠️ Lemmy comment update failed ({r.status_code}): {r.text}")

def sync_subreddit(subreddit, token):
    map_file = os.path.join(DATA_DIR, f"lemmy_map_{subreddit}.json")
    mapping = load_json(map_file, {})
    if not mapping:
        log(f"ℹ️ No map for r/{subreddit}, skipping.")
        return

    log(f"🪶 Checking edits for r/{subreddit}...")
    checked = 0
    for rid, entry in list(mapping.items())[-EDIT_CHECK_LIMIT:]:
        if checked >= EDIT_CHECK_LIMIT:
            break
        lemmy_post_id = entry.get("lemmy_post")
        last_text = entry.get("last_body", "")
        data = fetch_reddit_post(subreddit, rid)
        if not data:
            continue
        new_text = data.get("selftext", "")
        if data.get("edited") or new_text != last_text:
            log(f"✏️ Post u/{data.get('author')} edited — updating Lemmy post {lemmy_post_id}")
            new_body = f"{new_text}\n\n---\n[Original Reddit post](https://reddit.com{data.get('permalink')})"
            post_title = data.get("title", "Updated Post")
            update_lemmy_post(lemmy_post_id, new_body, post_title, token)
            entry["last_body"] = new_text
        mapping[rid] = entry
        checked += 1
        time.sleep(EDIT_SLEEP)

    save_json(map_file, mapping)
    log(f"✅ Checked {checked} posts for r/{subreddit}")

def main():
    if not MIRROR_EDITS:
        log("ℹ️ Edit mirroring disabled.")
        return
    token = get_lemmy_token()
    pairs = [x.split(":") for x in SUB_MAP.split(",") if ":" in x]
    for sub, comm in pairs:
        sync_subreddit(sub.strip(), token)
    log(f"🕒 Finished edit sync at {datetime.now(timezone.utc)}")

if __name__ == "__main__":
    main()
