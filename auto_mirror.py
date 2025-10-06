#!/usr/bin/env python3
import os, time, json, requests, mimetypes
from datetime import datetime, timezone

# === CONFIG ===
LEMMY_INSTANCE = os.getenv("LEMMY_INSTANCE", "http://lemmy:8536").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")
SUB_MAP = os.getenv("SUB_MAP", "")
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))
MIRROR_COMMENTS = os.getenv("MIRROR_COMMENTS", "true").lower() == "true"
COMMENT_LIMIT_TOTAL = int(os.getenv("COMMENT_LIMIT_TOTAL", "500"))
COMMENT_SLEEP = float(os.getenv("COMMENT_SLEEP", "0.3"))
GALLERY_SLEEP = float(os.getenv("GALLERY_SLEEP", "1.0"))

DATA_DIR = "data"
TOKEN_CACHE = os.path.join(DATA_DIR, "token.json")
COMMUNITY_CACHE = os.path.join(DATA_DIR, "communities.json")
os.makedirs(DATA_DIR, exist_ok=True)

# === UTILITIES ===
def log(msg): print(msg, flush=True)
def load_json(path, default=None):
    if os.path.exists(path):
        try: return json.load(open(path))
        except: pass
    return default if default is not None else {}
def save_json(path, data): json.dump(data, open(path, "w"))

# === AUTH ===
def get_lemmy_token():
    cache = load_json(TOKEN_CACHE)
    if cache and "jwt" in cache and (time.time() - cache.get("timestamp", 0) < 3600):
        age = int(time.time() - cache["timestamp"])
        log(f"🔁 Using cached Lemmy token (age={age}s)")
        return cache["jwt"]
    log(f"🔑 Logging in to {LEMMY_INSTANCE}/api/v3/user/login as {LEMMY_USER}")
    r = requests.post(f"{LEMMY_INSTANCE}/api/v3/user/login",
                      json={"username_or_email": LEMMY_USER, "password": LEMMY_PASS}, timeout=15)
    if r.status_code != 200 or "jwt" not in r.json():
        raise SystemExit(f"❌ Lemmy login failed: {r.text}")
    token = r.json()["jwt"]
    save_json(TOKEN_CACHE, {"jwt": token, "timestamp": time.time()})
    log("✅ Logged into Lemmy (new token cached)")
    return token

def get_community_id(name, token):
    cache = load_json(COMMUNITY_CACHE, {})
    if name in cache:
        return cache[name]
    log(f"🔍 Resolving community '{name}'...")
    r = requests.get(f"{LEMMY_INSTANCE}/api/v3/community", params={"name": name}, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"Failed to fetch community '{name}': {r.text}")
    cid = r.json()["community_view"]["community"]["id"]
    cache[name] = cid
    save_json(COMMUNITY_CACHE, cache)
    log(f"✅ Found community '{name}' (ID={cid})")
    return cid

# === REDDIT FETCH ===
def fetch_reddit_posts(subreddit):
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=10"
    headers = {"User-Agent": "reddit-lemmy-bridge"}
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        log(f"⚠️ Reddit fetch failed for r/{subreddit}: {r.status_code}")
        return []
    return [p["data"] for p in r.json()["data"]["children"]]

