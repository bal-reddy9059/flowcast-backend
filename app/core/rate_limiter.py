"""
Rate limiting for FlowCast using slowapi.

Tries Redis-backed storage first; falls back to in-memory if Redis is unavailable.
Uses a very short connect timeout so a missing Redis never hangs startup or reload.
"""

import logging
import os

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# Set REDIS_ENABLED=false to skip Redis entirely (recommended when Redis is not installed)
# Rate limiting is intentionally in-memory unless explicitly enabled. Reusing a
# flaky application Redis for request middleware can otherwise stall every API
# call for the Redis client's multi-minute retry window.
REDIS_ENABLED = os.getenv("RATE_LIMIT_REDIS_ENABLED", "false").lower() in ("1", "true", "yes")


def _build_limiter() -> Limiter:
    """Return a Redis-backed Limiter if Redis responds quickly, else in-memory."""
    if not REDIS_ENABLED:
        logger.info("Rate limiter: REDIS_ENABLED=false — using in-memory storage")
        return Limiter(key_func=get_remote_address, default_limits=["120/minute"])

    try:
        import redis as _redis_sync
        client = _redis_sync.from_url(
            REDIS_URL,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        client.ping()
        client.close()
        lim = Limiter(
            key_func=get_remote_address,
            default_limits=["120/minute"],
            storage_uri=REDIS_URL,
            storage_options={
                "socket_connect_timeout": 0.25,
                "socket_timeout": 0.25,
                "retry_on_timeout": False,
            },
        )
        logger.info("Rate limiter: Redis storage at %s", REDIS_URL)
        return lim
    except Exception as exc:
        logger.warning(
            "Rate limiter: Redis unavailable (%s) — using in-memory storage",
            type(exc).__name__,
        )
        return Limiter(key_func=get_remote_address, default_limits=["120/minute"])


limiter = _build_limiter()


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, RateLimitExceeded):
        detail = exc.detail if hasattr(exc, "detail") else str(exc)
        retry_after = 0
        if isinstance(detail, dict):
            retry_after = int(detail.get("retry_after", 0))
        logger.warning(
            "Rate limit exceeded for IP=%s path=%s",
            request.client.host if request.client else "unknown",
            request.url.path,
        )
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "message": f"Too many requests. Try again in {retry_after} seconds.",
                "retry_after": retry_after,
            },
        )

    logger.error(
        "Rate limiter storage error on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return None


def setup_rate_limiter(app) -> None:
    """Attach SlowAPI middleware and error handler to the FastAPI app."""
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(Exception, _generic_exception_handler)


def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    exc_name = type(exc).__name__
    if "ConnectionError" in exc_name or "Redis" in exc_name or "BusyLoadingError" in exc_name:
        logger.warning(
            "Rate limiter connection error on %s %s — passing request through: %s",
            request.method, request.url.path, exc,
        )
        return None
    raise exc
