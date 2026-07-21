"""Shared async HTTP client — reuse connections across TomTom/HERE/weather calls."""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Tight timeouts keep the API responsive when external providers stall
_TIMEOUT = httpx.Timeout(1.2, connect=0.5)
_LIMITS = httpx.Limits(max_connections=40, max_keepalive_connections=20)

_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """Return a process-wide AsyncClient (lazily created)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_TIMEOUT, limits=_LIMITS)
        logger.debug("Shared httpx client created")
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
