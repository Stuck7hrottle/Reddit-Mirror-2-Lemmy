#!/usr/bin/env python3
"""
reddit_adapter.py — Reddit source adapter for Lemmy bridge
----------------------------------------------------------
This module isolates all Reddit-specific logic.

It fetches new Reddit submissions and comments,
normalizes them into SourcePost / SourceComment objects,
and yields them to the mirror bridge for posting to Lemmy.

✅ Uses: core.models.SourcePost, SourceComment
✅ Handles: OAuth (if available) and fallback public JSON
✅ Provides: fetch_submissions(), fetch_submission(), fetch_comments()
"""

import os
import time
import requests
import logging
from typing import Optional, List
from core.models import SourcePost, SourceComment

logger = logging.getLogger(__name__)

USER_AGENT = os.getenv("REDDIT_USER_AGENT", "RedditToLemmyBridge/2.0")
CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")


# ───────────────────────────────
# Reddit Adapter Class
# ───────────────────────────────
class RedditAdapter:
    """Handles Reddit API interactions and normalization."""

    def __init__(self):
        self.base_api = "https://www.reddit.com"
        self.oauth_api = "https://oauth.reddit.com"
        self.user_agent = USER_AGENT
        self.token = self._get_token() if CLIENT_ID and CLIENT_SECRET else None

    # ───────────────────────────
    # Auth / Token
    # ───────────────────────────
    def _get_token(self) -> Optional[str]:
        """Retrieve OAuth token if credentials available."""
        try:
            auth = requests.auth.HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)
            data = {"grant_type": "client_credentials"}
            headers = {"User-Agent": self.user_agent}
            r = requests.post("https://www.reddit.com/api/v1/access_token", auth=auth, data=data, headers=headers, timeout=15)
            if r.ok:
                token = r.json().get("access_token")
                logger.info("🔑 Obtained Reddit OAuth token")
                return token
            logger.warning(f"⚠️ Reddit OAuth failed: {r.status_code} {r.text[:120]}")
        except Exception as e:
            logger.warning(f"⚠️ Reddit token fetch error: {e}")
        return None

    def _headers(self) -> dict:
        headers = {"User-Agent": self.user_agent}
        if self.token:
            headers["Authorization"] = f"bearer {self.token}"
        return headers

    # ───────────────────────────
    # Fetch Submissions
    # ───────────────────────────
    def fetch_submissions(self, subreddit: str, limit: int = 10) -> List[SourcePost]:
        """Fetch latest posts from a subreddit."""
        posts: List[SourcePost] = []
        url = f"{self.base_api}/r/{subreddit}/new.json"
        if self.token:
            url = f"{self.oauth_api}/r/{subreddit}/new.json"

        params = {"limit": limit}
        for attempt in range(3):
            r = requests.get(url, headers=self._headers(), params=params, timeout=20)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.info(f"⏳ Reddit rate limited, waiting {wait}s…")
                time.sleep(wait)
                continue
            if not r.ok:
                logger.warning(f"⚠️ Reddit fetch error: {r.status_code} {r.text[:100]}")
                return posts
            break

        data = r.json().get("data", {}).get("children", [])
        for item in data:
            sub = item.get("data", {})
            posts.append(self._to_source_post(sub))
        logger.info(f"📥 fetched {len(posts)} new posts from r/{subreddit}")
        return posts

    # ───────────────────────────
    # Fetch Single Submission
    # ───────────────────────────
    def fetch_submission(self, post_id: str) -> Optional[SourcePost]:
        """Fetch a specific Reddit submission by ID."""
        url = f"{self.base_api}/comments/{post_id}.json"
        if self.token:
            url = f"{self.oauth_api}/by_id/t3_{post_id}.json"

        for attempt in range(3):
            r = requests.get(url, headers=self._headers(), timeout=15)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if not r.ok:
                logger.warning(f"⚠️ Reddit fetch failed for {post_id}: {r.status_code}")
                return None
            break

        try:
            data = r.json()
            if isinstance(data, list):
                listing = data[0].get("data", {}).get("children", [])
                if listing:
                    return self._to_source_post(listing[0].get("data", {}))
            elif isinstance(data, dict):
                children = data.get("data", {}).get("children", [])
                if children:
                    return self._to_source_post(children[0].get("data", {}))
        except Exception as e:
            logger.warning(f"⚠️ Failed to parse Reddit JSON for {post_id}: {e}")
        return None

    # ───────────────────────────
    # Fetch Comments
    # ───────────────────────────
    def fetch_comments(self, post_id: str, limit: int = 500) -> List[SourceComment]:
        """Fetch comment tree for a submission."""
        comments: List[SourceComment] = []
        url = f"{self.base_api}/comments/{post_id}.json"
        if self.token:
            url = f"{self.oauth_api}/comments/{post_id}.json"

        r = requests.get(url, headers=self._headers(), timeout=30)
        if not r.ok:
            logger.warning(f"⚠️ Failed to fetch comments for {post_id}: {r.status_code}")
            return comments

        try:
            data = r.json()
            if isinstance(data, list) and len(data) > 1:
                comments_data = data[1].get("data", {}).get("children", [])
                for c in comments_data:
                    if c.get("kind") != "t1":
                        continue
                    comments.append(self._to_source_comment(c.get("data", {})))
        except Exception as e:
            logger.warning(f"⚠️ Error parsing comments for {post_id}: {e}")
        logger.info(f"💬 fetched {len(comments)} comments for Reddit post {post_id}")
        return comments

    # ───────────────────────────
    # Normalization
    # ───────────────────────────
    def _to_source_post(self, sub: dict) -> SourcePost:
        """Convert Reddit JSON to a SourcePost."""
        return SourcePost(
            source="reddit",
            id=sub.get("id", ""),
            title=sub.get("title", "[untitled]"),
            body=sub.get("selftext", "") or "",
            author=(sub.get("author") or "[deleted]"),
            community=sub.get("subreddit", "").lower(),
            created_utc=float(sub.get("created_utc", 0)),
            url=sub.get("url"),
            metadata={
                "permalink": sub.get("permalink"),
                "domain": sub.get("domain"),
                "is_gallery": sub.get("is_gallery"),
                "media_metadata": sub.get("media_metadata"),
                "over_18": sub.get("over_18"),
            },
        )

    def _to_source_comment(self, c: dict) -> SourceComment:
        """Convert Reddit JSON to a SourceComment."""
        return SourceComment(
            source="reddit",
            id=c.get("id", ""),
            post_id=c.get("link_id", "").replace("t3_", ""),
            author=(c.get("author") or "[deleted]"),
            body=c.get("body", ""),
            parent_id=c.get("parent_id"),
            created_utc=float(c.get("created_utc", 0)),
            metadata={"score": c.get("score")},
        )
