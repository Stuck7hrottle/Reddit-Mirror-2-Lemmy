#!/usr/bin/env python3
"""
lemmy_client.py — Unified Lemmy API client
------------------------------------------
Handles:
  ✅ Token reuse + refresh (with file-lock safety)
  ✅ Community lookup and caching
  ✅ Post and comment creation with retries
  ✅ Generic API helper for future bridge adapters
"""

import os
import json
import time
import requests
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ───────────────────────────────
# Configuration
# ───────────────────────────────
DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

LEMMY_URL = os.getenv("LEMMY_URL", "https://example.com").rstrip("/")
LEMMY_USER = os.getenv("LEMMY_USER")
LEMMY_PASS = os.getenv("LEMMY_PASS")

TOKEN_PATH = DATA_DIR / "lemmy_token.json"
TOKEN_LOCK = DATA_DIR / "login.lock"
COMMUNITY_CACHE = DATA_DIR / "community_map.json"

TOKEN_TTL_HOURS = 4
LOGIN_LOCK_TIMEOUT = 90


# ───────────────────────────────
# Utility Functions
# ───────────────────────────────
def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_json(path: Path, data: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _acquire_login_lock() -> bool:
    if TOKEN_LOCK.exists():
        age = time.time() - TOKEN_LOCK.stat().st_mtime
        if age < LOGIN_LOCK_TIMEOUT:
            logger.info(f"🕒 Recent Lemmy login {age:.0f}s ago — skipping new login.")
            return False
    TOKEN_LOCK.touch()
    return True


# ───────────────────────────────
# Lemmy Client Class
# ───────────────────────────────
class LemmyClient:
    def __init__(self):
        self.base_url = LEMMY_URL
        self.user = LEMMY_USER
        self.password = LEMMY_PASS
        self.jwt = self._get_valid_token()

    # ───────────────────────────
    # Authentication
    # ───────────────────────────
    def _get_valid_token(self) -> str:
        """Return a valid Lemmy JWT from cache or refresh if needed."""
        data = _read_json(TOKEN_PATH)
        jwt = data.get("jwt")
        expires = data.get("expires")

        # Reuse if valid
        if jwt and expires:
            try:
                if datetime.utcnow() < datetime.fromisoformat(expires):
                    return jwt
            except Exception:
                pass

        # Refresh if missing/expired
        return self._refresh_token()

    def _refresh_token(self) -> str:
        """Login to Lemmy and store new token."""
        if not all([self.base_url, self.user, self.password]):
            raise RuntimeError("Missing Lemmy credentials")

        if not _acquire_login_lock():
            # Another process logged in recently; wait and retry
            time.sleep(5)
            data = _read_json(TOKEN_PATH)
            if "jwt" in data and "expires" in data:
                try:
                    if datetime.utcnow() < datetime.fromisoformat(data["expires"]):
                        logger.info("♻️ Using freshly refreshed token from another process.")
                        return data["jwt"]
                except Exception:
                    pass

        url = f"{self.base_url}/api/v3/user/login"
        payload = {"username_or_email": self.user, "password": self.password}
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"Lemmy login failed: {r.status_code} {r.text}")

        data = r.json()
        jwt = data.get("jwt")
        if not jwt:
            raise RuntimeError("Lemmy returned no JWT")

        _write_json(
            TOKEN_PATH,
            {"jwt": jwt, "expires": (_now_iso() if TOKEN_TTL_HOURS <= 0 else (datetime.utcnow() + timedelta(hours=TOKEN_TTL_HOURS)).isoformat())},
        )
        logger.info("🔑 Lemmy token refreshed and cached.")
        return jwt

    # ───────────────────────────
    # Community Management
    # ───────────────────────────
    def get_community_id(self, name: str) -> Optional[int]:
        """Fetch a community ID by name (cached)."""
        cache = _read_json(COMMUNITY_CACHE)
        if name in cache:
            return cache[name]

        url = f"{self.base_url}/api/v3/community"
        params = {"name": name}
        headers = {"Authorization": f"Bearer {self.jwt}"}

        try:
            r = requests.get(url, params=params, headers=headers, timeout=20)
            if r.status_code != 200:
                logger.warning(f"⚠️ Failed to fetch community {name}: {r.status_code}")
                return None
            cid = r.json().get("community_view", {}).get("community", {}).get("id")
            if cid:
                cache[name] = cid
                _write_json(COMMUNITY_CACHE, cache)
                return cid
        except Exception as e:
            logger.warning(f"⚠️ get_community_id error: {e}")
        return None

    # ───────────────────────────
    # Post Creation
    # ───────────────────────────
    def create_post(self, title: str, body: str, community_id: int, url: Optional[str] = None) -> int:
        """Create a Lemmy post; returns Lemmy post ID."""
        endpoint = f"{self.base_url}/api/v3/post"
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"name": title, "community_id": community_id}
        if body:
            payload["body"] = body
        if url:
            payload["url"] = url

        for attempt in range(1, 6):
            r = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            if r.status_code == 401 and attempt == 1:
                self.jwt = self._refresh_token()
                headers["Authorization"] = f"Bearer {self.jwt}"
                continue
            if r.status_code == 400 and "rate_limit" in r.text:
                wait = 10 * attempt
                logger.info(f"⏳ Rate limited — retrying in {wait}s")
                time.sleep(wait)
                continue
            if not r.ok:
                logger.warning(f"⚠️ Post failed ({r.status_code}): {r.text[:120]}")
                continue
            post_id = r.json().get("post_view", {}).get("post", {}).get("id")
            if post_id:
                logger.info(f"✅ Created Lemmy post ID={post_id}")
                return post_id
        raise RuntimeError("Lemmy post creation failed after retries")

    # ───────────────────────────
    # Comment Creation
    # ───────────────────────────
    def create_comment(self, post_id: int, body: str) -> Optional[int]:
        """Post a comment under an existing Lemmy post."""
        endpoint = f"{self.base_url}/api/v3/comment"
        headers = {"Authorization": f"Bearer {self.jwt}"}
        payload = {"content": body, "post_id": int(post_id)}

        for attempt in range(1, 5):
            r = requests.post(endpoint, json=payload, headers=headers, timeout=20)
            if r.status_code == 401 and attempt == 1:
                self.jwt = self._refresh_token()
                headers["Authorization"] = f"Bearer {self.jwt}"
                continue
            if r.status_code == 400 and "rate_limit" in r.text:
                wait = 5 * attempt
                logger.info(f"⏳ Comment rate limited — waiting {wait}s")
                time.sleep(wait)
                continue
            if not r.ok:
                logger.warning(f"⚠️ Comment failed ({r.status_code}): {r.text[:120]}")
                continue
            cid = r.json().get("comment_view", {}).get("comment", {}).get("id")
            if cid:
                logger.info(f"💬 Created Lemmy comment ID={cid}")
                return cid
        return None
