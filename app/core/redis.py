import logging
import os
from typing import Any, Optional

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    aioredis = None          # type: ignore[assignment]
    _REDIS_AVAILABLE = False

redis_client: Optional[Any] = None
_redis_disabled = False

REDIS_ENABLED = os.getenv("REDIS_ENABLED", "true").lower() in ("1", "true", "yes")


async def init_redis() -> None:
    """
    Initialize the async Redis connection pool with a short timeout.
    Sets redis_client to None so cache_service no-ops instead of hanging.
    """
    global redis_client, _redis_disabled

    if not _REDIS_AVAILABLE or not REDIS_ENABLED:
        logging.info("Redis caching disabled")
        redis_client = None
        _redis_disabled = True
        return

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    try:
        client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
            socket_connect_timeout=0.25,
            socket_timeout=0.5,
        )
        await client.ping()
        redis_client = client
        logging.info("Redis connected: %s", redis_url)
    except Exception as exc:
        logging.warning("Redis unavailable — caching disabled: %s", type(exc).__name__)
        redis_client = None
        _redis_disabled = True
        try:
            await client.aclose()
        except Exception:
            pass


async def get_redis_client() -> Optional[Any]:
    """Return the active Redis client, or None if unavailable."""
    if _redis_disabled:
        return None
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
