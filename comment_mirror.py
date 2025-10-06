#!/usr/bin/env python3
"""
comment_mirror.py
-----------------
Mirrors comments from Reddit threads into their corresponding Lemmy posts.

Reads mapping data saved by auto_mirror.py (reddit_post_id → lemmy_post_id)
and recreates the comment trees on Lemmy.

Environment variables are loaded from .env:
    MIRROR_COMMENTS=true
    COMMENT_LIMIT=3
    COMMENT_LIMIT_TOTAL=500
    COMMENT_SLEEP=0.3
    DATA_DIR=/data
"""

import os
import json
import time
import praw
import requests
from dotenv import load_dotenv

load_dotenv()

# Reddit setup
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    username=os.getenv("REDDIT_USERNAME"),
    password=os.getenv("REDDIT_PASSWORD"),
    user_agent=os.getenv("REDDIT_USER_AGENT", "reddit-lemmy-bridge/1.0"),
)

# Lemmy setup
LEMMY_URL = os.getenv("LEMMY_URL")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")

DATA_DIR = os.getenv("DATA_DIR", "/data")
MIRROR_COMMENTS = os.getenv("MIRROR_COMMENTS", "true").lower() == "true"
COMMENT_LIMIT = int(os.getenv("COMMENT_LIMIT", 3))
COMMENT_LIMIT_TOTAL = int(os.getenv("COMMENT_LIMIT_TOTAL", 500))
COMMENT_SLEEP = float(os.getenv("COMMENT_SLEEP", 0.3))

# ---------------- Lemmy auth -----------------
TOKEN_CACHE = os.path.join(DATA_DIR, "lemmy_token.json")


def lemmy_login():
    """Authenticate with Lemmy and cache JWT."""
    if os.path.exists(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE, "r") as f:
                data = json.load(f)
            if time.time() - data["time"] < 3600:  # valid for an hour
                return data["jwt"]
        except Exception:
            pass

    print(f"🔑 Logging in to {LEMMY_URL} as {LEMMY_USER}")
    resp = requests.post(f"{LEMMY_URL}/api/v3/user/login", json={
        "username_or_email": LEMMY_USER,
        "password": LEMMY_PASS
    })
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to login to Lemmy: {resp.text}")
    jwt = resp.json()["jwt"]
    with open(TOKEN_CACHE, "w") as f:
        json.dump({"jwt": jwt, "time": time.time()}, f)
    return jwt


# ---------------- Helpers -----------------
def post_comment(lemmy_post_id, parent_id, content, jwt):
    """Post a comment to Lemmy."""
    payload = {
        "post_id": lemmy_post_id,
        "content": content,
    }
    if parent_id:
        payload["parent_id"] = parent_id

    headers = {"Authorization": f"Bearer {jwt}"}
    resp = requests.post(f"{LEMMY_URL}/api/v3/comment", json=payload, headers=headers)
    if resp.status_code == 200:
        return resp.json()["comment_view"]["comment"]["id"]
    else:
        print(f"⚠️ Failed to post comment: {resp.status_code} {resp.text}")
        return None


def mirror_comments_for_post(reddit_post_id, lemmy_post_id, jwt):
    """Recursively mirror all comments from a single Reddit submission."""
    submission = reddit.submission(id=reddit_post_id)
    submission.comments.replace_more(limit=None)
    count = 0

    def process_comment(c, parent_lemmy_id=None):
        nonlocal count
        if count >= COMMENT_LIMIT_TOTAL:
            return

        author = c.author.name if c.author else "[deleted]"
        text = f"**{author}** said:\n\n{c.body}"
        lemmy_comment_id = po
