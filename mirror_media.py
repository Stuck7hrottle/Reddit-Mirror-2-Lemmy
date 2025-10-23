#!/usr/bin/env python3
"""
mirror_media.py — shared helper for comment/post media mirroring
──────────────────────────────────────────────────────────────────
- Rehosts images to Lemmy /pictrs with dynamic JWT authentication
- Leaves videos as labeled outbound links (YouTube, v.redd.it, .mp4/.webm)
- Persistent caching in DATA_DIR/media_cache.json to avoid duplicate uploads
"""

import os
import re
import json
import time
from pathlib import Path
import requests

# Pull shared helpers if available; fall back gracefully if not.
try:
    from auto_mirror import is_image_url, guess_imgur_direct, log
except Exception:
    def is_image_url(u: str) -> bool:
        return bool(re.search(r"\.(png|jpe?g|gif|webp)(\?.*)?$", u or "", re.I))
    def guess_imgur_direct(u: str):
        m = re.match(r"https?://(www\.)?imgur\.com/([A-Za-z0-9]+)$", u or "", re.I)
        return f"https://i.imgur.com/{m.group(2)}.jpg" if m else None
    def log(msg: str):
        print(msg, flush=True)

# Try to import Lemmy token logic
try:
    from utils import get_valid_token
except Exception:
    get_valid_token = None

LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CACHE_PATH = DATA_DIR / "media_cache.json"
CACHE_TTL_SECS = int(os.getenv("MEDIA_CACHE_TTL_SECS", str(14 * 24 * 3600)))  # 14 days
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))    # 10 MB limit

VIDEO_DOMAINS = (
    "youtube.com", "youtu.be", "rumble.com", "odysee.com",
    "vimeo.com", "streamable.com"
)

_url_re = re.compile(r"https?://\S+")


# ───────────────────────────────
# Cache helpers
# ───────────────────────────────
def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cache(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(CACHE_PATH)

def _cache_get(url: str) -> str | None:
    cache = _load_cache()
    entry = cache.get(url)
    if not entry:
        return None
    if (time.time() - entry.get("ts", 0)) > CACHE_TTL_SECS:
        cache.pop(url, None)
        _save_cache(cache)
        return None
    return entry.get("mirrored")

def _cache_set(url: str, mirrored: str) -> None:
    cache = _load_cache()
    cache[url] = {"mirrored": mirrored, "ts": time.time()}
    _save_cache(cache)


# ───────────────────────────────
# Public helpers
# ───────────────────────────────
def find_urls(text: str) -> list[str]:
    if not text:
        return []
    return _url_re.findall(text)


def _get_lemmy_jwt() -> str | None:
    """
    Dynamically retrieves a valid Lemmy JWT via get_valid_token(),
    falling back to environment variables if needed.
    """
    jwt = None
    if get_valid_token:
        try:
            jwt = get_valid_token(
                username=os.getenv("LEMMY_COMMENT_USER", os.getenv("LEMMY_USER")),
                password=os.getenv("LEMMY_COMMENT_PASS", os.getenv("LEMMY_PASS")),
            )
        except Exception as e:
            log(f"⚠️ Failed to get_valid_token(): {e}")

    if not jwt:
        jwt = os.getenv("LEMMY_AUTH") or os.getenv("LEMMY_API_TOKEN")

    return jwt


def mirror_url(url: str) -> str | None:
    """
    Mirrors or safely labels media URLs.
    Returns:
      - /pictrs/ URL for images (rehosted)
      - Markdown link label for videos
      - None if unsupported
    """
    if not url:
        return None

    lower = url.lower()

    # Cached already?
    cached = _cache_get(url)
    if cached:
        return cached

    # Handle common video cases
    if any(d in lower for d in VIDEO_DOMAINS):
        labeled = f"[🎬 Watch video]({url})"
        _cache_set(url, labeled)
        return labeled

    if "v.redd.it" in lower:
        labeled = f"[🎥 View Reddit video]({url})"
        _cache_set(url, labeled)
        return labeled

    if lower.endswith((".mp4", ".webm", ".mov")):
        labeled = f"[🎬 Direct video link]({url})"
        _cache_set(url, labeled)
        return labeled

    # If already Lemmy-hosted
    if "/pictrs/" in url:
        _cache_set(url, url)
        return url

    # Not an image
    if not is_image_url(url) and not guess_imgur_direct(url):
        return None

    # Convert Imgur page to direct image
    direct = guess_imgur_direct(url)
    if direct:
        url = direct

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.content

        if len(data) > MAX_IMAGE_BYTES:
            log(f"⚠️ Skipping large image ({len(data)/1024/1024:.1f} MB): {url}")
            return None

        jwt = _get_lemmy_jwt()
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}

        upload_url = f"{LEMMY_URL}/pictrs/image"

        # Try all known compatible field names for Lemmy pictrs
        res = None
        field_names = ("images[]", "images", "file", "image")
        for key in field_names:
            files = {key: ("upload.jpg", data, "image/jpeg")}
            res = requests.post(upload_url, files=files, headers=headers, timeout=30)
            if res.ok:
                break
            else:
                log(f"⚠️ Upload attempt failed with field '{key}' → {res.status_code}: {res.text[:80]}")

        if not res or not res.ok:
            log(f"⚠️ Upload failed {getattr(res, 'status_code', '?')}: {getattr(res, 'text', '')[:150]}")
            return None

        payload = res.json()
        file_id = None
        if isinstance(payload, dict):
            files_arr = payload.get("files") or []
            if files_arr and isinstance(files_arr[0], dict):
                file_id = files_arr[0].get("file")

        if file_id:
            mirrored = f"{LEMMY_URL}/pictrs/image/{file_id}"
            _cache_set(url, mirrored)
            log(f"📸 Uploaded → {mirrored}")
            return mirrored

    except Exception as e:
        log(f"⚠️ mirror_url error for {url}: {e}")

    return None