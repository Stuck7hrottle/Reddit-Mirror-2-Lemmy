#!/usr/bin/env python3
"""
bridges/reddit_to_lemmy.py — Reddit → Lemmy bridge
---------------------------------------------------
Core bridge logic connecting RedditAdapter and LemmyClient.

Responsibilities:
  • Fetch a Reddit post and normalize it via RedditAdapter
  • Post it to Lemmy via LemmyClient
  • Store mapping in DB
  • Enqueue a background comment mirror job
"""

import json
import sqlite3
from datetime import datetime
from typing import Dict, Any

from sources.reddit_adapter import RedditAdapter
from destinations.lemmy_client import LemmyClient
from core.models import SourcePost, SourceComment
from db_cache import DB
from job_queue import JobDB


class RedditToLemmyBridge:
    """Bridge class that handles mirroring Reddit → Lemmy."""

    def __init__(self):
        self.reddit = RedditAdapter()
        self.lemmy = LemmyClient()
        self.db = DB()
        self.jobs = JobDB()

    # ───────────────────────────
    # Mirror a single post
    # ───────────────────────────
    async def mirror_post(self, reddit_id: str) -> Dict[str, Any]:
        """
        Fetch a Reddit submission, post it to Lemmy, store mapping, and queue comments.
        Returns {'lemmy_id': int}
        """
        post: SourcePost | None = self.reddit.fetch_submission(reddit_id)
        if not post:
            raise RuntimeError(f"Failed to fetch Reddit submission {reddit_id}")

        # Resolve community mapping (fallback to same name)
        community_name = post.community.lower()

        community_id = self.lemmy.get_community_id(community_name)
        lemmy_id = self.lemmy.create_post(
            title=post.title,
            body=post.body,
            community_id=community_id,
            url=post.url,
        )

        self.db.save_post(
            source="reddit",
            source_post_id=reddit_id,
            lemmy_id=str(lemmy_id),
            community=community_name,
        )

        # Enqueue background comment mirror job
        payload = {
            "source": "reddit",
            "reddit_post_id": reddit_id,
            "lemmy_post_id": lemmy_id,
        }
        self._enqueue_comment_job(payload)

        return {"lemmy_id": lemmy_id}

    # ───────────────────────────
    # Mirror comments (optional direct call)
    # ───────────────────────────
    async def mirror_comments(self, reddit_id: str, lemmy_post_id: int):
        """Mirror comments from Reddit to the given Lemmy post."""
        comments = self.reddit.fetch_comments(reddit_id)
        if not comments:
            print(f"✅ No comments to mirror for Reddit post {reddit_id}.")
            return

        jwt = self.lemmy.jwt
        url = f"{self.lemmy.base_url}/api/v3/comment"
        headers = {"Authorization": f"Bearer {jwt}"}

        import requests, time
        for c in comments:
            payload = {"content": c.body, "post_id": int(lemmy_post_id)}
            try:
                r = requests.post(url, json=payload, headers=headers, timeout=20)
                if not r.ok:
                    print(f"⚠️ Failed to post comment: {r.status_code} {r.text[:200]}")
                time.sleep(2)
            except Exception as e:
                print(f"⚠️ Error posting comment: {e}")
                continue
        print(f"✅ Mirrored {len(comments)} comments from Reddit {reddit_id} → Lemmy {lemmy_post_id}")

    # ───────────────────────────
    # Enqueue helper
    # ───────────────────────────
    def _enqueue_comment_job(self, payload: Dict[str, Any]):
        """Insert mirror_comment job if not already queued."""
        try:
            conn = sqlite3.connect("data/jobs.db")
            cur = conn.execute(
                "SELECT 1 FROM jobs WHERE type='mirror_comment' "
                "AND json_extract(payload, '$.reddit_post_id') = ?",
                (payload["reddit_post_id"],),
            )
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO jobs (type, payload, status, retries, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "mirror_comment",
                        json.dumps(payload),
                        "queued",
                        0,
                        datetime.utcnow().isoformat(),
                        datetime.utcnow().isoformat(),
                    ),
                )
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ Failed to enqueue comment mirror job: {e}")
