"""
ETA calculation endpoints for FlowCast.

Provides public routes for single and batch ETA queries and monitored location discovery.
Results are cached in-memory (and Redis when available) for 60 seconds.
Hard target: respond in under ~1.5s even when DB/API is slow.
"""

import asyncio
import logging
import os
import time
from typing import List

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, text

from app.database import SessionLocal
from app.models.predictor import TrafficRecord
from app.schemas.eta import ETAResponse, ETABatchRequest, ETABatchResponse
from app.services.eta_service import (
    calculate_eta_for_location,
    calculate_eta_from_snapshot,
    fetch_live_location_flow,
)
from app.services.cache_service import get_cache, set_cache
from app.utils.api_response import api_success
from app.services.city_aliases import CITY_ALIASES as _CITY_ALIASES_MAP

router = APIRouter(tags=["ETA Calculation"])

logger = logging.getLogger(__name__)

ALLOWED_ETA_MODES = {"driving", "walking", "transit"}
_ALL_MODES = ["driving", "walking", "transit"]
_ETA_CACHE_TTL = 60  # seconds
_ETA_BUDGET_SEC = 1.4
_REDIS_ON = os.getenv("REDIS_ENABLED", "true").lower() in ("1", "true", "yes")
_CITY_LEVEL_SUPPORTED = [k.title() for k in _CITY_ALIASES_MAP]

_mem_cache: dict[str, tuple[float, dict]] = {}


def _cache_get(key: str) -> dict | None:
    entry = _mem_cache.get(key)
    if not entry:
        return None
    expires_at, payload = entry
    if time.monotonic() > expires_at:
        _mem_cache.pop(key, None)
        return None
    return payload


def _cache_set(key: str, payload: dict, ttl: int) -> None:
    _mem_cache[key] = (time.monotonic() + ttl, payload)
    if len(_mem_cache) > 500:
        oldest = min(_mem_cache, key=lambda k: _mem_cache[k][0])
        _mem_cache.pop(oldest, None)


async def _calc_eta(location: str, distance_km: float, mode: str) -> ETAResponse:
    """Live API first (fast), then short DB lookup, then defaults."""

    async def _from_live() -> ETAResponse | None:
        live = await fetch_live_location_flow(location)
        if not live:
            return None
        return calculate_eta_from_snapshot(
            location,
            distance_km,
            mode,
            speed_kmh=float(live["speed_kmh"]),
            congestion_level=str(live["congestion_level"]),
            confidence="high",
            data_age_minutes=0.0,
        )

    def _from_db() -> ETAResponse | None:
        db = SessionLocal()
        try:
            return calculate_eta_for_location(location, distance_km, mode, db)
        except Exception as exc:
            logger.warning("ETA DB path failed: %s", type(exc).__name__)
            try:
                db.rollback()
            except Exception:
                pass
            return None
        finally:
            db.close()

    # 1) Live flow — usually <300ms when TomTom is up
    try:
        live_result = await asyncio.wait_for(_from_live(), timeout=0.75)
        if live_result is not None:
            return live_result
    except Exception as exc:
        logger.debug("Live ETA skipped: %s", type(exc).__name__)

    # 2) DB — hard-capped so a locked table cannot stall the client
    try:
        db_result = await asyncio.wait_for(asyncio.to_thread(_from_db), timeout=0.55)
        if db_result is not None:
            return db_result
    except Exception as exc:
        logger.debug("DB ETA skipped: %s", type(exc).__name__)

    # 3) Instant default — always answers
    return calculate_eta_from_snapshot(
        location,
        distance_km,
        mode,
        speed_kmh=35.0,
        congestion_level="medium",
        confidence="low",
        data_age_minutes=999.0,
    )


@router.get(
    "/traffic/eta",
    status_code=status.HTTP_200_OK,
)
async def get_eta(
    location: str = Query(..., min_length=2, description="Location name (e.g. Hitech City, Koramangala)"),
    distance_km: float = Query(
        ..., gt=0, le=500, description="Distance to travel in kilometers"
    ),
    mode: str = Query("driving", description="Travel mode"),
) -> dict:
    """Calculate real-time ETA for a location. Cached 60 s. Target <1.5 s."""
    normalized_location = location.strip()
    if len(normalized_location) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="location must contain at least 2 characters",
        )
    if mode not in ALLOWED_ETA_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be driving, walking or transit",
        )

    cache_key = f"eta:{normalized_location.lower()}:{distance_km}:{mode}"

    cached = _cache_get(cache_key)
    if cached:
        return api_success(data=cached, message="ETA (cached)")

    if _REDIS_ON:
        try:
            redis_cached = await asyncio.wait_for(get_cache(cache_key), timeout=0.2)
            if redis_cached:
                _cache_set(cache_key, redis_cached, _ETA_CACHE_TTL)
                return api_success(data=redis_cached, message="ETA (cached)")
        except Exception:
            pass

    t0 = time.monotonic()
    result = await _calc_eta(normalized_location, distance_km, mode)
    payload = result.model_dump(mode="json")
    _cache_set(cache_key, payload, _ETA_CACHE_TTL)
    if _REDIS_ON:
        asyncio.create_task(set_cache(cache_key, payload, ttl=_ETA_CACHE_TTL))
    logger.info(
        "ETA %s in %.0fms (cache MISS)",
        normalized_location,
        (time.monotonic() - t0) * 1000,
    )
    return api_success(data=payload)


