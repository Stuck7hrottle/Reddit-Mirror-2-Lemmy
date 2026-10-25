#!/usr/bin/env python3
"""
sources/odysee.py — read a channel's uploads from Odysee.

Uses Odysee's public API (the same one odysee.com runs on, and the one the
GunCAD mirror already uses), which unlike the RSS feed has a channel's whole
history. Uploads are returned as core.models.SourcePost, so the posting side
doesn't need to know anything about Odysee.
"""

from __future__ import annotations

import os
import time
from typing import Iterator, Optional

import requests

from core.models import SourcePost

API_URL = os.getenv("ODYSEE_API_URL", "https://api.na-backend.odysee.com/api/v1/proxy")
PAGE_SIZE = 50


def odysee_url(canonical: str) -> str:
    """lbry://@chan#3/some-video#6  ->  https://odysee.com/@chan:3/some-video:6"""
    path = (canonical or "").removeprefix("lbry://").replace("#", ":")
    return f"https://odysee.com/{path}"


class OdyseeSource:
    def __init__(self, channel: str, session: Optional[requests.Session] = None):
        # Accept "@name:3", "@name#3" or a full odysee.com channel link
        channel = channel.strip()
        if "odysee.com/" in channel:
            channel = channel.split("odysee.com/", 1)[1].split("/", 1)[0]
        self.channel = channel.replace("#", ":") if channel.startswith("@") else "@" + channel
        self.session = session or requests.Session()

    def _call(self, method: str, params: dict) -> dict:
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        for attempt in range(5):
            try:
                r = self.session.post(API_URL, json=body, timeout=30)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("error"):
                    raise RuntimeError(f"Odysee API error: {data['error']}")
                return data.get("result") or {}
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"Odysee API unreachable after retries ({method})")

    def uploads(self, newest_first: bool = True, max_items: int = 0) -> Iterator[SourcePost]:
        """
        Yield the channel's uploads. newest_first=False walks the whole history
        oldest to newest (for a backfill that reads in order on Lemmy).
        """
        order = ["release_time"] if newest_first else ["^release_time"]
        page, seen = 1, 0
        while True:
            result = self._call("claim_search", {
                "channel": self.channel,
                "claim_type": "stream",
                "order_by": order,
                "page": page,
                "page_size": PAGE_SIZE,
                "no_totals": False,
            })
            items = result.get("items") or []
            for claim in items:
                post = self.to_source_post(claim)
                if post:
                    yield post
                    seen += 1
                    if max_items and seen >= max_items:
                        return
            total_pages = result.get("total_pages") or 0
            if not items or (total_pages and page >= total_pages):
                return
            page += 1

    def to_source_post(self, claim: dict) -> Optional[SourcePost]:
        value = claim.get("value") or {}
        title = (value.get("title") or claim.get("name") or "").strip()
        if not title or not claim.get("claim_id"):
            return None
        signing = claim.get("signing_channel") or {}
        channel_title = ((signing.get("value") or {}).get("title") or signing.get("name") or self.channel).strip()
        released = int(value.get("release_time") or claim.get("timestamp") or 0)
        thumb = (value.get("thumbnail") or {}).get("url")
        video = value.get("video") or {}
        return SourcePost(
            source="odysee",
            id=claim["claim_id"],
            title=title,
            body=(value.get("description") or "").strip(),
            author=channel_title,
            community=self.channel,
            created_utc=float(released),
            url=odysee_url(claim.get("canonical_url") or claim.get("permanent_url") or ""),
            metadata={
                "stream_type": value.get("stream_type"),
                "thumbnail": thumb,
                "duration": video.get("duration"),
                "channel_url": odysee_url(signing.get("canonical_url") or "") if signing else None,
                "tags": value.get("tags") or [],
            },
        )
