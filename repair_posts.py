#!/usr/bin/env python3
"""
repair_posts.py — fix old mirrored posts that are missing their link or video.

Two kinds of post need it:
  title_only  posts that arrived with no link and no text. Before link posts
              were fixed, an article or other web link was silently dropped.
  video_link  posts whose Reddit video fell back to a "[Video](https://v.redd.it/…)"
              link, usually because it was longer than Lemmy then allowed.

The candidate list comes from Lemmy's own database (see README, "Repairing old
posts"). For each candidate this script looks up the original Reddit post (25
per request), works out what the post should contain now, and edits the Lemmy
post ONLY when that adds something. Genuine title-only posts are left alone.

Run it inside the worker container so it uses the same settings and folders:

    docker exec reddit_mirror_worker python3 repair_posts.py --dry-run
    docker exec -d reddit_mirror_worker python3 repair_posts.py

Safety: the tool never deletes a post or changes a title. Each post is read
from Lemmy just before editing and only added to: a missing link, a hosted
video in place of its fallback link, or text that was never mirrored.
Reddit's "[deleted]"/"[removed]" placeholders are never copied.

Progress is saved to data/repair_progress.json, so it can be stopped and
re-run: finished posts are skipped. Each change is written to logs/bridge.log
and shows on the dashboard's Logs page.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

import auto_mirror as am
import mirror_media as mm

DATA = Path(os.getenv("REPAIR_DATA_DIR", "data"))
DEFAULT_INPUT = DATA / "repair_candidates.csv"
PROGRESS = DATA / "repair_progress.json"
USER_AGENT = os.getenv("REDDIT_USER_AGENT", "RedditToLemmyBridge/1.1 (repair)")
# Reddit's /by_id returns only the first 25 posts of a request unless told
# otherwise, so ask for 25 at a time (and send limit=100 as well, to be safe).
BATCH = 25


def log(msg: str) -> None:
    am.log(f"🛠️ Repair: {msg}")


# ─────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────
def read_candidates(path: Path) -> list[tuple[str, str]]:
    """Rows of 'lemmy_post_id,kind' as exported from Lemmy's database."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[0].strip().isdigit():
                continue
            kind = row[1].strip()
            if kind in ("title_only", "video_link"):
                out.append((row[0].strip(), kind))
    return out


