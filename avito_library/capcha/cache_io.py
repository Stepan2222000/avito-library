"""Async cache helpers for Geetest offsets.

Supports local JSON storage and PostgreSQL-backed persistence.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..data import db_data

if TYPE_CHECKING:
    import asyncpg

CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "geetest_cache.json"
_VALID_STORAGE_MODES = {"json", "postgres"}
_STORAGE_MODE = getattr(db_data, "STORAGE_MODE", "json").strip().lower()

if _STORAGE_MODE not in _VALID_STORAGE_MODES:
    raise ValueError(
        f"Unsupported captcha cache storage mode '{_STORAGE_MODE}'. "
        f"Valid options: {', '.join(sorted(_VALID_STORAGE_MODES))}.",
    )

_POSTGRES_TABLE = getattr(db_data, "POSTGRES_TABLE_NAME", "geetest_cache")
if _STORAGE_MODE == "postgres":
    if not isinstance(_POSTGRES_TABLE, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*",
        _POSTGRES_TABLE,
    ):
        raise ValueError(
            f"Invalid PostgreSQL table name '{_POSTGRES_TABLE}'. "
            "Use alphanumeric characters and underscores only.",
        )

_POOL: Optional["asyncpg.Pool"] = None

# Экспортируем текущий режим хранения, чтобы другие модули могли принимать решения.
STORAGE_MODE = _STORAGE_MODE


async def load_cache() -> Dict[str, Dict[str, Any]]:
    """Load cache into a dict keyed by hash."""

    if _STORAGE_MODE == "json":
        return await _load_cache_json()
    return await _load_cache_postgres()


async def save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    """Persist cache based on the configured storage mode."""

    if _STORAGE_MODE == "json":
        await _save_cache_json(cache)
    else:
        await _save_cache_postgres(cache)


async def fetch_entry(hash_key: str) -> Optional[Dict[str, Any]]:
    """Fetch a single cache entry."""

    if _STORAGE_MODE == "json":
        cache = await _load_cache_json()
        return cache.get(hash_key)
    return await _fetch_entry_postgres(hash_key)


async def upsert_entry(entry: Dict[str, Any]) -> None:
    """Insert or update cache entry depending on storage mode."""

    normalized = _normalize_entry(entry or {})
    if normalized is None:
        raise ValueError("Cannot upsert entry without definite offset and h_content.")
    if _STORAGE_MODE == "json":
        cache = await _load_cache_json()
        cache[normalized["h_content"]] = normalized
        await _save_cache_json(cache)
    else:
        await _upsert_entry_postgres(normalized)


async def remove_entry(hash_key: str) -> None:
    """Remove cache entry for given key."""

    if _STORAGE_MODE == "json":
        cache = await _load_cache_json()
        if hash_key in cache:
            cache.pop(hash_key, None)
            await _save_cache_json(cache)
        return
    await _remove_entry_postgres(hash_key)


async def increment_failure_postgres(hash_key: str, threshold: int) -> bool:
    """Atomically increment failure counter in Postgres; delete if threshold reached."""

    pool = await _get_pool()
    update_sql = (
        f"UPDATE {_POSTGRES_TABLE} "
        "SET fail_count = fail_count + 1 "
        "WHERE h_content = $1 "
        "RETURNING fail_count"
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(update_sql, hash_key)
        if row is None:
            return False
        fail_count = int(row["fail_count"])
        if fail_count >= threshold:
            await conn.execute(
                f"DELETE FROM {_POSTGRES_TABLE} WHERE h_content = $1",
                hash_key,
            )
            return True
        return False


def _normalize_entry(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not item.get("definitely"):
        return None
    h_content = item.get("h_content")
    if not h_content:
        return None
    return {
        "h_content": str(h_content),
        "offset": int(item.get("offset", 0) or 0),
        "definitely": bool(item.get("definitely")),
        "fail_count": int(item.get("fail_count", 0) or 0),
    }


async def _load_cache_json() -> Dict[str, Dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    text = await asyncio.to_thread(CACHE_PATH.read_text, "utf-8")
    if not text.strip():
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, list):
        return {}
    filtered = []
    for item in raw:
        normalized = _normalize_entry(item)
        if normalized is None:
            continue
        filtered.append(normalized)
    return {item["h_content"]: item for item in filtered}


async def _save_cache_json(cache: Dict[str, Dict[str, Any]]) -> None:
    data = list(cache.values())
    text = json.dumps(data, ensure_ascii=False, indent=2)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    await asyncio.to_thread(tmp.write_text, text, "utf-8")
    await asyncio.to_thread(tmp.replace, CACHE_PATH)


async def _load_cache_postgres() -> Dict[str, Dict[str, Any]]:
    pool = await _get_pool()
    query = (
        f"SELECT h_content, offset, definitely, fail_count "
        f"FROM {_POSTGRES_TABLE} WHERE definitely IS TRUE"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query)
    filtered = []
    for row in rows:
        normalized = _normalize_entry(dict(row))
        if normalized is None:
            continue
        filtered.append(normalized)
    return {item["h_content"]: item for item in filtered}


async def _save_cache_postgres(cache: Dict[str, Dict[str, Any]]) -> None:
    if not cache:
        # При пустом словаре ничего не делаем, чтобы не удалять внешние записи.
        return
    # Для Postgres используем upsert по каждому ключу, не затрагивая
    # отсутствующие в словаре записи — они считаются внешними изменениями.
    for entry in cache.values():
        normalized = _normalize_entry(entry)
        if normalized is None:
            continue
        await _upsert_entry_postgres(normalized)


async def _fetch_entry_postgres(hash_key: str) -> Optional[Dict[str, Any]]:
    pool = await _get_pool()
    query = (
        f"SELECT h_content, offset, definitely, fail_count "
        f"FROM {_POSTGRES_TABLE} WHERE h_content = $1"
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, hash_key)
    if row is None:
        return None
    return _normalize_entry(dict(row))


async def _upsert_entry_postgres(entry: Dict[str, Any]) -> None:
    pool = await _get_pool()
    insert_sql = (
        f"INSERT INTO {_POSTGRES_TABLE} (h_content, offset, definitely, fail_count) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (h_content) DO UPDATE "
        "SET offset = EXCLUDED.offset, definitely = EXCLUDED.definitely, fail_count = EXCLUDED.fail_count"
    )
    async with pool.acquire() as conn:
        await conn.execute(
            insert_sql,
            entry["h_content"],
            int(entry.get("offset", 0) or 0),
            bool(entry.get("definitely")),
            int(entry.get("fail_count", 0) or 0),
        )


async def _remove_entry_postgres(hash_key: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"DELETE FROM {_POSTGRES_TABLE} WHERE h_content = $1",
            hash_key,
        )


async def _get_pool() -> "asyncpg.Pool":
    global _POOL
    if _POOL is not None:
        return _POOL
    try:
        import asyncpg  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency should exist
        raise RuntimeError(
            "asyncpg is required for PostgreSQL captcha cache backend",
        ) from exc
    config = dict(getattr(db_data, "POSTGRES_CONFIG", {}))
    min_size = int(getattr(db_data, "POSTGRES_POOL_MIN_SIZE", 10) or 1)
    max_size = int(getattr(db_data, "POSTGRES_POOL_MAX_SIZE", 10) or min_size)
    if max_size < min_size:
        raise ValueError(
            f"POSTGRES_POOL_MAX_SIZE ({max_size}) must be >= POSTGRES_POOL_MIN_SIZE ({min_size}).",
        )
    _POOL = await asyncpg.create_pool(
        min_size=min_size,
        max_size=max_size,
        **config,
    )
    create_sql = (
        f"CREATE TABLE IF NOT EXISTS {_POSTGRES_TABLE} ("
        "h_content TEXT PRIMARY KEY,"
        "offset INTEGER NOT NULL,"
        "definitely BOOLEAN NOT NULL DEFAULT FALSE,"
        "fail_count INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    async with _POOL.acquire() as conn:
        await conn.execute(create_sql)
    return _POOL
