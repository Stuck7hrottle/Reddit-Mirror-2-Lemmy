#!/usr/bin/env python3
"""
mirror_media.py — shared helper for comment/post media mirroring
──────────────────────────────────────────────────────────────────
- Rehosts images/videos to Lemmy /pictrs via Lemmy's /pictrs/image endpoint
- Downloads v.redd.it + direct .mp4/.webm when configured, then uploads to pictrs
- Persistent caching in DATA_DIR/media_cache.json to avoid duplicate uploads
- Production hardening: throttling, retries/backoff (rate_limit_error + 502/503/504),
  long upload timeouts, streaming uploads for large videos, bounded image downloads
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

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
    from utils import get_valid_token  # expected signature: get_valid_token(username=..., password=...)
except Exception:
    get_valid_token = None


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
UPLOAD_BASE = os.getenv("LEMMY_UPLOAD_URL", LEMMY_URL).rstrip("/")
UPLOAD_URL = f"{UPLOAD_BASE}/pictrs/image"

DATA_DIR = Path(os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CACHE_PATH = DATA_DIR / "media_cache.json"

# Default 14 days (comment + behavior consistent)
CACHE_TTL_SECS = int(os.getenv("MEDIA_CACHE_TTL_SECS", str(14 * 24 * 3600)))

# 500 MB default
MAX_MEDIA_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(500 * 1024 * 1024)))

# Upload pacing:
# With Lemmy image:120/min, sustained rate is 2/sec -> 0.5s interval is appropriate.
MIN_UPLOAD_INTERVAL_SECS = float(os.getenv("MIN_UPLOAD_INTERVAL_SECS", "0.5"))
UPLOAD_JITTER_SECS = float(os.getenv("UPLOAD_JITTER_SECS", "0.35"))

# Retry/backoff
UPLOAD_MAX_ATTEMPTS = int(os.getenv("UPLOAD_MAX_ATTEMPTS", "8"))
UPLOAD_BACKOFF_BASE = float(os.getenv("UPLOAD_BACKOFF_BASE", "2.0"))
UPLOAD_BACKOFF_MAX = float(os.getenv("UPLOAD_BACKOFF_MAX", "60.0"))

# HTTP timeouts
FETCH_TIMEOUT_SECS = float(os.getenv("FETCH_TIMEOUT_SECS", "20"))
UPLOAD_CONNECT_TIMEOUT = float(os.getenv("UPLOAD_CONNECT_TIMEOUT", "10"))
UPLOAD_READ_TIMEOUT = float(os.getenv("UPLOAD_READ_TIMEOUT", "1200"))  # 20 min

# Video download behavior
YT_DLP_TIMEOUT_SECS = int(os.getenv("YT_DLP_TIMEOUT_SECS", "180"))
YT_DLP_ALLOW_AUDIO = os.getenv("YT_DLP_ALLOW_AUDIO", "true").lower() in ("1", "true", "yes", "y")

VIDEO_DOMAINS = (
    "youtube.com", "youtu.be", "rumble.com", "odysee.com",
    "vimeo.com", "streamable.com",
)

_url_re = re.compile(r"https?://\S+")

# Reuse connections for large batch runs
_session = requests.Session()

# Simple in-process throttle (works for single process; multiple procs should also pace via Lemmy limits)
_last_upload_ts = 0.0

# Cache in-memory (loaded lazily once)
_cache_loaded = False
_cache: dict[str, dict] = {}


# ─────────────────────────────────────────────
# Utility / sniffing
# ─────────────────────────────────────────────
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
    """Best-effort type detection from bytes."""
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


def _sniff_file(path: str) -> Tuple[Optional[str], Optional[str], bytes]:
    """Read a tiny header and sniff media. Returns (filename, mimetype, header_bytes)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except Exception:
        return (None, None, b"")
    filename, mimetype = _sniff_media(head)
    return (filename, mimetype, head)


def _is_video_url(url_lower: str) -> bool:
    return (
        any(d in url_lower for d in VIDEO_DOMAINS)
        or "v.redd.it" in url_lower
        or url_lower.endswith((".mp4", ".webm", ".mov"))
    )


