#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import traceback
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import requests
import praw
from dotenv import load_dotenv

# --------------------------
# Load config
# --------------------------
load_dotenv()

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "reddit-lemmy-bridge/1.0")

LEMMY_URL = (os.getenv("LEMMY_URL") or "http://lemmy:8536").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")

# Mapping of subreddit -> lemmy community name (comma-separated pairs "sub:community")
SUB_MAP = os.getenv("SUB_MAP", "example:example")
REDDIT_LIMIT = int(os.getenv("REDDIT_LIMIT", "10"))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "900"))
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "5"))

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = DATA_DIR / "lemmy_token.json"
GLOBAL_MAP_FILE = DATA_DIR / "post_map.json"  # reddit_post_id -> { lemmy_post_id, ... }

# --------------------------
# Helpers
# --------------------------
def log(msg: str) -> None:
    print(msg, flush=True)

def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"⚠️ Failed reading {path}: {e}")
    return default

def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def parse_sub_map(raw: str) -> Dict[str, str]:
    # "a:b,c:d" -> {"a":"b","c":"d"}
    out = {}
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        if ":" in part:
            s, c = [x.strip() for x in part.split(":", 1)]
            if s and c:
                out[s] = c
    return out

def reddit_client() -> praw.Reddit:
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

# --------------------------
# Lemmy API
# --------------------------
def lemmy_login(force: bool = False) -> str:
    if not force and TOKEN_FILE.exists():
        token = load_json(TOKEN_FILE, {})
        if isinstance(token, dict) and token.get("jwt"):
            return token["jwt"]

    payload = {"username_or_email": LEMMY_USER, "password": LEMMY_PASS}
    url = f"{LEMMY_URL}/api/v3/user/login"
    log(f"🔑 Logging in to {url} as {LEMMY_USER}")
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Lemmy login failed: {r.status_code} {r.text}")
    jwt = r.json().get("jwt")
    if not jwt:
        raise RuntimeError("No JWT returned by Lemmy login")
    save_json(TOKEN_FILE, {"jwt": jwt, "cached_at": time.time()})
    log("✅ Logged into Lemmy (token cached)")
    return jwt

def resolve_community_id(jwt: str, community_name: str) -> Optional[int]:
    """
    Try both ?name=<name> and ?name=c/<name> for compatibility.
    Cache results in /app/data/community_cache.json.
    """
    cache_file = DATA_DIR / "community_cache.json"
    cache = load_json(cache_file, {})

    if community_name in cache:
        return cache[community_name]

    sess = requests.Session()
    headers = {"Authorization": f"Bearer {jwt}"}

    for nm in (community_name, f"c/{community_name}"):
        url = f"{LEMMY_URL}/api/v3/community"
        params = {"name": nm}
        try:
            r = sess.get(url, params=params, headers=headers, timeout=20)
            if r.status_code == 200:
                cid = r.json().get("community_view", {}).get("community", {}).get("id")
                if cid:
                    cache[community_name] = cid
                    save_json(cache_file, cache)
                    return cid
        except Exception:
            pass

    log(f"⚠️ Could not resolve community_id for '{community_name}'")
    return None

def create_lemmy_post(jwt: str, community_id: int, name: str,
                      body: Optional[str], url: Optional[str], nsfw: bool) -> Tuple[Optional[int], Optional[str]]:
    payload: Dict[str, Any] = {
        "name": name,
        "community_id": community_id,
        "nsfw": bool(nsfw),
        "auth": jwt,
    }
    if body:
        payload["body"] = body
    if url:
        payload["url"] = url

    for attempt in range(1, 4):
        try:
            r = requests.post(f"{LEMMY_URL}/api/v3/post", json=payload, timeout=30)
            if r.status_code == 200:
                pv = r.json().get("post_view", {})
                pid = pv.get("post", {}).get("id")
                apurl = pv.get("post", {}).get("ap_id")
                if pid:
                    return pid, apurl
                log(f"⚠️ Post created but no ID in response: {r.text}")
                return None, None
            elif r.status_code == 401:
                log("⚠️ Token invalid, refreshing...")
                jwt = lemmy_login(force=True)
                payload["auth"] = jwt
                continue
            elif r.status_code in (429, 502, 503):
                log(f"⏳ Lemmy backoff {r.status_code}, attempt {attempt}/3")
                time.sleep(5 * attempt)
                continue
            else:
                log(f"⚠️ Lemmy responded {r.status_code}: {r.text}")
                return None, None
        except requests.RequestException as e:
            log(f"⚠️ Network error during API call: {e} (attempt {attempt}/3)")
            time.sleep(3)

    log("❌ All retries failed for /api/v3/post")
    return None, None

