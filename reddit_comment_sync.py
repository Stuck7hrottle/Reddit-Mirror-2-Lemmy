#!/usr/bin/env python3
"""
Reddit → Lemmy Comment Mirror (Selective, Safe Two-Way, Media Complete)
- Adds author label (u/Name) and mirrors images/videos found in Reddit comments.
- Images rehosted to Lemmy /pictrs; videos kept as labeled outbound links.
- Persistent media cache in DATA_DIR/media_cache.json.
"""

import os
import time
import praw
import requests

from db_cache import DB
from utils import get_valid_token, log, log_error
from auto_backfill import is_first_run, mark_backfill_complete
from mirror_media import find_urls, mirror_url

LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
DATA_DIR = os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data")

SYNC_INTERVAL_SECS = int(os.getenv("REDDIT_COMMENT_SYNC_INTERVAL", "600"))  # 10 minutes default
REDDIT_BOT_USERNAME = os.getenv("REDDIT_BOT_USERNAME", "").lower()

def create_reddit_client():
    return praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        username=os.getenv("REDDIT_USERNAME"),
        password=os.getenv("REDDIT_PASSWORD"),
        user_agent=os.getenv("REDDIT_USER_AGENT", "RedditCommentBridge/1.3"),
    )

def format_reddit_comment_body(comment):
    """Generate Markdown for Lemmy with mirrored media list."""
    author = getattr(comment, "author", None)
    name = getattr(author, "name", None)
    author_line = f"**u/{name}:**" if name else "**u/[deleted]:**"

    body = (getattr(comment, "body", "") or "").strip()
    if not body or body in ("[deleted]", "[removed]"):
        return None

    # Extract & mirror media; remove raw URLs from visible text
    urls = find_urls(body)
    mirrored_links: list[str] = []
    for u in urls:
        try:
            m = mirror_url(u)
            if m:
                mirrored_links.append(m)
                body = body.replace(u, "")
        except Exception as e:
            log_error("reddit_comment_sync.mirror_media", e)

    parts = [author_line, "", body.strip()]
    if mirrored_links:
        parts += ["", "📸 **Mirrored media:**"]
        for m in mirrored_links:
            parts.append(f"- {m}")

    # Foodnote keeps it tidy without outbound Reddit permalinks (optional)
    parts += ["", "— _Mirrored from Reddit_"]
    return "\n".join([p for p in parts if p]).strip()

