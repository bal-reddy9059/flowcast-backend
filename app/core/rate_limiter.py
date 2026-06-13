"""
Rate limiting for FlowCast using slowapi.

Tries Redis-backed storage first; falls back to in-memory if Redis is unavailable.
The exception handler is tolerant of both RateLimitExceeded and any stray
ConnectionError/redis errors that slowapi surfaces as exceptions.
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


def _build_limiter() -> Limiter:
    """
    Return a Redis-backed Limiter if Redis is reachable right now,
    otherwise an in-memory Limiter.

    The synchronous ping check lets us decide at startup rather than
    discovering the failure on the first request inside the middleware.
    """
    try:
        import redis as _redis_sync
        client = _redis_sync.from_url(REDIS_URL, socket_connect_timeout=1)
        client.ping()
        client.close()
        lim = Limiter(
            key_func=get_remote_address,
            default_limits=["100/minute"],
            storage_uri=REDIS_URL,
        )
        logger.info("Rate limiter: Redis storage at %s", REDIS_URL)
        return lim
    except Exception as exc:
        logger.warning("Rate limiter: Redis unavailable (%s) — using in-memory storage", exc)
        return Limiter(key_func=get_remote_address, default_limits=["100/minute"])


limiter = _build_limiter()


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle both RateLimitExceeded and any stray connection/storage errors
    that slowapi middleware can surface.
    """
    # Normal rate-limit path
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

    # Unexpected storage / connection error surfaced by slowapi middleware
    logger.error(
        "Rate limiter storage error on %s %s: %s",
        request.method, request.url.path, exc,
    )
    # Let the request through rather than returning a 500 — rate-limiting is
    # non-critical; a Redis blip should never block legitimate traffic.
    return None   # returning None tells Starlette to continue to the next handler


def setup_rate_limiter(app) -> None:
    """Attach SlowAPI middleware and error handler to the FastAPI app."""
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    # Register for RateLimitExceeded AND generic Exception so ConnectionErrors
    # from a flapping Redis don't propagate as unhandled AttributeErrors.
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(Exception, _generic_exception_handler)


def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all that specifically intercepts slowapi ConnectionError objects
    (which have no .detail).  All other exceptions are re-raised so FastAPI's
    own 500 handler can deal with them.
    """
    # Only swallow connection-type errors from the rate-limiter storage layer
    exc_name = type(exc).__name__
    if "ConnectionError" in exc_name or "Redis" in exc_name or "BusyLoadingError" in exc_name:
        logger.warning(
            "Rate limiter connection error on %s %s — passing request through: %s",
            request.method, request.url.path, exc,
        )
        # Return None to let the real route handler run
        return None

    # Everything else — re-raise for FastAPI's default handler
    raise exc
