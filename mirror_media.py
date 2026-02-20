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
import subprocess

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
CACHE_TTL_SECS = int(os.getenv("MEDIA_CACHE_TTL_SECS", str(99 * 365 * 3600)))  # 14 days
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(500 * 1024 * 1024)))    # 10 MB limit

VIDEO_DOMAINS = (
    "youtube.com", "youtu.be", "rumble.com", "odysee.com",
    "vimeo.com", "streamable.com"
)

_url_re = re.compile(r"https?://\S+")

def _looks_like_html(data: bytes) -> bool:
    if not data:
        return True
    head = data.lstrip()[:64].lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<head")
        or head.startswith(b"<body")
    )

def _sniff_media(data: bytes) -> tuple[str | None, str | None]:
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
    # MP4-ish
    if len(data) > 12 and data[4:8] == b"ftyp":
        return ("video.mp4", "video/mp4")
    # WebM/Matroska
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ("video.webm", "video/webm")

    return (None, None)

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

def download_video(url: str) -> str | None:
    """Downloads a video to DATA_DIR and returns the local path."""
    output_template = str(DATA_DIR / "temp_video_%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bv*+ba/b",             # Merges the best video and best audio streams
        "--merge-output-format", "mp4", # Ensures the output is an MP4
        "--max-filesize", str(MAX_IMAGE_BYTES),
        "-o", output_template,
        url
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            # Logic to find the downloaded filename
            for f in DATA_DIR.glob("temp_video_*"):
                return str(f)
    except Exception as e:
        log(f"⚠️ Video download failed: {e}")
    return None

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

    # Handle Videos by Downloading
    is_video = any(d in lower for d in VIDEO_DOMAINS) or "v.redd.it" in lower or lower.endswith((".mp4", ".webm"))

    if is_video:
        log(f"DEBUG: Attempting to download video from {url}")
        local_path = download_video(url)
        if local_path:
            log(f"DEBUG: Video downloaded successfully to {local_path}")
            with open(local_path, "rb") as f:
                data = f.read()
            os.remove(local_path) # Clean up temp file
            # If a video was downloaded, we set 'data' and fall through to the upload logic
        else:
            log(f"DEBUG: Video download failed or exceeded size limit for {url}")
            return None # Don't return a link; we want local hosting only
    else:
        # If not a video, handle as image
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
        except Exception as e:
            log(f"⚠️ Failed to fetch image {url}: {e}")
            return None
            
        if _looks_like_html(data):
            log(f"⚠️ Fetched HTML/non-media from {url}; skipping upload")
            return None

# Common Upload Logic (for both downloaded videos and images)
    try:
        if len(data) > MAX_IMAGE_BYTES:
            log(f"⚠️ Skipping large file ({len(data)/1024/1024:.1f} MB): {url}")
            return None

        jwt = _get_lemmy_jwt()
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}
        upload_base = os.getenv("LEMMY_UPLOAD_URL", LEMMY_URL).rstrip("/")
        upload_url = f"{upload_base}/pictrs/image"

        # Determine correct filename and MIME type from the actual bytes
        filename, mimetype = _sniff_media(data)
        if not filename:
            log(f"⚠️ Unknown/invalid media bytes from {url}; first32={data[:32]!r}")
            return None

        # Prefer images[] first, then fallback to images
        files = {"images[]": (filename, data, mimetype)}

        log(f"DEBUG: Uploading {filename} ({mimetype}) to Pictrs...")
        res = requests.post(upload_url, files=files, headers=headers, timeout=60)

        if not res.ok:
            log("DEBUG: 'images[]' field failed, trying 'images' fallback...")
            files = {"images": (filename, data, mimetype)}
            res = requests.post(upload_url, files=files, headers=headers, timeout=60)

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
        log(f"⚠️ mirror_url upload error for {url}: {e}")

    return None