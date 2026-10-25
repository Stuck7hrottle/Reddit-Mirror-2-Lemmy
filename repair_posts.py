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

Progress is saved to data/repair_progress.json, so it can be stopped and
re-run: finished posts are skipped. Each change is written to logs/bridge.log
and shows on the dashboard's Logs page.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
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


def plan(kind: str, sub: dict) -> str | None:
    """
    What a dry run would do, without downloading or uploading anything.
    Returns a short description, or None if there's nothing to gain.
    """
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


def edit_post(post_id: int, title: str, body: str, url: str | None, jwt: str) -> tuple[bool, str]:
    payload = {"post_id": post_id, "name": title, "body": body}
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
                    if kind == "video_link" or is_reddit_media(sub.get("url", "")):
                        forget_cached_media((sub.get("url") or "").replace("&amp;", "&"))
                    body, url = am.build_media_block_from_submission(sub)
                    still_link = "[Video](https://v.redd.it/" in (body or "")
                    if not body and not url:
                        outcome = "left alone: nothing could be recovered"
                    elif kind == "video_link" and still_link and not url:
                        outcome = "left alone: video still couldn't be stored (too long or download failed)"
                    else:
                        title = am._sanitize_title(sub.get("title") or "", sub.get("subreddit") or "mirror")
                        ok, detail = edit_post(int(lid), title, body or "", url, jwt)
                        outcome = f"fixed: {action}" if ok else f"failed: {detail}"
                        if ok:
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
