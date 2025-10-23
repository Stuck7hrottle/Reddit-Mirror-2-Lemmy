#!/usr/bin/env python3
"""
Lemmy → Reddit Comment Mirror (Two-Way Bridge, Humanized + Media Mirroring)
- Uses separate Reddit creds (LEMMY_TO_REDDIT_*).
- Humanized text to reduce spam/duplicate-template signals.
- Mirrors comment media: rehosts images to Lemmy /pictrs; labels videos.
- No direct Lemmy permalink in the body (spam-safe).
- Persistent media cache in DATA_DIR/media_cache.json.
"""

import os
import re
import time
import random
import requests
import praw
from prawcore.exceptions import RequestException, ResponseException, Forbidden

from db_cache import DB
from utils import get_valid_token, log, log_error
from auto_backfill import is_first_run, mark_backfill_complete
from mirror_media import find_urls, mirror_url

LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
DATA_DIR = os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data")

SYNC_INTERVAL_SECS = int(os.getenv("LEMMY_COMMENT_SYNC_INTERVAL", "600"))  # 10 min default
LEMMY_COMMENT_FETCH_LIMIT = int(os.getenv("LEMMY_COMMENT_FETCH_LIMIT", "50"))

# ───────────────────────────────
# Reddit + Lemmy setup
# ───────────────────────────────
def create_reddit_client():
    """Initialize Reddit client using second bot credentials."""
    return praw.Reddit(
        client_id=os.getenv("LEMMY_TO_REDDIT_CLIENT_ID"),
        client_secret=os.getenv("LEMMY_TO_REDDIT_CLIENT_SECRET"),
        username=os.getenv("LEMMY_TO_REDDIT_USERNAME"),
        password=os.getenv("LEMMY_TO_REDDIT_PASSWORD"),
        user_agent=os.getenv("LEMMY_TO_REDDIT_USER_AGENT", "LemmySyncBot/1.0"),
    )

# ───────────────────────────────
# Formatting (humanized + media)
# ───────────────────────────────
def _human_intro(full_user: str) -> str:
    intros = [
        f"Sharing a comment originally made by Lemmy user {full_user}:",
        f"A Lemmy user ({full_user}) shared this thought:",
        f"From a Lemmy discussion by {full_user}:",
        f"This was mentioned on Lemmy by {full_user}:",
        f"Lemmy user {full_user} said:",
    ]
    return random.choice(intros)

def format_lemmy_comment_body(comment_json):
    """Create a Reddit-safe, natural comment with mirrored media and no Lemmy links."""
    comment = comment_json.get("comment", {})
    creator = comment_json.get("creator", {})
    lemmy_user = creator.get("name") or "unknown"
    instance = creator.get("actor_id", "")

    if instance and "//" in instance:
        instance = instance.split("//")[1].split("/")[0]
        full_user = f"@{lemmy_user}@{instance}"
    else:
        full_user = f"@{lemmy_user}"

    content = (comment.get("content") or "").strip()
    if not content:
        return None

    # Extract & mirror media; remove raw URLs from visible content
    urls = find_urls(content)
    mirrored_links: list[str] = []
    for u in urls:
        try:
            m = mirror_url(u)
            if m:
                mirrored_links.append(m)
                content = content.replace(u, "")
        except Exception as e:
            log_error("lemmy_comment_sync.mirror_media", e)

    intro = _human_intro(full_user)

    body_parts = [intro, "", content.strip()]

    if mirrored_links:
        body_parts.append("")
        body_parts.append("📸 **Mirrored media:**")
        for m in mirrored_links:
            body_parts.append(f"- {m}")

    # No Lemmy permalink to avoid link-only spam triggers
    body_parts.append("")
    body_parts.append("_(Mirrored for cross-platform discussion — no direct Lemmy link included.)_")

    body = "\n".join([p for p in body_parts if p is not None])
    return body.strip()

# ───────────────────────────────
# Lemmy → Reddit logic
# ───────────────────────────────
def fetch_recent_lemmy_comments(jwt, limit=LEMMY_COMMENT_FETCH_LIMIT):
    try:
        url = f"{LEMMY_URL}/api/v3/comment/list"
        params = {"sort": "New", "limit": limit, "page": 1, "auth": jwt}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("comments", [])
    except Exception as e:
        log_error("lemmy_comment_sync.fetch_comments", e)
        return []

