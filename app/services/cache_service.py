"""
Redis caching service for FlowCast.

Provides async helper functions for caching JSON payloads and retrieving
Redis statistics. This module is used by route handlers to reduce database
load for frequently requested traffic and ETA data.
"""

import json
import logging
from typing import Any

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)


async def get_cache(key: str) -> dict | None:
    """Retrieve a JSON cache entry for the given key."""
    try:
        client = await get_redis_client()
        raw_value = await client.get(key)
        if raw_value is None:
            return None
        return json.loads(raw_value)
    except Exception as error:
        logger.warning("Redis cache get failed for key=%s: %s", key, error)
        return None


async def set_cache(key: str, value: dict, ttl: int) -> None:
    """Set a JSON cache entry with a TTL in seconds."""
    try:
        client = await get_redis_client()
        await client.set(key, json.dumps(value), ex=ttl)
    except Exception as error:
        logger.warning("Redis cache set failed for key=%s: %s", key, error)


async def delete_cache(key: str) -> None:
    """Delete a specific cache entry by key."""
    try:
        client = await get_redis_client()
        await client.delete(key)
    except Exception as error:
        logger.warning("Redis cache delete failed for key=%s: %s", key, error)


async def clear_all_cache() -> None:
    """Clear all Redis cache entries for the application."""
    try:
        client = await get_redis_client()
        await client.flushdb()
    except Exception as error:
        logger.warning("Redis cache flush failed: %s", error)


async def get_cache_stats() -> dict[str, Any]:
    """Return Redis cache statistics for monitoring and health checks."""
    try:
        client = await get_redis_client()
        info = await client.info()

        keyspace = info.get("Keyspace", {}) or info.get("keyspace", {})
        db0 = keyspace.get("db0", {}) if isinstance(keyspace, dict) else {}
        total_keys = int(db0.get("keys", 0)) if isinstance(db0, dict) else 0

        used_memory = info.get("used_memory_human") or info.get("used_memory", 0)
        hits = int(info.get("keyspace_hits", 0))
        misses = int(info.get("keyspace_misses", 0))
        hit_rate = round(hits / (hits + misses), 2) if (hits + misses) > 0 else 0.0

        return {
            "total_keys": total_keys,
            "memory_used": used_memory,
            "hit_rate": hit_rate,
        }
    except Exception as error:
        logger.warning("Redis cache stats retrieval failed: %s", error)
        return {
            "total_keys": 0,
            "memory_used": "0B",
            "hit_rate": 0.0,
        }


# Cache key naming conventions:
# heatmap:{hours}:{filter}:{intensity}
# eta:{location}:{distance}:{mode}
# snapshot:{hours}
# trend:{location}:{intervals}
