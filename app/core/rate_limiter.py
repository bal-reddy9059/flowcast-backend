"""
Rate limiting configuration for FlowCast using slowapi and Redis.

This module defines a shared limiter instance, a Redis-backed storage adapter,
and a custom rate limit exceeded handler for FastAPI.
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

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"], storage_uri=REDIS_URL)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a custom JSON response when rate limit is exceeded."""
    retry_after = int(exc.detail.get("retry_after", 0)) if isinstance(exc.detail, dict) else 0
    logger.warning(
        "Rate limit exceeded for IP=%s path=%s retry_after=%s",
        request.client.host if request.client else "unknown",
        request.url.path,
        retry_after,
    )
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "message": f"Too many requests. Try again in {retry_after} seconds.",
            "retry_after": retry_after,
        },
    )


def setup_rate_limiter(app):
    """Attach SlowAPI middleware and custom error handler to the FastAPI app."""
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
