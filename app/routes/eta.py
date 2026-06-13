"""
ETA calculation endpoints for FlowCast.

Provides public routes for single and batch ETA queries and monitored location discovery.
Results are cached in Redis for 60 seconds to reduce DB load.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import TrafficRecord
from app.schemas.eta import ETAResponse, ETABatchRequest, ETABatchResponse
from app.services.eta_service import calculate_eta_for_location
from app.services.cache_service import get_cache, set_cache

router = APIRouter(tags=["ETA Calculation"])

logger = logging.getLogger(__name__)

ALLOWED_ETA_MODES = {"driving", "walking", "transit"}
_ALL_MODES = ["driving", "walking", "transit"]
_ETA_CACHE_TTL = 60  # seconds


@router.get(
    "/traffic/eta",
    response_model=ETAResponse,
    status_code=status.HTTP_200_OK,
)
async def get_eta(
    location: str = Query(..., min_length=2, description="Hyderabad location name"),
    distance_km: float = Query(
        ..., gt=0, le=500, description="Distance to travel in kilometers"
    ),
    mode: str = Query("driving", description="Travel mode"),
    db: Session = Depends(get_db),
) -> ETAResponse:
    """Calculate real-time ETA for a Hyderabad location. Cached 60 s in Redis."""
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

    cache_key = f"eta:{normalized_location}:{distance_km}:{mode}"
    cached = await get_cache(cache_key)
    if cached:
        logger.debug("ETA cache HIT for %s", cache_key)
        return ETAResponse(**cached)

    logger.info("ETA requested for %s (cache MISS)", normalized_location)
    result = calculate_eta_for_location(normalized_location, distance_km, mode, db)
    await set_cache(cache_key, result.model_dump(mode="json"), ttl=_ETA_CACHE_TTL)
    return result


@router.post(
    "/traffic/eta/batch",
    response_model=ETABatchResponse,
    status_code=status.HTTP_200_OK,
)
def post_eta_batch(
    request: ETABatchRequest,
    db: Session = Depends(get_db),
) -> ETABatchResponse:
    """Calculate ETA for multiple Hyderabad locations at once."""
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

    results: List[ETAResponse] = []
    for location in request.locations:
        normalized_location = location.strip()
        if len(normalized_location) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="each location must contain at least 2 characters",
            )
        results.append(
            calculate_eta_for_location(
                normalized_location,
                request.distance_km,
                request.mode,
                db,
            )
        )

    fastest = min(results, key=lambda item: item.eta_minutes)
    slowest = max(results, key=lambda item: item.eta_minutes)
    average_eta_minutes = round(
        sum(item.eta_minutes for item in results) / len(results), 1
    )

    logger.info("Batch ETA for %s locations", len(results))
    return ETABatchResponse(
        results=results,
        total_locations=len(results),
        fastest_location=fastest.location,
        slowest_location=slowest.location,
        average_eta_minutes=average_eta_minutes,
        calculated_at=results[0].calculated_at if results else None,
    )


@router.get(
    "/traffic/eta/compare",
    status_code=status.HTTP_200_OK,
)
def compare_eta_modes(
    location: str = Query(..., min_length=2, description="Hyderabad location name"),
    distance_km: float = Query(..., gt=0, le=500, description="Distance in kilometers"),
    db: Session = Depends(get_db),
) -> dict:
    """Compare ETA for driving, walking, and transit side-by-side for one location.

    Returns all three mode results plus the recommended (fastest) mode.
    """
    normalized = location.strip()
    if len(normalized) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="location must contain at least 2 characters",
        )

    results = {}
    for mode in _ALL_MODES:
        eta = calculate_eta_for_location(normalized, distance_km, mode, db)
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

    logger.info("ETA compare for %s @ %.1f km — fastest: %s", normalized, distance_km, recommended)
    return {
        "location": normalized,
        "distance_km": distance_km,
        "modes": results,
        "recommended_mode": recommended,
        "calculated_at": eta.calculated_at.isoformat(),
    }


from app.services.city_aliases import CITY_ALIASES as _CITY_ALIASES_MAP

_CITY_LEVEL_SUPPORTED = [k.title() for k in _CITY_ALIASES_MAP]


@router.get(
    "/traffic/eta/locations",
    status_code=status.HTTP_200_OK,
)
def get_eta_locations(
    db: Session = Depends(get_db),
) -> dict:
    """Get all monitored locations available for ETA queries across India.

    City-level names (e.g. 'Hyderabad') aggregate traffic across all their
    neighbourhoods and return a single blended ETA.
    """
    stmt = select(TrafficRecord.location).distinct().order_by(
        TrafficRecord.location.asc()
    )
    result = db.execute(stmt)
    raw: list[str] = result.scalars().all() or []

    # Deduplicate: skip "X, State" when plain "X" already exists
    names_lower = {loc.lower() for loc in raw}
    deduplicated: list[str] = []
    for loc in raw:
        base = loc.split(",")[0].strip()
        if base.lower() != loc.lower() and base.lower() in names_lower:
            continue
        deduplicated.append(loc)

    logger.info("Returned %s ETA locations (raw %s, deduplicated %s)", len(deduplicated), len(raw), len(raw) - len(deduplicated))
    return {
        "city_level_supported": _CITY_LEVEL_SUPPORTED,
        "locations": deduplicated,
        "total": len(deduplicated),
        "message": (
            "Pass any location name or city shortcut to /traffic/eta. "
            "City-level names aggregate ETA across all their neighbourhoods."
        ),
    }


# SECTION A — curl commands:
# Single ETA driving
# curl "http://localhost:8000/traffic/eta?location=Hitech%20City&distance_km=12.5&mode=driving"
#
# Walking ETA
# curl "http://localhost:8000/traffic/eta?location=Gachibowli&distance_km=2.0&mode=walking"
#
# Batch ETA
# curl -X POST http://localhost:8000/traffic/eta/batch \
# -H "Content-Type: application/json" \
# -d '{
#   "locations": ["Gachibowli", "Hitech City", "Banjara Hills"],
#   "distance_km": 12.5,
#   "mode": "driving"
# }'
#
# Available locations
# curl http://localhost:8000/traffic/eta/locations

# SECTION B — Sample ETAResponse:
# {
#   "location": "Hitech City",
#   "distance_km": 12.5,
#   "eta_minutes": 21.4,
#   "eta_with_buffer_minutes": 23.6,
#   "congestion_level": "medium",
#   "average_speed_kmh": 35.0,
#   "vehicle_count": 67,
#   "traffic_condition": "Moderate traffic — slight delays possible",
#   "confidence": "high",
#   "calculated_at": "2026-05-08T10:30:00"
# }
