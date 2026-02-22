#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
mirror_media.py — shared helper for comment/post media mirroring

Goals:
- Rehost images to Lemmy Pictrs when feasible (stable, cached, throttled)
- Rehost *some* videos (v.redd.it / direct mp4/webm) when feasible
- Treat YouTube/large/long videos as external links (avoid yt-dlp JS runtime issues)
- Persistent cache (DATA_DIR/media_cache.json) to avoid duplicate uploads
- Avoid hammering pictrs / nginx with bridge-side throttling + retries
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import requests

# Pull shared helpers if available; fall back gracefully if not.
try:
    from auto_mirror import is_image_url, guess_imgur_direct, log  # type: ignore
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
    from utils import get_valid_token  # type: ignore
except Exception:
    get_valid_token = None  # type: ignore


LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
LEMMY_UPLOAD_URL = os.getenv("LEMMY_UPLOAD_URL", LEMMY_URL).rstrip("/")

DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Cache format:
#   {
#     "<src_url>": {"mirrored": "<pictrs_or_external>", "ts": <unix>, "status": "ok|fail", "reason": "..."}
#   }
CACHE_PATH = DATA_DIR / "media_cache.json"

# Defaults (override in .env)
MEDIA_CACHE_TTL_SECS = int(os.getenv("MEDIA_CACHE_TTL_SECS", str(14 * 24 * 3600)))  # 14 days
MAX_MEDIA_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(500 * 1024 * 1024)))          # 500 MB
MIN_UPLOAD_INTERVAL_SECS = float(os.getenv("MIN_UPLOAD_INTERVAL_SECS", "0.6"))

UPLOAD_RETRY_MAX = int(os.getenv("UPLOAD_RETRY_MAX", "4"))
UPLOAD_RETRY_BASE_SECS = float(os.getenv("UPLOAD_RETRY_BASE_SECS", "2.0"))

MEDIA_FETCH_TIMEOUT_SECS = float(os.getenv("MEDIA_FETCH_TIMEOUT_SECS", "30"))
MEDIA_UPLOAD_TIMEOUT_SECS = float(os.getenv("MEDIA_UPLOAD_TIMEOUT_SECS", "90"))

# If pictrs keeps failing with ffprobe errors for an item, stop re-trying it forever.
FAIL_PERSIST_SECS = int(os.getenv("MEDIA_FAIL_PERSIST_SECS", str(45 * 24 * 3600)))  # 45 days

# Video policy:
# - EXTERNAL_ONLY: YouTube, rumble, odysee, etc. (no yt-dlp)
# - TRY_REHOST: v.redd.it or direct .mp4/.webm URLs
EXTERNAL_VIDEO_DOMAINS = (
    "youtube.com", "youtu.be", "rumble.com", "odysee.com", "vimeo.com",
    "streamable.com", "twitch.tv", "kick.com"
)

_url_re = re.compile(r"https?://\S+")

_session: Optional[requests.Session] = None
_last_upload_ts = 0.0


@dataclass(frozen=True)
class MirrorResult:
    # If kind == "url", value is a URL to embed (pictrs or original direct)
    # If kind == "md", value is a markdown snippet already (e.g., external link label)
    kind: str
    value: str


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "reddit-lemmy-bridge/1.0"})
        _session = s
    return _session


def find_urls(text: str) -> list[str]:
    if not text:
        return []
    return _url_re.findall(text)


def _looks_like_html(data: bytes) -> bool:
    if not data:
        return True
    head = data.lstrip()[:96].lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<head")
        or head.startswith(b"<body")
    )


def _sniff_media(data: bytes) -> Tuple[Optional[str], Optional[str]]:
    if not data:
        return (None, None)

    # JPEG
    if data.startswith(b"\xff\xd8\xff"):
        return ("image.jpg", "image/jpeg")
    # PNG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ("image.png", "image/png")
    # GIF
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ("image.gif", "image/gif")
    # WEBP
    if data.startswith(b"RIFF") and b"WEBP" in data[8:16]:
        return ("image.webp", "image/webp")
    # MP4-ish (ftyp)
    if len(data) > 12 and data[4:8] == b"ftyp":
        return ("video.mp4", "video/mp4")
    # WebM/Matroska
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ("video.webm", "video/webm")

    return (None, None)


