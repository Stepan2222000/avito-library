"""High-level async cache manager for Geetest offsets."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from .cache_io import (
    STORAGE_MODE,
    fetch_entry,
    increment_failure_postgres,
    load_cache,
    save_cache,
    upsert_entry,
)

_CACHE: Dict[str, Dict[str, Any]] | None = None
_CACHE_LOCK = asyncio.Lock()
FAILURE_THRESHOLD = 5


async def get_cache() -> Dict[str, Dict[str, Any]]:
    """Return in-memory cache, loading lazily from disk."""

    global _CACHE
    if STORAGE_MODE == "postgres":
        return await load_cache()
    if _CACHE is not None:
        return _CACHE
    async with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = await load_cache()
    return _CACHE


async def get_offset(hash_key: str) -> Optional[Dict[str, Any]]:
    """Fetch offset entry for given hash."""

    if STORAGE_MODE == "postgres":
        entry = await fetch_entry(hash_key)
        if entry is None or not entry.get("definitely"):
            return None
        return entry

    cache = await get_cache()
    entry = cache.get(hash_key)
    if entry is None or not entry.get("definitely"):
        return None
    return entry


async def update_offset(
    hash_key: str,
    *,
    offset: int,
    definitely: bool,
    fail_count: int = 0,
) -> None:
    """Update cache entry and persist to JSON."""

    normalized = {
        "h_content": hash_key,
        "offset": offset,
        "definitely": definitely,
        "fail_count": max(0, int(fail_count)),
    }

    if STORAGE_MODE == "postgres":
        await upsert_entry(normalized)
        return

    cache = await get_cache()
    async with _CACHE_LOCK:
        cache[hash_key] = normalized
        await save_cache(cache)


async def record_failure(hash_key: str) -> bool:
    """Increment failure counter; return True if entry removed."""

    if STORAGE_MODE == "postgres":
        return await increment_failure_postgres(hash_key, FAILURE_THRESHOLD)

    cache = await get_cache()
    async with _CACHE_LOCK:
        entry = cache.get(hash_key)
        if entry is None:
            return False

        fail_count = int(entry.get("fail_count", 0) or 0) + 1
        if fail_count >= FAILURE_THRESHOLD:
            cache.pop(hash_key, None)
            await save_cache(cache)
            return True

        entry["fail_count"] = fail_count
        cache[hash_key] = entry
        await save_cache(cache)
        return False