def safe_post_to_reddit(post_fn, retries=3, cooldown=30):
    for attempt in range(1, retries + 1):
        try:
            return post_fn()
        except (RequestException, ResponseException, Forbidden) as e:
            log_error(f"Reddit API error (attempt {attempt})", e)
            if attempt < retries:
                sleep_time = cooldown * attempt
                log(f"⏳ Cooling down {sleep_time}s before retry...")
                time.sleep(sleep_time)
            else:
                raise

def sync_lemmy_to_reddit(force_backfill=False):
    db = DB()
    jwt = get_valid_token(
        username=os.getenv("LEMMY_COMMENT_USER", os.getenv("LEMMY_USER")),
        password=os.getenv("LEMMY_COMMENT_PASS", os.getenv("LEMMY_PASS")),
    )
    reddit = create_reddit_client()

    comments = fetch_recent_lemmy_comments(jwt, limit=LEMMY_COMMENT_FETCH_LIMIT)
    if not comments:
        log("🗒️ No Lemmy comments found to process.")
        return

    for c in comments:
        try:
            comment_id = str(c.get("comment", {}).get("id"))
            if not comment_id:
                continue

            if not force_backfill and db.get_reddit_comment_id(comment_id):
                continue

            post_id = c.get("comment", {}).get("post_id")
            if not post_id:
                continue

            reddit_post_id = db.get_reddit_post_id(str(post_id))
            if not reddit_post_id:
                continue  # only mirror for posts we mirrored earlier

            body = format_lemmy_comment_body(c)
            if not body:
                continue

            parent_id = c.get("comment", {}).get("parent_id")
            parent_reddit_id = db.get_reddit_comment_id(str(parent_id)) if parent_id else None

            def do_post():
                submission = reddit.submission(id=reddit_post_id)
                if parent_reddit_id:
                    return reddit.comment(id=parent_reddit_id).reply(body)
                return submission.reply(body)

            reddit_comment = safe_post_to_reddit(do_post)
            db.save_comment(
                reddit_id=reddit_comment.id,
                lemmy_id=comment_id,
                parent_reddit_id=parent_reddit_id,
                parent_lemmy_id=str(parent_id) if parent_id else None,
                source="lemmy",
            )

            log(f"💬 Mirrored Lemmy comment {comment_id} → Reddit comment {reddit_comment.id}")
            delay = 2 + random.random() * 4  # 2–6s delay
            log(f"⏱️ Sleeping {delay:.1f}s before next comment...")
            time.sleep(delay)

        except Exception as e:
            log_error("lemmy_comment_sync.loop", e)
            continue

# ───────────────────────────────
# Main loop
# ───────────────────────────────
def mirror_lemmy_comments(run_once: bool = False):
    log("🚀 Starting Lemmy → Reddit comment sync loop")
    db = DB()

    if is_first_run(db, flag_name="lemmy_backfill_done"):
        log("🆕 First run detected — performing one-time Lemmy comment backfill...")
        try:
            sync_lemmy_to_reddit(force_backfill=True)
            mark_backfill_complete(db, flag_name="lemmy_backfill_done")
            log("✅ Lemmy comment backfill complete — future runs will only sync new comments.")
        except Exception as e:
            log_error("lemmy_comment_sync.backfill", e)

    while True:
        try:
            sync_lemmy_to_reddit()
        except Exception as e:
            log_error("lemmy_comment_sync.loop", e)

        if run_once:
            log("🧩 run_once=True → exiting after single Lemmy→Reddit sync pass.")
            break

        jitter = random.randint(-60, 60)  # ±1 minute jitter
        interval = max(60, SYNC_INTERVAL_SECS + jitter)
        log(f"⏳ Sleeping {interval // 60} minutes before next check…")
        time.sleep(interval)

def main():
    delay = int(os.getenv("LEMMY_COMMENT_SYNC_STARTUP_DELAY", "60"))
    log(f"⏳ Waiting {delay}s before starting Lemmy→Reddit comment sync...")
    time.sleep(delay)
    mirror_lemmy_comments()

if __name__ == "__main__":
    main()

# Compatibility shim for background worker
def mirror_lemmy_comments_to_reddit(payload=None):
    try:
        log("🧩 mirror_lemmy_comments_to_reddit() called (compatibility shim).")
        mirror_lemmy_comments(run_once=True)
    except Exception as e:
        log_error("mirror_lemmy_comments_to_reddit", e)