# === PICTRS ===
def upload_to_pictrs(media_url, token):
    try:
        log(f"🖼️ Downloading {media_url}")
        r = requests.get(media_url, stream=True, timeout=20)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("Content-Type", "image/jpeg")
        ext = mimetypes.guess_extension(ctype.split(";")[0]) or ".jpg"
        files = {"images[]": ("upload" + ext, r.content, ctype)}
        res = requests.post(f"{LEMMY_INSTANCE}/pictrs/image",
                            files=files, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if res.status_code == 200 and "files" in res.json():
            filehash = res.json()["files"][0]["file"]
            url = f"{LEMMY_INSTANCE}/pictrs/image/{filehash}"
            log(f"✅ Uploaded to Pictrs: {url}")
            return url
    except Exception as e:
        log(f"⚠️ Pictrs upload failed: {e}")
    return None

# === COMMENTS ===
def mirror_comments_full(permalink, token, post_id, subreddit):
    """Recursively mirror all Reddit comments (nested) into Lemmy."""
    try:
        url = f"{permalink}.json"
        headers = {"User-Agent": "reddit-lemmy-bridge"}
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            log(f"⚠️ Failed to fetch comments for {permalink}")
            return
        comments = r.json()[1]["data"]["children"]
        posted_count = 0
        map_file = os.path.join(DATA_DIR, f"lemmy_map_{subreddit}.json")
        mapping = load_json(map_file, {})

        def recurse_tree(nodes, parent=None):
            nonlocal posted_count
            for node in nodes:
                if posted_count >= COMMENT_LIMIT_TOTAL:
                    return
                if node["kind"] != "t1":
                    continue
                d = node["data"]
                content = d.get("body", "")
                author = d.get("author", "[deleted]")
                payload = {
                    "content": f"{content}\n\n_Originally by u/{author}_",
                    "post_id": post_id,
                }
                if parent:
                    payload["parent_id"] = parent
                rc = requests.post(f"{LEMMY_INSTANCE}/api/v3/comment",
                                   json=payload,
                                   headers={"Authorization": f"Bearer {token}"},
                                   timeout=15)
                if rc.status_code == 200:
                    new_id = rc.json()["comment_view"]["comment"]["id"]
                    reddit_cid = d["id"]
                    mapping[reddit_cid] = {"lemmy_comment": new_id, "last_body": content}
                    posted_count += 1
                    log(f"💬 Comment {posted_count} by u/{author}")
                    time.sleep(COMMENT_SLEEP)
                    if "replies" in d and isinstance(d["replies"], dict):
                        recurse_tree(d["replies"]["data"]["children"], new_id)
                else:
                    log(f"⚠️ Comment post failed: {rc.status_code}")
                    time.sleep(COMMENT_SLEEP)

        recurse_tree(comments)
        save_json(map_file, mapping)
        log(f"✅ Mirrored {posted_count} comments.")
    except Exception as e:
        log(f"⚠️ Comment mirror error: {e}")

# === LEMMY POST ===
def post_to_lemmy(post, token, community_id, subreddit):
    """Post Reddit submissions to Lemmy with Pictrs and comment mirror."""
    title = post["title"][:255]
    permalink = f"https://reddit.com{post['permalink']}"
    body = post.get("selftext", "").strip()
    post_hint = post.get("post_hint", "")
    media_url = post.get("url_overridden_by_dest")
    is_gallery = post.get("is_gallery", False)
    pictrs_urls = []

    if is_gallery and "media_metadata" in post:
        for m in post["media_metadata"].values():
            if "s" in m and "u" in m["s"]:
                u = m["s"]["u"].replace("&amp;", "&")
                up = upload_to_pictrs(u, token)
                if up: pictrs_urls.append(up)
                time.sleep(GALLERY_SLEEP)
    elif post_hint in ("image", "hosted:video") and media_url:
        up = upload_to_pictrs(media_url, token)
        if up: pictrs_urls.append(up)

    payload = {"name": title, "community_id": community_id}
    if pictrs_urls:
        images_md = "\n".join([f"![image]({u})" for u in pictrs_urls])
        text = (body + "\n\n" + images_md) if body else images_md
        payload["body"] = f"{text}\n\n---\n[Original Reddit post]({permalink})"
        payload["thumbnail_url"] = pictrs_urls[0]
    elif body:
        payload["body"] = f"{body}\n\n---\n[Original Reddit post]({permalink})"
    elif media_url:
        payload["url"] = media_url
    else:
        payload["body"] = f"[Original Reddit post]({permalink})"

    r = requests.post(f"{LEMMY_INSTANCE}/api/v3/post",
                      json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if r.status_code != 200:
        log(f"⚠️ Lemmy post failed ({r.status_code}): {r.text}")
        return None
    pid = r.json()["post_view"]["post"]["id"]
    log(f"✅ Posted '{title}' (Lemmy ID={pid})")

    # Map Reddit → Lemmy post
    map_file = os.path.join(DATA_DIR, f"lemmy_map_{subreddit}.json")
    mapping = load_json(map_file, {})
    mapping[post["id"]] = {
        "lemmy_post": pid,
        "last_body": body,
        "permalink": permalink
    }
    save_json(map_file, mapping)

    if MIRROR_COMMENTS:
        mirror_comments_full(permalink, token, pid, subreddit)
    return pid

# === PER-SUB MIRROR ===
def mirror_subreddit(subreddit, community, token):
    cachefile = os.path.join(DATA_DIR, f"posted_{subreddit}.json")
    seen = set(load_json(cachefile, []))
    community_id = get_community_id(community, token)
    posts = fetch_reddit_posts(subreddit)
    for p in reversed(posts):
        pid = p["id"]
        if pid in seen:
            continue
        post_to_lemmy(p, token, community_id, subreddit)
        seen.add(pid)
        save_json(cachefile, list(seen)[-500:])
    log(f"✅ Done r/{subreddit} → c/{community}")

# === MAIN LOOP ===
def main():
    pairs = [x.split(":") for x in SUB_MAP.split(",") if ":" in x]
    if not pairs:
        raise SystemExit("❌ SUB_MAP not set correctly (expected 'sub:community,...').")
    while True:
        token = get_lemmy_token()
        for sub, comm in pairs:
            try:
                log(f"\n🔍 Checking r/{sub.strip()} → c/{comm.strip()} @ {datetime.now(timezone.utc)}")
                mirror_subreddit(sub.strip(), comm.strip(), token)
            except Exception as e:
                log(f"⚠️ Error mirroring {sub}: {e}")
        log(f"🕒 Sleeping {SLEEP_SECONDS}s...\n")
        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main()
