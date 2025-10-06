import os
import praw
import requests
import json
import time
import argparse
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# Reddit credentials
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    username=os.getenv("REDDIT_USERNAME"),
    password=os.getenv("REDDIT_PASSWORD"),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
)

# Lemmy credentials
LEMMY_URL = os.getenv("LEMMY_URL").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")

# Comment options
COMMENT_SLEEP = float(os.getenv("COMMENT_SLEEP", 0.3))
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
POST_MAP_PATH = os.path.join(DATA_DIR, "post_map.json")

# Parse CLI arguments
parser = argparse.ArgumentParser()
parser.add_argument("--refresh", action="store_true", help="Re-sync all existing posts and fill missing comments")
args = parser.parse_args()

# Simple Lemmy login
def lemmy_login():
    r = requests.post(f"{LEMMY_URL}/api/v3/user/login", json={
        "username_or_email": LEMMY_USER,
        "password": LEMMY_PASS
    })
    r.raise_for_status()
    jwt = r.json().get("jwt")
    if not jwt:
        raise RuntimeError(f"Failed to login to Lemmy: {r.text}")
    return jwt

jwt = lemmy_login()
headers = {"Authorization": f"Bearer {jwt}"}

# Load post mapping
if not os.path.exists(POST_MAP_PATH):
    print(f"⚠️ No mapping file found at {POST_MAP_PATH}. Run auto_mirror.py first.")
    exit()

with open(POST_MAP_PATH, "r") as f:
    post_map = json.load(f)

print(f"🔁 Loaded {len(post_map)} Reddit→Lemmy mappings")

# Lemmy helper: get all comment contents under a post
def get_existing_lemmy_comments(lemmy_post_id):
    try:
        r = requests.get(f"{LEMMY_URL}/api/v3/comment/list", params={
            "post_id": lemmy_post_id,
            "limit": 1000,
            "sort": "New"
        }, headers=headers)
        if r.status_code != 200:
            return set()
        comments = r.json().get("comments", [])
        return set(c["comment"]["content"].strip() for c in comments)
    except Exception as e:
        print(f"⚠️ Failed to fetch Lemmy comments for {lemmy_post_id}: {e}")
        return set()

# Lemmy helper: post a comment
def post_lemmy_comment(lemmy_post_id, content, parent_id=None):
    data = {"post_id": lemmy_post_id, "content": content}
    if parent_id:
        data["parent_id"] = parent_id
    r = requests.post(f"{LEMMY_URL}/api/v3/comment", json=data, headers=headers)
    if r.status_code != 200:
        print(f"⚠️ Lemmy error posting comment: {r.status_code} {r.text}")
    else:
        print(f"💬 Comment posted successfully → Lemmy ID {r.json()['comment_view']['comment']['id']}")
    time.sleep(COMMENT_SLEEP)

# Recursive function to mirror Reddit comment trees
def mirror_comments_tree(comments, existing_comments, lemmy_post_id, depth=0):
    for comment in comments:
        if isinstance(comment, praw.models.MoreComments):
            continue
        body = comment.body.strip()
        if body in existing_comments:
            print(f"{'  ' * depth}↩️ Skipping already mirrored comment by u/{comment.author}")
            continue
        post_lemmy_comment(lemmy_post_id, f"**u/{comment.author}:** {body}")
        existing_comments.add(body)
        if len(comment.replies) > 0:
            mirror_comments_tree(comment.replies, existing_comments, lemmy_post_id, depth + 1)

# Main loop
for reddit_id, lemmy_post_id in post_map.items():
    try:
        submission = reddit.submission(id=reddit_id.split("_")[-1])
        submission.comments.replace_more(limit=None)
        print(f"🧩 Syncing comments for Reddit post {submission.id} → Lemmy post {lemmy_post_id}")
        existing_comments = get_existing_lemmy_comments(lemmy_post_id)
        print(f"   {len(existing_comments)} existing Lemmy comments detected.")
        mirror_comments_tree(submission.comments, existing_comments, lemmy_post_id)
    except Exception as e:
        print(f"⚠️ Error syncing {reddit_id}: {e}")

print("✅ Comment sync complete.")
