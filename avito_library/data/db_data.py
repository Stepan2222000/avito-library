"""Runtime configuration for captcha cache storage."""

from __future__ import annotations

# Storage backend mode:
#   - "json": keep using the local geetest_cache.json file
#   - "postgres": persist offsets in the configured PostgreSQL table
STORAGE_MODE: str = "postgres"

# PostgreSQL connection parameters used when STORAGE_MODE == "postgres".
POSTGRES_CONFIG: dict[str, object] = {
    "host": "81.30.105.134",
    "port": 5411,
    "database": "capcha",
    "user": "admin",
    "password": "Password123",
}

# Connection pool sizing for asyncpg.
POSTGRES_POOL_MIN_SIZE: int = 10
POSTGRES_POOL_MAX_SIZE: int = 50

# Destination table for storing captcha offsets.
POSTGRES_TABLE_NAME: str = "geetest_cache"