def lemmy_to_reddit_map(db_path: Path) -> dict[str, str]:
    """Lemmy post id -> Reddit post id, from the mirror's own records."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)")}
        rid, lid = ("reddit_id", "lemmy_id") if "reddit_id" in cols else ("reddit_post_id", "lemmy_post_id")
        rows = conn.execute(f"SELECT {rid}, {lid} FROM posts WHERE {lid} IS NOT NULL").fetchall()
    finally:
        conn.close()
    return {str(l).strip(): str(r).strip().removeprefix("t3_") for r, l in rows if r and l}


def load_progress() -> dict:
    try:
        return json.loads(PROGRESS.read_text())
    except Exception:
        return {}


def save_progress(progress: dict) -> None:
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps(progress, indent=1, sort_keys=True))
    tmp.replace(PROGRESS)


# ─────────────────────────────────────────────
# Reddit: 100 posts per request
# ─────────────────────────────────────────────
def reddit_headers() -> dict:
    headers = {"User-Agent": USER_AGENT}
    cid, secret = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if cid and secret:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=requests.auth.HTTPBasicAuth(cid, secret),
            data={"grant_type": "client_credentials"},
            headers=headers, timeout=15,
        )
        if r.ok and r.json().get("access_token"):
            headers["Authorization"] = f"bearer {r.json()['access_token']}"
    return headers


def fetch_reddit_batch(ids: list[str], headers: dict) -> dict[str, dict]:
    host = "https://oauth.reddit.com" if "Authorization" in headers else "https://www.reddit.com"
    url = f"{host}/by_id/{','.join('t3_' + i for i in ids)}.json"
    for attempt in range(5):
        r = requests.get(url, headers=headers, params={"raw_json": 1, "limit": 100}, timeout=30)
        if r.status_code == 429:
            wait = int(float(r.headers.get("Retry-After", 10 * (attempt + 1))))
            log(f"Reddit asked us to slow down; waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        children = r.json().get("data", {}).get("children", [])
        return {c["data"]["id"]: c["data"] for c in children if c.get("data", {}).get("id")}
    raise RuntimeError("Reddit kept rate-limiting the batch request")


# ─────────────────────────────────────────────
# Deciding what a post should contain
# ─────────────────────────────────────────────
def is_reddit_media(url: str) -> bool:
    low = (url or "").lower()
    return any(h in low for h in ("i.redd.it", "v.redd.it", "preview.redd.it", "reddit.com/gallery", "imgur.com"))


def forget_cached_media(url: str) -> None:
    """Drop the media cache's memory of a URL so it's downloaded again."""
    cache = mm._load_cache()
    keys = [k for k in cache if k.rstrip("/") == url.rstrip("/")]
    if keys:
        for k in keys:
            cache.pop(k, None)
        mm._save_cache(cache)


# What Reddit shows in place of text that was deleted or removed after mirroring.
# It must never be copied into a Lemmy post.
TEXT_PLACEHOLDERS = {"[deleted]", "[removed]", "[removed by reddit]", "[ removed by reddit ]"}
VIDEO_FALLBACK = re.compile(r"\[Video\]\((https?://v\.redd\.it/[^)\s]+)\)")


def without_placeholders(sub: dict) -> dict:
    """The Reddit post with '[deleted]'/'[removed]' text treated as no text."""
    text = (sub.get("selftext") or "").strip()
    if text.lower() in TEXT_PLACEHOLDERS:
        sub = dict(sub, selftext="")
    return sub


def get_lemmy_post(post_id: int, jwt: str) -> dict | None:
    """The post as it is on Lemmy right now: name, body and url."""
    headers = {"Authorization": f"Bearer {jwt}"}
    for _ in range(4):
        r = requests.get(f"{am.LEMMY_URL}/api/v3/post", params={"id": post_id}, headers=headers, timeout=30)
        if r.ok:
            post = (r.json().get("post_view") or {}).get("post") or {}
            return {"name": post.get("name") or "", "body": post.get("body") or "", "url": post.get("url")}
        if r.status_code == 400 and "rate_limit" in (r.text or ""):
            time.sleep(30)
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(10)
            continue
        return None
    return None


def swap_video_fallback(body: str, has_url: bool, video_src: str, hosted: str) -> tuple[str, str | None]:
    """
    Replace the '[Video](https://v.redd.it/...)' fallback with the hosted video,
    keeping everything else in the Lemmy post exactly as it is.
    Returns (new_body, new_url): the video becomes the post's link when the post
    has none, otherwise the line points at the hosted copy instead.
    """
    line = f"[Video]({video_src})"
    if not has_url:
        new_body = body.replace(line, "")
        new_body = re.sub(r"\n{3,}", "\n\n", new_body).strip()
        new_body = re.sub(r"(\n\s*---\s*)+$", "", new_body).strip()  # separator left with nothing after it
        return new_body, hosted
    return body.replace(line, f"[Video]({hosted})"), None


def plan(kind: str, sub: dict) -> str | None:
    """
    What a dry run would do, without downloading or uploading anything.
    Returns a short description, or None if there's nothing to gain.
    """
    sub = without_placeholders(sub)
    url = (sub.get("url") or "").replace("&amp;", "&")
    if kind == "video_link":
        return "retry the video under the new length limit"
    if am._external_link(sub, url):
        return f"add link {url[:90]}"
    if sub.get("is_gallery") or is_reddit_media(url):
        return "retry the missing image/video"
    if (sub.get("selftext") or "").strip():
        return "restore the post text"
    return None


def edit_post(post_id: int, title: str, body: str | None, url: str | None, jwt: str) -> tuple[bool, str]:
    """body=None leaves the post's text untouched; a string (even empty) replaces it."""
    payload = {"post_id": post_id, "name": title}
    if body is not None:
        payload["body"] = body
    if url:
        payload["url"] = url
    headers = {"Authorization": f"Bearer {jwt}"}
    for attempt in range(6):
        r = requests.put(f"{am.LEMMY_URL}/api/v3/post", json=payload, headers=headers, timeout=60)
        text = r.text or ""
        if r.ok:
            return True, "edited"
        if r.status_code == 401:
            headers["Authorization"] = f"Bearer {am.lemmy_login(force=True)}"
            continue
        if r.status_code == 400 and "rate_limit" in text:
            time.sleep(30)
            continue
        if r.status_code == 400 and payload.get("url") and "/pictrs/" not in payload["url"] \
                and any(k in text for k in ("url", "blocked")):
            link = payload.pop("url")
            payload["body"] = (f"🔗 <{link}>\n\n" + (payload.get("body") or "")).strip()
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(10)
            continue
        return False, f"Lemmy answered {r.status_code}: {text[:120]}"
    return False, "gave up after repeated rate limits/errors"


