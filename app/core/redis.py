import os
import logging
from typing import Optional

import aioredis

# Module-level Redis client instance
redis_client: Optional[aioredis.Redis] = None


async def init_redis() -> None:
    """
    Initialize Redis connection pool.

    Creates an async Redis client with connection pooling.
    Verifies connection with a ping. If Redis is unavailable,
    logs a warning and sets redis_client to None (caching disabled).
    """
    global redis_client
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    try:
        redis_client = aioredis.from_url(redis_url, max_connections=10)
        await redis_client.ping()
        logging.info("Redis connected successfully")
    except Exception as e:
        logging.warning(f"Redis unavailable — caching disabled: {e}")
        redis_client = None


async def get_redis_client() -> Optional[aioredis.Redis]:
    """
    Return the Redis client instance.

    Used as a FastAPI dependency to inject Redis client.
    Returns None if Redis is unavailable.
    """
    return redis_client


async def close_redis() -> None:
    """
    Close the Redis connection pool.

    Called during app shutdown to clean up resources.
    """
    if redis_client:
        await redis_client.close()
        logging.info("Redis connection closed")