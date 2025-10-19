#!/usr/bin/env python3
"""
bridge_registry.py — dynamic registry for multi-source bridges
--------------------------------------------------------------
Centralized bridge loader / router for the mirror system.

Allows Job Workers to resolve a bridge dynamically by:
  - source platform (e.g. "reddit", "mastodon")
  - destination platform (e.g. "lemmy", "kbin")

Future adapters can be added just by dropping a new file
into `bridges/` and registering it here.
"""

import importlib
from typing import Dict, Tuple, Type, Any


class BridgeRegistry:
    """
    Registry for source→destination bridge classes.

    Example:
      from core.bridge_registry import BridgeRegistry
      bridge = BridgeRegistry.get("reddit", "lemmy")
      await bridge.mirror_post("abc123")
    """

    _registry: Dict[Tuple[str, str], str] = {}

    # ───────────────────────────────
    # Register a bridge
    # ───────────────────────────────
    @classmethod
    def register(cls, source: str, destination: str, class_path: str):
        """
        Register a bridge by fully-qualified class path, e.g.:
        'bridges.reddit_to_lemmy.RedditToLemmyBridge'
        """
        key = (source.lower(), destination.lower())
        cls._registry[key] = class_path

    # ───────────────────────────────
    # Resolve bridge
    # ───────────────────────────────
    @classmethod
    def get(cls, source: str, destination: str) -> Any:
        """
        Dynamically import and instantiate the appropriate bridge class.
        """
        key = (source.lower(), destination.lower())
        path = cls._registry.get(key)
        if not path:
            raise KeyError(f"No registered bridge for {source}→{destination}")

        module_name, class_name = path.rsplit(".", 1)
        mod = importlib.import_module(module_name)
        klass = getattr(mod, class_name)
        return klass()

    # ───────────────────────────────
    # List all registered bridges
    # ───────────────────────────────
    @classmethod
    def available(cls) -> Dict[str, str]:
        """Return mapping of registered source→destination bridge names."""
        return {f"{k[0]}→{k[1]}": v for k, v in cls._registry.items()}


# ───────────────────────────────
# Default registrations
# ───────────────────────────────
# You can safely add new bridges here later.
BridgeRegistry.register("reddit", "lemmy", "bridges.reddit_to_lemmy.RedditToLemmyBridge")

# Example future expansions:
# BridgeRegistry.register("mastodon", "lemmy", "bridges.mastodon_to_lemmy.MastodonToLemmyBridge")
# BridgeRegistry.register("bluesky", "lemmy", "bridges.bluesky_to_lemmy.BlueskyToLemmyBridge")