def mirror_new_reddit_replies(force_backfill=False):
    db = DB()
    reddit = create_reddit_client()

    # Load mirrored posts map (Reddit↔Lemmy)
    try:
        conn = db._get_conn()
        mirrored_posts = conn.execute("SELECT reddit_id, lemmy_id FROM posts").fetchall()
        conn.close()
    except Exception as e:
        log_error("reddit_comment_sync.load_posts", e)
        return

    # Use DB-based ignored posts tracking
    ignored_posts = set(db.get_ignored_posts())

    for reddit_post_id, lemmy_post_id in mirrored_posts:
        # 🔒 Skip permanently ignored posts
        if reddit_post_id in ignored_posts:
            log(f"🚫 Permanently ignoring previously skipped post {reddit_post_id}")
            continue

        # 💡 Fetch a fresh JWT at the start of every batch
        jwt = get_valid_token(
            username=os.getenv("LEMMY_COMMENT_USER", os.getenv("LEMMY_USER")),
            password=os.getenv("LEMMY_COMMENT_PASS", os.getenv("LEMMY_PASS")),
        )
        headers = {"Authorization": f"Bearer {jwt}"}

        try:
            submission = reddit.submission(id=reddit_post_id)
            try:
                submission.comments.replace_more(limit=None)
                comments = submission.comments.list()
            except Exception as e:
                # Gracefully handle forbidden/deleted/private posts
                if "403" in str(e) or "forbidden" in str(e).lower():
                    log(f"⚠️ Skipping Reddit post {reddit_post_id} — access forbidden or removed.")
                    db.mark_post_ignored(reddit_post_id, reason="forbidden")
                    continue
                log_error("reddit_comment_sync.fetch_comments", e)
                continue

            for rc in comments:
                # Skip bot's own Reddit comments to avoid loops
                if REDDIT_BOT_USERNAME and str(rc.author).lower() == REDDIT_BOT_USERNAME:
                    continue

                reddit_comment_id = rc.id
                parent_id = rc.parent_id  # "t1_xxx" or "t3_xxx"

                # Skip duplicates unless forcing backfill
                if not force_backfill and db.get_lemmy_comment_id(reddit_comment_id):
                    continue

                formatted = format_reddit_comment_body(rc)
                if not formatted or len(formatted.strip()) < 3:
                    continue

                payload = {"content": formatted, "post_id": int(lemmy_post_id)}
                parent_lemmy_id = None

                if parent_id.startswith("t3_"):  # reply to post
                    if not db.get_lemmy_post_id(reddit_post_id):
                        continue
                elif parent_id.startswith("t1_"):  # reply to comment
                    parent_reddit_id = parent_id.split("_", 1)[1]
                    parent_lemmy_id = db.get_lemmy_comment_id(parent_reddit_id)
                    if not parent_lemmy_id:
                        continue
                    payload["parent_id"] = int(parent_lemmy_id)
                else:
                    continue

                try:
                    r = requests.post(
                        f"{LEMMY_URL}/api/v3/comment",
                        json=payload,
                        headers=headers,
                        timeout=20,
                    )

                    # 🔄 Refresh on 401
                    if r.status_code == 401:
                        jwt = get_valid_token(
                            username=os.getenv("LEMMY_COMMENT_USER", os.getenv("LEMMY_USER")),
                            password=os.getenv("LEMMY_COMMENT_PASS", os.getenv("LEMMY_PASS")),
                        )
                        headers = {"Authorization": f"Bearer {jwt}"}
                        r = requests.post(
                            f"{LEMMY_URL}/api/v3/comment",
                            json=payload,
                            headers=headers,
                            timeout=20,
                        )

                    # 🧩 Retry once if Lemmy DB lag / transient 400
                    if r.status_code == 400 and "couldnt_create_comment" in r.text:
                        log(f"⚠️ Lemmy rejected comment — retrying once after 3s (r:{reddit_comment_id})…")
                        time.sleep(3)
                        jwt = get_valid_token(
                            username=os.getenv("LEMMY_COMMENT_USER", os.getenv("LEMMY_USER")),
                            password=os.getenv("LEMMY_COMMENT_PASS", os.getenv("LEMMY_PASS")),
                        )
                        headers = {"Authorization": f"Bearer {jwt}"}
                        r = requests.post(
                            f"{LEMMY_URL}/api/v3/comment",
                            json=payload,
                            headers=headers,
                            timeout=20,
                        )

                    if not r.ok:
                        log(f"⚠️ Failed Lemmy comment ({r.status_code}): {r.text[:150]}")
                        continue

                    lemmy_comment_id = r.json()["comment_view"]["comment"]["id"]

                    db.save_comment(
                        reddit_id=reddit_comment_id,
                        lemmy_id=str(lemmy_comment_id),
                        parent_reddit_id=parent_id.split("_", 1)[1] if parent_id.startswith("t1_") else None,
                        parent_lemmy_id=str(parent_lemmy_id) if parent_lemmy_id else None,
                        source="reddit",
                    )

                    log(f"💬 Mirrored Reddit comment u/{getattr(rc.author, 'name', '[deleted]')} "
                        f"(r:{reddit_comment_id} → l:{lemmy_comment_id})")

                    # 🕒 Gentle per-comment delay
                    import random
                    time.sleep(2.5 + random.uniform(0.5, 1.5))

                except Exception as e:
                    log_error("reddit_comment_sync.mirror_comment", e)
                    continue

        except Exception as e:
            log_error("reddit_comment_sync.post_loop", e)
            continue

def main():
    delay = int(os.getenv("REDDIT_COMMENT_SYNC_STARTUP_DELAY", "90"))
    log(f"⏳ Waiting {delay}s before starting Reddit→Lemmy comment sync...")
    time.sleep(delay)

    log("🚀 Starting Reddit → Lemmy comment sync loop")
    db = DB()

    if is_first_run(db, flag_name="reddit_backfill_done"):
        log("🆕 First run detected — performing one-time Reddit comment backfill...")
        try:
            mirror_new_reddit_replies(force_backfill=True)
            mark_backfill_complete(db, flag_name="reddit_backfill_done")
            log("✅ Reddit comment backfill complete — future runs will only sync new comments.")
        except Exception as e:
            log_error("reddit_comment_sync.backfill", e)

    while True:
        try:
            mirror_new_reddit_replies()
        except Exception as e:
            log_error("reddit_comment_sync.loop", e)
        log(f"⏳ Sleeping {SYNC_INTERVAL_SECS // 60} minutes before next Reddit→Lemmy sync…")
        time.sleep(SYNC_INTERVAL_SECS)

if __name__ == "__main__":
    main()