# --------------------------
# Compose Lemmy body with rich info
# --------------------------
def compose_body_with_footer(title: str, submission) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (body, url)
    - body includes Reddit permalink + author
    - url is direct media/link if applicable (lets Lemmy render a preview)
    """
    permalink = f"https://www.reddit.com{submission.permalink}"
    author = f"u/{submission.author.name}" if submission.author else "u/[deleted]"
    nsfw = bool(getattr(submission, "over_18", False))

    base_footer = f"\n\n---\nMirrored from r/{submission.subreddit.display_name} by {author}\n{permalink}"

    # Self post
    if submission.is_self:
        body = (submission.selftext or "").strip()
        full_body = (body + base_footer).strip() if body else base_footer.strip()
        return full_body, None

    # Link / image / gallery
    link_url = getattr(submission, "url", None)
    body = f"(Preview link below)\n{base_footer}".strip()
    return body, link_url

# --------------------------
# Mapping writers
# --------------------------
def save_per_sub_map(subreddit: str, reddit_post_id: str, lemmy_post_id: int,
                     body_snapshot: str, permalink: str) -> None:
    sub_map_file = DATA_DIR / f"lemmy_map_{subreddit}.json"
    sub_map = load_json(sub_map_file, {})
    sub_map[reddit_post_id] = {
        "lemmy_post": lemmy_post_id,
        "last_body": body_snapshot,
        "permalink": permalink,
        "updated_at": int(time.time()),
    }
    save_json(sub_map_file, sub_map)

def save_global_map(reddit_post_id: str, lemmy_post_id: int,
                    subreddit: str, community: str, permalink: str) -> None:
    global_map = load_json(GLOBAL_MAP_FILE, {})
    global_map[reddit_post_id] = {
        "lemmy_post_id": lemmy_post_id,
        "subreddit": subreddit,
        "community": community,
        "permalink": permalink,
        "updated_at": int(time.time()),
    }
    save_json(GLOBAL_MAP_FILE, global_map)

# --------------------------
# Main loop
# --------------------------
def mirror_once():
    sub_map = parse_sub_map(SUB_MAP)
    if not sub_map:
        log("⚠️ SUB_MAP is empty. Nothing to mirror.")
        return

    reddit = reddit_client()
    jwt = lemmy_login()
    community_cache: Dict[str, int] = {}

    posts_mirrored = 0

    for subreddit, community in sub_map.items():
        try:
            # ensure community id
            if community not in community_cache:
                cid = resolve_community_id(jwt, community)
                if not cid:
                    log(f"⚠️ Skipping r/{subreddit}: unable to resolve community '{community}'")
                    continue
                community_cache[community] = cid
            community_id = community_cache[community]

            log(f"🔍 Checking r/{subreddit} → c/{community} @ {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
            per_file = DATA_DIR / f"lemmy_map_{subreddit}.json"
            already = set(load_json(per_file, {}).keys())

            # fetch new reddit posts
            submissions = list(reddit.subreddit(subreddit).new(limit=REDDIT_LIMIT))
            # newest last → post in chronological order
            submissions = list(reversed(submissions))

            for s in submissions:
                if posts_mirrored >= MAX_POSTS_PER_RUN:
                    break

                rid = s.id
                if rid in already:
                    continue

                title = (s.title or "").strip()[:300]
                body, link_url = compose_body_with_footer(title, s)
                nsfw = bool(getattr(s, "over_18", False))
                pid, ap = create_lemmy_post(
                    jwt=jwt,
                    community_id=community_id,
                    name=title,
                    body=body,
                    url=link_url,
                    nsfw=nsfw,
                )
                if pid:
                    log(f"✅ Posted '{title}' (Lemmy ID={pid})")
                    permalink = f"https://www.reddit.com{s.permalink}"
                    save_per_sub_map(subreddit, rid, pid, (s.selftext or "")[:5000], permalink)
                    save_global_map(rid, pid, subreddit, community, permalink)
                    log(f"💾 Added mapping: {rid} → Lemmy {pid}")
                    posts_mirrored += 1
                else:
                    log("⚠️ Failed to post submission.")

            log(f"✅ Done r/{subreddit} → c/{community}")
        except Exception as e:
            log(f"❌ Error processing r/{subreddit}: {e}")
            traceback.print_exc()

    if posts_mirrored == 0:
        log("ℹ️ No new posts this cycle.")

def main():
    while True:
        mirror_once()
        log(f"🕒 Sleeping {SLEEP_SECONDS}s...")
        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main()
