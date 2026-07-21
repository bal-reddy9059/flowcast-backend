"""
Unified live traffic flow fetcher — TomTom first, HERE fallback.

HERE is skipped when unavailable/invalid so we never wait on 401s every cycle.
"""

import asyncio
import logging
import os
from typing import Optional

from app.services import here_traffic_service
from app.services.tomtom_service import fetch_flow as tomtom_fetch_flow

logger = logging.getLogger(__name__)

_CALL_DELAY = 0.0  # parallel batching preferred; keep 0 for speed

REAL_DATA_ONLY = os.getenv("REAL_DATA_ONLY", "true").lower() in ("1", "true", "yes")


def _tomtom_ok() -> bool:
    from app.services.tomtom_service import TOMTOM_API_KEY, _key_invalid as _tt_invalid
    return bool(TOMTOM_API_KEY) and not _tt_invalid


def is_live_available() -> bool:
    """True when at least one real traffic API key is configured and active."""
    return here_traffic_service.is_available() or _tomtom_ok()


def active_source() -> str:
    if _tomtom_ok():
        return "tomtom"
    if here_traffic_service.is_available():
        return "here"
    return "unavailable"


async def fetch_flow(lat: float, lng: float) -> Optional[dict]:
    """
    Fetch real-time traffic flow for a coordinate.

    Prefer TomTom (stable for this project); try HERE only if TomTom fails
    and HERE is still marked available.
    """
    # 1. TomTom first — avoids waiting on a known-bad HERE key
    if _tomtom_ok():
        flow = await tomtom_fetch_flow(lat, lng)
        if flow is not None:
            return {**flow, "source": "tomtom"}

    # 2. HERE only when still considered valid
    if here_traffic_service.is_available():
        flow = await here_traffic_service.fetch_flow(lat, lng)
        if flow is not None:
            return flow

    return None


async def fetch_flow_batch(
    points: list[tuple[float, float]],
    delay: float = _CALL_DELAY,
) -> list[Optional[dict]]:
    """Fetch flow for multiple lat/lng pairs with optional delay between calls."""
    results: list[Optional[dict]] = []
    for i, (lat, lng) in enumerate(points):
        results.append(await fetch_flow(lat, lng))
        if delay > 0 and i < len(points) - 1:
            await asyncio.sleep(delay)
    return results


def speed_ratio_to_score(
    current_speed: float,
    free_flow_speed: float,
    station_type: str = "bus",
    prev_score: int | None = None,
) -> int:
    """
    Map road congestion near a station to a 0–100 crowd score.

    Slower traffic relative to free-flow ⇒ higher station crowd estimate.
    """
    if free_flow_speed <= 0:
        ratio = 0.5
    else:
        ratio = max(0.0, min(1.0, current_speed / free_flow_speed))

    # Invert: congested roads → high crowd
    score = (1.0 - ratio) * 100.0

    # Railway hubs typically draw more footfall than bus stands at same congestion
    if station_type == "railway":
        score *= 1.12

    score = min(100.0, max(0.0, score))

    # Smooth vs previous reading (max ±8 per 30 s tick)
    if prev_score is not None:
        drift = 8
        score = max(prev_score - drift, min(prev_score + drift, score))

    return int(round(score))


def score_to_level(score: int) -> str:
    if score <= 25:
        return "Low"
    if score <= 50:
        return "Moderate"
    if score <= 75:
        return "High"
    return "Overcrowded"


def congestion_to_score(congestion: str) -> int:
    """Map low/medium/high congestion label to a crowd score midpoint."""
    return {"low": 18, "medium": 48, "high": 82}.get(congestion, 50)
