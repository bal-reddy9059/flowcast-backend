import logging
import os
from typing import Any, Optional

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    aioredis = None          # type: ignore[assignment]
    _REDIS_AVAILABLE = False

# Module-level client — None means caching is disabled
redis_client: Optional[Any] = None


async def init_redis() -> None:
    """
    Initialize the async Redis connection pool.

    Uses redis.asyncio (redis-py >= 4.2). Falls back gracefully — sets
    redis_client to None so cache_service no-ops instead of crashing.
    """
    global redis_client

    if not _REDIS_AVAILABLE:
        logging.warning("redis package not installed — caching disabled")
        return

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
        await client.ping()
        redis_client = client
        logging.info("Redis connected: %s", redis_url)
    except Exception as exc:
        logging.warning("Redis unavailable — caching disabled: %s", exc)
        redis_client = None


async def get_redis_client() -> Optional[Any]:
    """Return the active Redis client, or None if unavailable."""
    return redis_client


async def close_redis() -> None:
    """Close the Redis connection pool on shutdown."""
    global redis_client
    if redis_client is not None:
        try:
            await redis_client.aclose()
        except Exception:
            pass
        redis_client = None
        logging.info("Redis connection closed")
