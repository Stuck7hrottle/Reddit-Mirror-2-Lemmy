# community_cache.py
# Case-insensitive community lookup with a 6h persistent cache.
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional

import requests

CACHE_FILENAME = "community_map.json"
CACHE_TTL_SECS = 6 * 60 * 60  # 6 hours


class CommunityCache:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / CACHE_FILENAME
        self.by_exact: Dict[str, int] = {}
        self.by_lower: Dict[str, int] = {}
        self.loaded_at: float = 0.0

    def load_disk(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.by_exact = data.get("by_exact", {})
            self.by_lower = data.get("by_lower", {})
            self.loaded_at = data.get("loaded_at", 0.0)
            return True
        except Exception:
            return False

    def save_disk(self) -> None:
        obj = {
            "by_exact": self.by_exact,
            "by_lower": self.by_lower,
            "loaded_at": self.loaded_at,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def fresh(self) -> bool:
        return (time.time() - self.loaded_at) < CACHE_TTL_SECS and bool(self.by_lower)

    def resolve_id(self, name: str) -> Optional[int]:
        if not name:
            return None
        # Try exact (case-sensitive), then lower-case
        if name in self.by_exact:
            return self.by_exact[name]
        lower = name.lower()
        return self.by_lower.get(lower)

    def refresh_from_lemmy(self, base_url: str, jwt: str) -> None:
        # Fetch all communities visible to this JWT
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}
        # Lemmy 0.19 list endpoint; add a generous limit to reduce pagination needs
        url = f"{base_url}/api/v3/community/list?limit=9999&type_=All"
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        by_exact: Dict[str, int] = {}
        by_lower: Dict[str, int] = {}
        for cv in data.get("communities", []):
            c = cv.get("community", {})
            name = c.get("name")
            cid = c.get("id")
            if isinstance(name, str) and isinstance(cid, int):
                by_exact[name] = cid
                by_lower[name.lower()] = cid
                # Support both “c/foo” & “foo” lookup (be lenient)
                by_exact[f"c/{name}"] = cid
                by_lower[f"c/{name.lower()}"] = cid
        self.by_exact = by_exact
        self.by_lower = by_lower
        self.loaded_at = time.time()
        self.save_disk()


# --- Convenience helpers ---

_cache_instances: Dict[str, CommunityCache] = {}

def _key_for_dir(data_dir: Path) -> str:
    return str(Path(data_dir).resolve())

def get_cache(data_dir: Path) -> CommunityCache:
    key = _key_for_dir(data_dir)
    if key not in _cache_instances:
        cc = CommunityCache(Path(data_dir))
        cc.load_disk()  # best-effort
        _cache_instances[key] = cc
    return _cache_instances[key]

def ensure_fresh(base_url: str, jwt: str, data_dir: Path) -> CommunityCache:
    cc = get_cache(data_dir)
    if not cc.fresh():
        # Attempt refresh; if it fails, keep whatever is on disk
        try:
            cc.refresh_from_lemmy(base_url, jwt)
        except Exception:
            # Don’t crash the bridge on a transient outage
            pass
    return cc

def resolve_community_id(base_url: str, jwt: str, data_dir: Path, name: str) -> Optional[int]:
    """
    Case-insensitive resolver:
      - Try the cache (exact and lowercase)
      - If miss, force a one-shot refresh, then try again
    """
    cc = get_cache(data_dir)
    cid = cc.resolve_id(name)
    if cid is not None:
        return cid

    # Miss: try refreshing once
    try:
        cc.refresh_from_lemmy(base_url, jwt)
        return cc.resolve_id(name)
    except Exception:
        return None
