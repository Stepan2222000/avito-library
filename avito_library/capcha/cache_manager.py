"""High-level async cache manager for Geetest offsets."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from .cache_io import load_cache, save_cache

_CACHE: Dict[str, Dict[str, Any]] | None = None
_CACHE_LOCK = asyncio.Lock()


async def get_cache() -> Dict[str, Dict[str, Any]]:
    """Return in-memory cache, loading lazily from disk."""

    global _CACHE
    if _CACHE is not None:
        return _CACHE
    async with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = await load_cache()
    return _CACHE


async def get_offset(hash_key: str) -> Optional[Dict[str, Any]]:
    """Fetch offset entry for given hash."""

    cache = await get_cache()
    entry = cache.get(hash_key)
    if entry is None:
        return None
    if not entry.get("definitely"):
        return None
    return entry


async def update_offset(hash_key: str, *, offset: int, definitely: bool) -> None:
    """Update cache entry and persist to JSON."""

    cache = await get_cache()
    async with _CACHE_LOCK:
        cache[hash_key] = {
            "h_content": hash_key,
            "offset": offset,
            "definitely": definitely,
        }
        await save_cache(cache)
