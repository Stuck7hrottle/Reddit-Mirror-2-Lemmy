#!/usr/bin/env python3
"""
odysee_mirror.py — mirror Odysee channels into Lemmy communities.

Each upload becomes a Lemmy link post: the upload's title, its Odysee page as
the link (Lemmy shows the thumbnail and preview), the creator's description,
and a credit line. Videos and files are linked, not copied.

Settings (.env):
  ODYSEE_MAP              @channel:id=community pairs, comma-separated, e.g.
                          @hoffmantactical:3=hoffman_odysee
  ODYSEE_BACKFILL         true: the first time a channel is seen, mirror its
                          whole history, oldest first. false: only uploads
                          from now on. (default true)
  ODYSEE_INTERVAL_SECS    how often to check for new uploads (default 3600)
  ODYSEE_POST_DELAY_SECS  pause between posts (default 10)

Usage:
  python3 odysee_mirror.py --dry-run --once   show what would be posted
  python3 odysee_mirror.py --once --limit 5   post up to 5, then stop
  python3 odysee_mirror.py                   run continuously

Communities must exist on Lemmy first; create them in Lemmy's UI so you can
set their description and moderators.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import auto_mirror as am
from core.models import SourcePost
from sources.odysee import OdyseeSource

DB_PATH = Path(os.getenv("ODYSEE_DB", "data/jobs.db"))
BODY_LIMIT = 9000  # characters of description to copy


def log(msg: str) -> None:
    am.log(f"📺 Odysee: {msg}")


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "y", "on")


def parse_map(raw: str) -> list[tuple[str, str]]:
    """'@a:1=comm_a, @b:2=comm_b' -> [('@a:1', 'comm_a'), ('@b:2', 'comm_b')]"""
    pairs = []
    for part in (raw or "").split(","):
        if "=" not in part:
            continue
        channel, community = (s.strip() for s in part.split("=", 1))
        if channel and community:
            pairs.append((channel, community.lower()))
    return pairs


# ─────────────────────────────────────────────
# What's been mirrored (kept apart from the Reddit records)
# ─────────────────────────────────────────────
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_posts (
            source     TEXT NOT NULL,
            source_id  TEXT NOT NULL,
            channel    TEXT,
            community  TEXT,
            lemmy_id   TEXT,
            status     TEXT,          -- posted | skipped
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, source_id)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_channels (
            source        TEXT NOT NULL,
            channel       TEXT NOT NULL,
            history_done  INTEGER DEFAULT 0,
            PRIMARY KEY (source, channel)
        )""")
    conn.commit()
    return conn


def known_ids(conn, channel: str) -> set[str]:
    rows = conn.execute("SELECT source_id FROM source_posts WHERE source='odysee' AND channel=?", (channel,))
    return {r[0] for r in rows}


def history_done(conn, channel: str) -> bool:
    row = conn.execute("SELECT history_done FROM source_channels WHERE source='odysee' AND channel=?", (channel,)).fetchone()
    return bool(row and row[0])


def mark_history_done(conn, channel: str) -> None:
    conn.execute("INSERT INTO source_channels (source, channel, history_done) VALUES ('odysee', ?, 1) "
                 "ON CONFLICT(source, channel) DO UPDATE SET history_done=1", (channel,))
    conn.commit()


def record(conn, post: SourcePost, channel: str, community: str, lemmy_id, status: str) -> None:
    conn.execute("INSERT OR REPLACE INTO source_posts (source, source_id, channel, community, lemmy_id, status) "
                 "VALUES ('odysee', ?, ?, ?, ?, ?)", (post.id, channel, community, str(lemmy_id) if lemmy_id else None, status))
    conn.commit()


# ─────────────────────────────────────────────
# Building and sending a post
# ─────────────────────────────────────────────
def build_body(post: SourcePost) -> str:
    desc = post.body
    if len(desc) > BODY_LIMIT:
        desc = desc[:BODY_LIMIT].rsplit("\n", 1)[0] + "\n\n…"
    when = datetime.fromtimestamp(post.created_utc, tz=timezone.utc).strftime("%-d %b %Y") if post.created_utc else None
    kind = "Video" if post.metadata.get("stream_type") == "video" else "Upload"
    who = f"[{post.author}]({post.metadata['channel_url']})" if post.metadata.get("channel_url") else post.author
    credit = f"*{kind} by {who} on Odysee" + (f", released {when}" if when else "") + f". [Open on Odysee]({post.url})*"
    return f"{desc}\n\n---\n{credit}" if desc else credit


