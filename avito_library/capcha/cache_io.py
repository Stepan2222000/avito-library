"""Async JSON cache helpers for Geetest offsets."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict

CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "geetest_cache.json"


async def load_cache() -> Dict[str, Dict[str, Any]]:
    """Load cache JSON into a dict keyed by hash."""

    if not CACHE_PATH.exists():
        return {}
    text = await asyncio.to_thread(CACHE_PATH.read_text, "utf-8")
    raw = json.loads(text)
    filtered = [item for item in raw if item.get("definitely")]
    return {item["h_content"]: item for item in filtered}


async def save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    """Atomically save cache dict to JSON."""

    data = list(cache.values())
    text = json.dumps(data, ensure_ascii=False, indent=2)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    await asyncio.to_thread(tmp.write_text, text, "utf-8")
    await asyncio.to_thread(tmp.replace, CACHE_PATH)