@router.post(
    "/traffic/eta/batch",
    status_code=status.HTTP_200_OK,
)
async def post_eta_batch(
    request: ETABatchRequest,
) -> dict:
    """Calculate ETA for multiple locations at once."""
    if not request.locations:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="locations list must contain at least one item",
        )
    if request.mode not in ALLOWED_ETA_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be driving, walking or transit",
        )

    tasks = [
        _calc_eta(location.strip(), request.distance_km, request.mode)
        for location in request.locations
        if len(location.strip()) >= 2
    ]
    if not tasks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="each location must contain at least 2 characters",
        )

    results: List[ETAResponse] = await asyncio.gather(*tasks)

    fastest = min(results, key=lambda item: item.eta_minutes)
    slowest = max(results, key=lambda item: item.eta_minutes)
    average_eta_minutes = round(
        sum(item.eta_minutes for item in results) / len(results), 1
    )

    batch = ETABatchResponse(
        results=results,
        total_locations=len(results),
        fastest_location=fastest.location,
        slowest_location=slowest.location,
        average_eta_minutes=average_eta_minutes,
        calculated_at=results[0].calculated_at if results else None,
    )
    logger.info("Batch ETA for %s locations", len(results))
    return api_success(data=batch.model_dump(mode="json"))


@router.get(
    "/traffic/eta/compare",
    status_code=status.HTTP_200_OK,
)
async def compare_eta_modes(
    location: str = Query(..., min_length=2, description="Location name"),
    distance_km: float = Query(..., gt=0, le=500, description="Distance in kilometers"),
) -> dict:
    """Compare ETA for driving, walking, and transit side-by-side for one location."""
    normalized = location.strip()
    if len(normalized) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="location must contain at least 2 characters",
        )

    cache_key = f"eta-compare:{normalized.lower()}:{distance_km}"
    cached = _cache_get(cache_key)
    if cached:
        return api_success(data=cached, message="Mode comparison (cached)")

    etas = await asyncio.gather(
        *[_calc_eta(normalized, distance_km, mode) for mode in _ALL_MODES]
    )

    results = {}
    for mode, eta in zip(_ALL_MODES, etas):
        results[mode] = {
            "eta_minutes": eta.eta_minutes,
            "eta_with_buffer_minutes": eta.eta_with_buffer_minutes,
            "average_speed_kmh": round(eta.average_speed_kmh, 1),
            "congestion_level": eta.congestion_level,
            "traffic_condition": eta.traffic_condition,
            "confidence": eta.confidence,
            "data_age_minutes": eta.data_age_minutes,
            "arrival_time": eta.arrival_time.isoformat(),
        }

    recommended = min(results, key=lambda m: results[m]["eta_minutes"])
    payload = {
        "location": normalized,
        "distance_km": distance_km,
        "modes": results,
        "recommended_mode": recommended,
        "calculated_at": etas[0].calculated_at.isoformat(),
    }
    _cache_set(cache_key, payload, _ETA_CACHE_TTL)
    logger.info("ETA compare for %s @ %.1f km — fastest: %s", normalized, distance_km, recommended)
    return api_success(data=payload)


@router.get(
    "/traffic/eta/locations",
    status_code=status.HTTP_200_OK,
)
async def get_eta_locations() -> dict:
    """Get all monitored locations available for ETA queries across India."""
    cache_key = "eta:locations"
    cached = _cache_get(cache_key)
    if cached:
        return api_success(data=cached)

    def _run() -> dict:
        raw: list[str] = []
        db = SessionLocal()
        try:
            db.execute(text("SET LOCAL statement_timeout = '800ms'"))
            db.execute(text("SET LOCAL lock_timeout = '400ms'"))
            stmt = select(TrafficRecord.location).distinct().order_by(
                TrafficRecord.location.asc()
            ).limit(500)
            raw = list(db.execute(stmt).scalars().all() or [])
        except Exception as exc:
            logger.warning("ETA locations DB query failed (%s) — using seed list", type(exc).__name__)
            try:
                db.rollback()
            except Exception:
                pass
            try:
                from app.services.india_locations import INDIA_LOCATIONS
                raw = sorted({loc["name"] for loc in INDIA_LOCATIONS})
            except Exception:
                raw = []
        finally:
            db.close()

        names_lower = {loc.lower() for loc in raw}
        deduplicated: list[str] = []
        for loc in raw:
            base = loc.split(",")[0].strip()
            if base.lower() != loc.lower() and base.lower() in names_lower:
                continue
            deduplicated.append(loc)

        return {
            "city_level_supported": _CITY_LEVEL_SUPPORTED,
            "locations": deduplicated,
            "total": len(deduplicated),
            "message": (
                "Pass any location name or city shortcut to /traffic/eta. "
                "City-level names aggregate ETA across all their neighbourhoods."
            ),
        }

    payload = await asyncio.to_thread(_run)
    _cache_set(cache_key, payload, 300)
    logger.info("Returned %s ETA locations", payload["total"])
    return api_success(data=payload)