def create_post(payload: dict, jwt: str) -> tuple[int | None, str]:
    headers = {"Authorization": f"Bearer {jwt}"}
    for attempt in range(8):
        r = requests.post(f"{am.LEMMY_URL}/api/v3/post", json=payload, headers=headers, timeout=60)
        text = r.text or ""
        if r.ok:
            return (r.json().get("post_view") or {}).get("post", {}).get("id"), "ok"
        if r.status_code == 401:
            headers["Authorization"] = f"Bearer {am.lemmy_login(force=True)}"
            continue
        if r.status_code == 400 and "rate_limit" in text:
            time.sleep(30)
            continue
        if r.status_code == 400 and payload.pop("custom_thumbnail", None):
            continue  # older Lemmy or a thumbnail it won't take: let Lemmy fetch its own
        if r.status_code == 400 and payload.get("url") and any(k in text for k in ("url", "blocked")):
            link = payload.pop("url")
            payload["body"] = (f"🔗 <{link}>\n\n" + payload.get("body", "")).strip()
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(10)
            continue
        return None, f"Lemmy answered {r.status_code}: {text[:150]}"
    return None, "gave up after repeated rate limits/errors"


# ─────────────────────────────────────────────
# One pass over the configured channels
# ─────────────────────────────────────────────
def sync(pairs, *, dry_run: bool, limit: int, delay: float, backfill: bool) -> int:
    conn = db()
    jwt = None if dry_run else (am.get_cached_jwt() or am.lemmy_login(force=True))
    say = print if dry_run else log
    posted = 0

    for channel, community in pairs:
        src = OdyseeSource(channel)
        known = known_ids(conn, src.channel)

        community_id = None
        if not dry_run:
            try:
                community_id = am.get_community_id(community, jwt)
            except Exception:
                log(f"c/{community} doesn't exist on Lemmy yet; create it, then this channel will mirror")
                continue

        # Decide what to post this pass
        if not history_done(conn, src.channel):
            if backfill:
                todo = [p for p in src.uploads(newest_first=False) if p.id not in known]
                say(f"{src.channel} → c/{community}: backfilling {len(todo)} uploads, oldest first")
            else:
                existing = list(src.uploads(newest_first=True))
                if not dry_run:
                    for p in existing:
                        if p.id not in known:
                            record(conn, p, src.channel, community, None, "skipped")
                    mark_history_done(conn, src.channel)
                say(f"{src.channel} → c/{community}: skipping {len(existing)} existing uploads; mirroring new ones from now on")
                continue
        else:
            # New uploads only: newest first until a run of already-mirrored ones
            todo, streak = [], 0
            for p in src.uploads(newest_first=True):
                if p.id in known:
                    streak += 1
                    if streak >= 10:
                        break
                    continue
                streak = 0
                todo.append(p)
            todo.reverse()  # post oldest first

        for p in todo:
            if limit and posted >= limit:
                say(f"Stopping after {posted} posts (--limit). Run again to continue.")
                return posted
            if dry_run:
                kind = p.metadata.get("stream_type") or "upload"
                when = datetime.fromtimestamp(p.created_utc, tz=timezone.utc).date() if p.created_utc else "?"
                print(f"  would post: {when} | {kind} | {p.title} | {p.url}")
                posted += 1
                continue

            payload = {
                "name": am._sanitize_title(p.title, community),
                "community_id": community_id,
                "url": p.url,
                "body": build_body(p),
            }
            if p.metadata.get("thumbnail"):
                payload["custom_thumbnail"] = p.metadata["thumbnail"]
            lemmy_id, detail = create_post(payload, jwt)
            if lemmy_id:
                record(conn, p, src.channel, community, lemmy_id, "posted")
                log(f"✅ Posted '{p.title}' → c/{community} (Lemmy ID={lemmy_id})")
                posted += 1
                time.sleep(delay)
            else:
                log(f"⚠️ Couldn't post '{p.title}': {detail}")

        if not dry_run and backfill and not history_done(conn, src.channel):
            mark_history_done(conn, src.channel)
            log(f"{src.channel}: history mirrored; watching for new uploads")

    return posted


def main() -> int:
    ap = argparse.ArgumentParser(description="Mirror Odysee channels into Lemmy communities.")
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would be posted; post nothing")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many posts")
    ap.add_argument("--channel", help="only this channel (as written in ODYSEE_MAP)")
    args = ap.parse_args()

    pairs = parse_map(os.getenv("ODYSEE_MAP", ""))
    if args.channel:
        pairs = [(c, m) for c, m in pairs if c == args.channel]
    if not pairs:
        print("Nothing to do: set ODYSEE_MAP in .env, e.g. ODYSEE_MAP=@hoffmantactical:3=hoffman_odysee")
        return 1

    delay = float(os.getenv("ODYSEE_POST_DELAY_SECS", "10"))
    interval = int(os.getenv("ODYSEE_INTERVAL_SECS", "3600"))
    backfill = env_bool("ODYSEE_BACKFILL", True)

    while True:
        try:
            n = sync(pairs, dry_run=args.dry_run, limit=args.limit, delay=delay, backfill=backfill)
            (print if args.dry_run else log)(f"Pass finished: {n} {'would be posted' if args.dry_run else 'posted'}")
        except Exception as e:
            log(f"❌ Pass failed: {e}")
            if args.once:
                return 1
        if args.once or args.dry_run:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
