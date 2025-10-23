#!/usr/bin/env python3
"""
Bridge Configuration Loader
────────────────────────────
Centralized configuration for Reddit ↔ Lemmy bridge scripts.

This ensures consistent environment handling across:
  • reddit_comment_sync.py
  • lemmy_comment_sync.py
  • auto_mirror.py
  • future worker or dashboard scripts
"""

import os

class BridgeConfig:
    def __init__(self):
        # ────── Lemmy Configuration ──────
        self.LEMMY_URL = os.getenv("LEMMY_URL", "https://fosscad.guncaddesigns.com").rstrip("/")
        self.LEMMY_USER = os.getenv("LEMMY_USER", "")
        self.LEMMY_PASS = os.getenv("LEMMY_PASS", "")
        self.LEMMY_BOT_USERNAME = os.getenv("LEMMY_BOT_USERNAME", "").lower()
        self.LEMMY_COMMENT_SYNC_INTERVAL = int(os.getenv("LEMMY_COMMENT_SYNC_INTERVAL", "600"))  # 10 min default
        self.LEMMY_COMMENT_FETCH_LIMIT = int(os.getenv("LEMMY_COMMENT_FETCH_LIMIT", "50"))

        # ────── Reddit Configuration ──────
        self.REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
        self.REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
        self.REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
        self.REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
        self.REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "RedditBridgeBot/1.0")
        self.REDDIT_BOT_USERNAME = os.getenv("REDDIT_BOT_USERNAME", "").lower()
        self.REDDIT_COMMENT_SYNC_INTERVAL = int(os.getenv("REDDIT_COMMENT_SYNC_INTERVAL", "600"))  # 10 min default

        # ────── General Paths ──────
        self.DATA_DIR = os.getenv("DATA_DIR", "/opt/Reddit-Mirror-2-Lemmy/data")
        self.DB_PATH = os.path.join(self.DATA_DIR, "jobs.db")
        self.TOKEN_PATH = os.path.join(self.DATA_DIR, "lemmy_token.json")

        # ────── Behavior Flags ──────
        self.DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
        self.MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
        self.RETRY_DELAY = int(os.getenv("RETRY_DELAY", "2"))  # seconds

    def summary(self):
        """Human-readable summary (for debug/logging)."""
        return {
            "Lemmy": {
                "url": self.LEMMY_URL,
                "user": self.LEMMY_USER,
                "interval": self.LEMMY_COMMENT_SYNC_INTERVAL,
            },
            "Reddit": {
                "username": self.REDDIT_USERNAME,
                "interval": self.REDDIT_COMMENT_SYNC_INTERVAL,
            },
            "Paths": {
                "data_dir": self.DATA_DIR,
                "db_path": self.DB_PATH,
            },
        }

# Global singleton pattern
config = BridgeConfig()