def repair_one(post_id: int, kind: str, sub: dict, action: str, jwt: str) -> str:
    """
    Fix one post. Starts from the post as it is on Lemmy now, so nothing that's
    there is lost: the title is never changed, and text is never replaced.
    """
    current = get_lemmy_post(post_id, jwt)
    if current is None:
        return "failed: couldn't read the post from Lemmy"

    if kind == "video_link":
        sources = VIDEO_FALLBACK.findall(current["body"])
        if not sources:
            return "left alone: the post has changed since the list was made"
        body, url, stored = current["body"], current["url"], 0
        for src in dict.fromkeys(sources):
            forget_cached_media(src)
            hosted = mm.mirror_url(src)
            if hosted and not hosted.startswith("[") and "/pictrs/" in hosted:
                body, new_url = swap_video_fallback(body, bool(url), src, hosted)
                url = url or new_url
                stored += 1
        if not stored:
            return "left alone: video still couldn't be stored (too long or download failed)"
        new_url = url if url != current["url"] else None
        ok, detail = edit_post(post_id, current["name"], body, new_url, jwt)
        return f"fixed: {action}" if ok else f"failed: {detail}"

    # title_only: Lemmy has no link and no text, so anything recovered is new
    if current["body"].strip() or current["url"]:
        return "left alone: the post has changed since the list was made"
    if is_reddit_media(sub.get("url", "")):
        forget_cached_media((sub.get("url") or "").replace("&amp;", "&"))
    body, url = am.build_media_block_from_submission(sub)
    if not (body or "").strip() and not url:
        return "left alone: nothing could be recovered"
    if not url and not VIDEO_FALLBACK.sub("", body or "").strip(" \n-"):
        # All we'd add is a link back to the Reddit video: not a repair
        return "left alone: video couldn't be downloaded"
    ok, detail = edit_post(post_id, current["name"], body if (body or "").strip() else None, url, jwt)
    return f"fixed: {action}" if ok else f"failed: {detail}"


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Fix old mirrored posts missing their link or video.")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="candidate CSV (lemmy_post_id,kind)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; edit nothing")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many edits (0 = no limit)")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds to wait after each edit")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"Candidate list not found: {args.input}. See README, 'Repairing old posts'.")
        return 1

    candidates = read_candidates(args.input)
    mapping = lemmy_to_reddit_map(DATA / "jobs.db")
    progress = {} if args.dry_run else load_progress()
    todo = [(lid, kind) for lid, kind in candidates if lid not in progress]
    say = print if args.dry_run else log
    say(f"{len(candidates)} candidates, {len(candidates) - len(todo)} already done, "
        f"{len(todo)} to check{' (dry run: nothing will be edited)' if args.dry_run else ''}")

    counts: dict[str, int] = {}
    def tally(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    jwt = None if args.dry_run else (am.get_cached_jwt() or am.lemmy_login(force=True))
    headers = reddit_headers()
    edits = 0

    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        reddit_ids = [mapping[lid] for lid, _ in chunk if lid in mapping]
        subs = fetch_reddit_batch(reddit_ids, headers) if reddit_ids else {}
        # Don't call a post deleted just because a batch left it out: ask again alone
        for rid in reddit_ids:
            if rid not in subs:
                subs.update(fetch_reddit_batch([rid], headers))
                time.sleep(1)

        for lid, kind in chunk:
            rid = mapping.get(lid)
            sub = subs.get(rid) if rid else None
            if not rid:
                outcome = "skipped: not in the mirror's records"
            elif not sub:
                outcome = "skipped: original is gone from Reddit"
            else:
                action = plan(kind, sub)
                if action is None:
                    outcome = "left alone: genuinely a title-only post"
                elif args.dry_run:
                    outcome = f"would {action}"
                else:
                    outcome = repair_one(int(lid), kind, without_placeholders(sub), action, jwt)
                    if outcome.startswith("fixed"):
                        edits += 1
                        time.sleep(args.delay)

            tally("would change" if outcome.startswith("would") else
                  outcome if outcome.startswith("skipped") else outcome.split(":")[0])
            line = f"post {lid} (reddit {rid or '?'}): {outcome}"
            if args.dry_run:
                if outcome.startswith("would"):
                    print(line, flush=True)  # console only: a dry run shouldn't fill bridge.log
            elif not outcome.startswith("left alone"):
                log(line)
            if not args.dry_run:
                progress[lid] = outcome
                save_progress(progress)
            if args.limit and edits >= args.limit:
                log(f"Stopping after {edits} edits (--limit). Re-run to continue.")
                log(f"Summary: {counts}")
                return 0

    say(f"Done. Summary: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
