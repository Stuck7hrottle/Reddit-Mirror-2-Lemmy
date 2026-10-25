#!/usr/bin/env python3
"""
models.py — Shared content schema for all source→destination bridges
-------------------------------------------------------------------
These dataclasses define the normalized structure of posts and comments
as they move through the mirroring system.

Each source adapter (Reddit, Mastodon, Bluesky, etc.)
should produce these objects before handing them off to
the Lemmy client or job queue.

This enables platform-agnostic mirroring logic.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any


# ───────────────────────────────
# Unified Post Schema
# ───────────────────────────────
@dataclass
class SourcePost:
    """
    Represents a single post or submission fetched from any platform.

    Fields:
      - source: identifier for the source platform ("reddit", "mastodon", etc.)
      - id: unique post identifier from the source platform
      - title: title or headline of the post
      - body: text content (or post caption if applicable)
      - author: username or handle of the poster
      - url: optional link or media URL
      - community: source community / subreddit / tag / feed name
      - created_utc: timestamp (UTC seconds)
      - metadata: optional dictionary for platform-specific fields
    """

    source: str
    id: str
    title: str
    body: str
    author: str
    community: str
    created_utc: float
    url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ───────────────────────────────
# Unified Comment Schema
# ───────────────────────────────
@dataclass
class SourceComment:
    """
    Represents a comment from any platform, normalized for mirroring.

    Fields:
      - source: identifier for the source platform
      - id: comment ID in source platform
      - post_id: the source post this comment belongs to
      - author: username or handle
      - body: comment text
      - parent_id: ID of parent comment (None for top-level)
      - created_utc: timestamp (UTC seconds)
      - metadata: optional extra fields
    """

    source: str
    id: str
    post_id: str
    author: str
    body: str
    created_utc: float
    parent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ───────────────────────────────
# Utility for conversion / display
# ───────────────────────────────
def to_dict(obj) -> Dict[str, Any]:
    """Convert any dataclass to a serializable dict."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: getattr(obj, k) for k in obj.__dataclass_fields__}
    raise TypeError("Object is not a dataclass instance")


def from_dict(data: Dict[str, Any], cls):
    """Instantiate a dataclass (SourcePost/SourceComment) from a dict."""
    return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})