# ─────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────
def _load_cache_once() -> None:
    global _cache_loaded, _cache
    if _cache_loaded:
        return
    _cache_loaded = True
    if not CACHE_PATH.exists():
        _cache = {}
        return
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            _cache = data if isinstance(data, dict) else {}
    except Exception:
        _cache = {}


def _save_cache() -> None:
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(CACHE_PATH)
    except Exception as e:
        log(f"⚠️ Failed to write media cache: {e}")


def _cache_get(url: str) -> Optional[str]:
    _load_cache_once()
    entry = _cache.get(url)
    if not isinstance(entry, dict):
        return None
    mirrored = entry.get("mirrored")
    ts = entry.get("ts")
    if not mirrored:
        return None
    if isinstance(ts, (int, float)) and CACHE_TTL_SECS > 0:
        if (time.time() - float(ts)) > CACHE_TTL_SECS:
            return None
    return str(mirrored)


def _cache_set(url: str, mirrored: str) -> None:
    _load_cache_once()
    _cache[url] = {"mirrored": mirrored, "ts": time.time()}
    _save_cache()


# ─────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────
def find_urls(text: str) -> list[str]:
    if not text:
        return []
    return _url_re.findall(text)


def _get_lemmy_jwt() -> Optional[str]:
    """
    Retrieves a valid Lemmy JWT via get_valid_token() if present,
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
            log(f"⚠️ get_valid_token() failed: {e}")

    if not jwt:
        jwt = os.getenv("LEMMY_AUTH") or os.getenv("LEMMY_API_TOKEN")

    return jwt


# ─────────────────────────────────────────────
# Download helpers
# ─────────────────────────────────────────────
def _download_to_bytes(url: str) -> Optional[bytes]:
    """
    Bounded download into memory (for images/small media).
    Aborts if size exceeds MAX_MEDIA_BYTES.
    """
    try:
        with _session.get(url, stream=True, timeout=FETCH_TIMEOUT_SECS) as r:
            r.raise_for_status()

            chunks: list[bytes] = []
            total = 0
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    log(f"⚠️ Download exceeded MAX_MEDIA_BYTES ({MAX_MEDIA_BYTES}); aborting: {url}")
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
            return data
    except Exception as e:
        log(f"⚠️ Failed to fetch {url}: {e}")
        return None


def download_video(url: str) -> Optional[str]:
    """
    Downloads a video to DATA_DIR and returns the local path.
    Uses yt-dlp. Enforces MAX_MEDIA_BYTES via --max-filesize.
    """
    output_template = str(DATA_DIR / "temp_video_%(id)s.%(ext)s")

    # If audio is disallowed in your pictrs config, set YT_DLP_ALLOW_AUDIO=false
    fmt = "bv*+ba/b" if YT_DLP_ALLOW_AUDIO else "bv*/b"

    cmd = [
        "yt-dlp",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--max-filesize", str(MAX_MEDIA_BYTES),
        "-o", output_template,
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=YT_DLP_TIMEOUT_SECS,
        )
        if result.returncode != 0:
            log(f"⚠️ yt-dlp failed ({result.returncode}) for {url}: {result.stderr.strip()[:200]}")
            return None

        # Pick the newest matching temp_video_* file
        candidates = sorted(DATA_DIR.glob("temp_video_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            log(f"⚠️ yt-dlp reported success but no temp_video_* file found for {url}")
            return None
        return str(candidates[0])

    except Exception as e:
        log(f"⚠️ Video download failed: {e}")
        return None


# ─────────────────────────────────────────────
# Upload helpers
# ─────────────────────────────────────────────
def _throttle_upload() -> None:
    global _last_upload_ts
    if MIN_UPLOAD_INTERVAL_SECS <= 0:
        return
    now = time.time()
    wait = (_last_upload_ts + MIN_UPLOAD_INTERVAL_SECS) - now
    if wait > 0:
        time.sleep(wait + random.uniform(0.0, max(0.0, UPLOAD_JITTER_SECS)))
    _last_upload_ts = time.time()


def _is_rate_limited(res: requests.Response) -> bool:
    # Lemmy typically returns {"error":"rate_limit_error"}
    try:
        j = res.json()
        return isinstance(j, dict) and j.get("error") == "rate_limit_error"
    except Exception:
        return False


def _retry_after_seconds(res: requests.Response) -> Optional[float]:
    ra = res.headers.get("Retry-After")
    if not ra:
        return None
    try:
        return float(ra)
    except Exception:
        return None


def _post_with_resilience(*, files, headers) -> requests.Response:
    """
    POST to Lemmy /pictrs/image with:
      - throttle
      - retries on rate_limit_error and transient upstream issues
      - long timeouts
    """
    last_res: Optional[requests.Response] = None

    for attempt in range(1, UPLOAD_MAX_ATTEMPTS + 1):
        _throttle_upload()

        try:
            res = _session.post(
                UPLOAD_URL,
                files=files,
                headers=headers,
                timeout=(UPLOAD_CONNECT_TIMEOUT, UPLOAD_READ_TIMEOUT),
            )
            last_res = res
        except requests.RequestException as e:
            sleep_s = min(UPLOAD_BACKOFF_MAX, UPLOAD_BACKOFF_BASE * (2 ** (attempt - 1)))
            sleep_s += random.uniform(0.0, 0.8)
            log(f"⚠️ Upload network error ({e}); retrying in {sleep_s:.1f}s ({attempt}/{UPLOAD_MAX_ATTEMPTS})")
            time.sleep(sleep_s)
            continue

        if res.ok:
            return res

        # Rate limit
        if _is_rate_limited(res):
            ra = _retry_after_seconds(res)
            sleep_s = ra if ra is not None else min(UPLOAD_BACKOFF_MAX, UPLOAD_BACKOFF_BASE * (2 ** (attempt - 1)))
            sleep_s += random.uniform(0.0, 0.8)
            log(f"⏳ rate_limited; sleeping {sleep_s:.1f}s ({attempt}/{UPLOAD_MAX_ATTEMPTS})")
            time.sleep(sleep_s)
            continue

        # Transient upstream (nginx/pictrs under load)
        if res.status_code in (502, 503, 504):
            sleep_s = min(UPLOAD_BACKOFF_MAX, UPLOAD_BACKOFF_BASE * (2 ** (attempt - 1)))
            sleep_s += random.uniform(0.0, 0.8)
            log(f"⚠️ Upstream {res.status_code}; retrying in {sleep_s:.1f}s ({attempt}/{UPLOAD_MAX_ATTEMPTS})")
            time.sleep(sleep_s)
            continue

        # Sometimes pictrs/Lemmy returns 500 transiently under load; retry a couple times
        if res.status_code == 500 and attempt <= 2:
            sleep_s = 5.0 + random.uniform(0.0, 1.0)
            log(f"⚠️ 500 from upload endpoint; retrying once in {sleep_s:.1f}s")
            time.sleep(sleep_s)
            continue

        # Non-retriable: return immediately
        return res

    # Out of attempts
    return last_res if last_res is not None else requests.Response()


def _parse_pictrs_file_id(res: requests.Response) -> Optional[str]:
    try:
        payload = res.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    files_arr = payload.get("files") or []
    if files_arr and isinstance(files_arr, list) and isinstance(files_arr[0], dict):
        file_id = files_arr[0].get("file")
        return str(file_id) if file_id else None
    return None


# ─────────────────────────────────────────────
# Main API
# ─────────────────────────────────────────────
def mirror_url(url: str) -> Optional[str]:
    """
    Mirrors media URLs.
    Returns:
      - Lemmy /pictrs/image/<id> URL for images/videos (rehosted)
      - None if unsupported or failed (caller decides how to handle)
    """
    if not url:
        return None

    url = url.strip()
    lower = url.lower()

    # Cached already?
    cached = _cache_get(url)
    if cached:
        return cached

    # Already local?
    if "/pictrs/" in lower:
        _cache_set(url, url)
        return url

    is_video = _is_video_url(lower)

    # ── Videos: download to disk then stream upload
    if is_video:
        log(f"DEBUG: Attempting to download video from {url}")
        local_path = download_video(url)
        if not local_path:
            log(f"DEBUG: Video download failed or exceeded size limit for {url}")
            return None  # local hosting only
        try:
            file_size = os.path.getsize(local_path)
            if file_size > MAX_MEDIA_BYTES:
                log(f"⚠️ Skipping large video ({file_size/1024/1024:.1f} MB): {url}")
                return None

            filename, mimetype, head = _sniff_file(local_path)
            log(f"DEBUG: sniff => filename={filename} mimetype={mimetype} first12={head[:12]!r}")
            if not filename:
                log(f"⚠️ Unknown/invalid video bytes from {url}; first32={head[:32]!r}")
                return None

            jwt = _get_lemmy_jwt()
            headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}

            # Only supported field for Lemmy -> pictrs
            field = "images[]"

            with open(local_path, "rb") as fh:
                files = {field: (filename, fh, mimetype)}
                log(f"DEBUG: Uploading {filename} ({mimetype}) to Pictrs using field '{field}'...")
                res = _post_with_resilience(files=files, headers=headers)

            if not res or not res.ok:
                log(f"⚠️ Upload failed {getattr(res, 'status_code', '?')}: {(getattr(res, 'text', '') or '')[:150]}")
                return None

            file_id = _parse_pictrs_file_id(res)
            if not file_id:
                log("⚠️ Upload succeeded but could not parse file id from response.")
                return None

            mirrored = f"{LEMMY_URL}/pictrs/image/{file_id}"
            _cache_set(url, mirrored)
            log(f"📸 Uploaded → {mirrored}")
            return mirrored

        finally:
            try:
                if local_path and os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                pass

    # ── Images: download bounded into memory then upload
    # Only allow images (or imgur pages convertible to direct)
    if not is_image_url(url) and not guess_imgur_direct(url):
        return None

    direct = guess_imgur_direct(url)
    if direct:
        url = direct

    data = _download_to_bytes(url)
    if not data:
        return None

    if _looks_like_html(data):
        log(f"⚠️ Fetched HTML/non-media from {url}; skipping upload")
        return None

    if len(data) > MAX_MEDIA_BYTES:
        log(f"⚠️ Skipping large file ({len(data)/1024/1024:.1f} MB): {url}")
        return None

    filename, mimetype = _sniff_media(data)
    log(f"DEBUG: sniff => filename={filename} mimetype={mimetype} first12={data[:12]!r}")
    if not filename:
        log(f"⚠️ Unknown/invalid media bytes from {url}; first32={data[:32]!r}")
        return None

    # Basic corruption guards (helps reduce ffprobe/processing errors)
    if len(data) < 2048:
        log(f"⚠️ Media too small ({len(data)} bytes); skipping: {url}")
        return None
    if filename == "image.jpg" and not data.rstrip().endswith(b"\xff\xd9"):
        log("⚠️ JPEG missing EOI marker (likely truncated); skipping")
        return None

    jwt = _get_lemmy_jwt()
    headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}

    field = "images[]"
    files = {field: (filename, data, mimetype)}
    log(f"DEBUG: Uploading {filename} ({mimetype}) to Pictrs using field '{field}'...")
    res = _post_with_resilience(files=files, headers=headers)

    if not res or not res.ok:
        log(f"⚠️ Upload failed {getattr(res, 'status_code', '?')}: {(getattr(res, 'text', '') or '')[:150]}")
        return None

    file_id = _parse_pictrs_file_id(res)
    if not file_id:
        log("⚠️ Upload succeeded but could not parse file id from response.")
        return None

    mirrored = f"{LEMMY_URL}/pictrs/image/{file_id}"
    _cache_set(url, mirrored)
    log(f"📸 Uploaded → {mirrored}")
    return mirrored