def _jpeg_has_eoi(data: bytes) -> bool:
    # Many truncated JPEGs start OK but are missing the EOI marker (FFD9)
    return len(data) >= 2 and data[-2:] == b"\xff\xd9"


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    tmp = CACHE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(CACHE_PATH)


def _cache_get(url: str) -> Optional[dict]:
    cache = _load_cache()
    ent = cache.get(url)
    if not ent:
        return None
    ts = float(ent.get("ts") or 0)
    status = ent.get("status") or "ok"
    # ok entries expire by TTL; fail entries persist longer to prevent thrash
    if status == "ok":
        if time.time() - ts > MEDIA_CACHE_TTL_SECS:
            return None
    else:
        if time.time() - ts > FAIL_PERSIST_SECS:
            return None
    return ent


def _cache_set_ok(url: str, mirrored: str) -> None:
    cache = _load_cache()
    cache[url] = {"mirrored": mirrored, "ts": time.time(), "status": "ok"}
    _save_cache(cache)


def _cache_set_fail(url: str, reason: str) -> None:
    cache = _load_cache()
    cache[url] = {"mirrored": "", "ts": time.time(), "status": "fail", "reason": reason[:200]}
    _save_cache(cache)


def _get_lemmy_jwt() -> Optional[str]:
    """
    Retrieves a valid Lemmy JWT via get_valid_token() if available,
    else falls back to env vars.
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


def _throttle_upload() -> None:
    global _last_upload_ts
    if MIN_UPLOAD_INTERVAL_SECS <= 0:
        return
    now = time.time()
    wait = (_last_upload_ts + MIN_UPLOAD_INTERVAL_SECS) - now
    if wait > 0:
        time.sleep(wait)
    _last_upload_ts = time.time()


def _fetch_bytes(url: str) -> Optional[bytes]:
    s = _get_session()
    try:
        r = s.get(url, timeout=MEDIA_FETCH_TIMEOUT_SECS, allow_redirects=True)
        r.raise_for_status()
        data = r.content
        if _looks_like_html(data):
            return None
        return data
    except Exception as e:
        log(f"⚠️ Failed to fetch media {url}: {e}")
        return None


def _upload_to_pictrs(filename: str, mimetype: str, data: bytes) -> Optional[str]:
    """
    Uploads bytes to /pictrs/image and returns the public /pictrs/image/<file> URL on success.
    Retries transient errors with backoff, and applies bridge-side throttling.
    """
    jwt = _get_lemmy_jwt()
    headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}

    upload_url = f"{LEMMY_UPLOAD_URL}/pictrs/image"
    s = _get_session()

    # Pictrs deployments differ; this one accepts images[] (as you've seen).
    field = "images[]"

    attempt = 0
    while attempt <= UPLOAD_RETRY_MAX:
        attempt += 1
        _throttle_upload()

        try:
            files = {field: (filename, data, mimetype)}
            res = s.post(upload_url, files=files, headers=headers, timeout=MEDIA_UPLOAD_TIMEOUT_SECS)

            # Success
            if res.ok:
                payload = res.json()
                file_id = None
                if isinstance(payload, dict):
                    arr = payload.get("files") or []
                    if arr and isinstance(arr[0], dict):
                        file_id = arr[0].get("file")
                if file_id:
                    return f"{LEMMY_URL}/pictrs/image/{file_id}"
                # If ok but no file id, treat as failure
                return None

            text = (res.text or "")[:300]

            # Hard failures that are not worth retrying
            if res.status_code == 400:
                # Too many frames => long video; don’t keep hammering
                if "Too many frames" in text or "too many frames" in text:
                    return "__TOO_MANY_FRAMES__"
                # Lemmy/pictrs rate limit
                if "rate_limit_error" in text:
                    # treat as transient; backoff below
                    pass
                # ffprobe failures are often deterministic for the payload => stop after limited retries
                if "ffprobe Failed" in text:
                    # retry a couple times, but don't loop forever
                    if attempt >= 2:
                        return "__FFPROBE_FAILED__"

            # Transient infra issues
            if res.status_code in (429, 500, 502, 503, 504):
                pass
            else:
                # Other status codes: treat as non-transient
                log(f"⚠️ Upload failed {res.status_code}: {text}")
                return None

            # Backoff for retry
            backoff = UPLOAD_RETRY_BASE_SECS * (1.7 ** (attempt - 1))
            backoff = min(backoff, 30.0)
            backoff *= random.uniform(0.85, 1.20)

            if res.status_code == 429:
                ra = res.headers.get("Retry-After")
                if ra:
                    try:
                        backoff = max(backoff, float(ra))
                    except Exception:
                        pass

            log(f"⚠️ {res.status_code} from upload endpoint; retrying in {backoff:.1f}s")
            time.sleep(backoff)

        except Exception as e:
            # Network / timeout errors: retry similarly
            backoff = UPLOAD_RETRY_BASE_SECS * (1.7 ** (attempt - 1))
            backoff = min(backoff, 30.0)
            backoff *= random.uniform(0.85, 1.20)
            log(f"⚠️ Upload exception: {e} — retrying in {backoff:.1f}s")
            time.sleep(backoff)

    return None


def mirror_url(url: str) -> Optional[str]:
    """
    Mirror or label media URLs.

    Returns a *string* for backward compatibility:
      - If rehosted: returns the pictrs URL (https://.../pictrs/image/<id>.<ext>)
      - If external-only video: returns a markdown link string like "[Video](https://...)"
      - If cannot process: returns None

    NOTE:
    - Because your callers currently assume mirror_url() returns a URL,
      external link strings MUST be handled by the caller. See patch note below.
    """
    if not url:
        return None

    # Cache hit?
    ent = _cache_get(url)
    if ent:
        if ent.get("status") == "ok":
            mirrored = ent.get("mirrored") or ""
            if mirrored:
                return mirrored
        else:
            # Permanent-ish fail
            return None

    lower = url.lower()

    # Already local
    if "/pictrs/" in lower:
        _cache_set_ok(url, url)
        return url

    # External-only videos (no yt-dlp)
    if any(dom in lower for dom in EXTERNAL_VIDEO_DOMAINS):
        md = f"[Video]({url})"
        _cache_set_ok(url, md)
        return md

    # Direct videos we will TRY to rehost
    is_direct_video = ("v.redd.it" in lower) or lower.endswith((".mp4", ".webm", ".mov"))
    is_img = is_image_url(url) or bool(guess_imgur_direct(url))

    # Only handle known image/video candidates
    if not (is_direct_video or is_img):
        return None

    # Imgur normalization
    direct = guess_imgur_direct(url)
    if direct:
        url = direct

    data = _fetch_bytes(url)
    if not data:
        _cache_set_fail(url, "fetch_failed_or_html")
        return None

    if len(data) > MAX_MEDIA_BYTES:
        _cache_set_fail(url, f"too_large_{len(data)}")
        return None

    filename, mimetype = _sniff_media(data)
    log(f"DEBUG: sniff => filename={filename} mimetype={mimetype} first12={data[:12]!r}")

    if not filename or not mimetype:
        _cache_set_fail(url, "sniff_failed")
        return None

    # Truncated JPEG guard (prevents many ffprobe-ish downstream issues)
    if mimetype == "image/jpeg" and not _jpeg_has_eoi(data):
        log("⚠️ JPEG missing EOI marker (likely truncated); skipping")
        _cache_set_fail(url, "jpeg_truncated_no_eoi")
        return None

    log(f"DEBUG: Uploading {filename} ({mimetype}) to Pictrs using field 'images[]'...")
    uploaded = _upload_to_pictrs(filename, mimetype, data)

    # Special outcomes
    if uploaded == "__TOO_MANY_FRAMES__":
        # Fall back to external link so update run can proceed
        md = f"[Video]({url})"
        _cache_set_ok(url, md)
        return md

    if uploaded == "__FFPROBE_FAILED__":
        _cache_set_fail(url, "ffprobe_failed")
        return None

    if uploaded:
        log(f"📸 Uploaded → {uploaded}")
        _cache_set_ok(url, uploaded)
        return uploaded

    _cache_set_fail(url, "upload_failed")
    return None