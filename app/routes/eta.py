"""
ETA calculation endpoints for FlowCast.

Provides public routes for single and batch ETA queries and monitored location discovery.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.predictor import TrafficRecord
from app.schemas.eta import ETAResponse, ETABatchRequest, ETABatchResponse
from app.services.eta_service import calculate_eta_for_location

router = APIRouter(tags=["ETA Calculation"])

logger = logging.getLogger(__name__)

ALLOWED_ETA_MODES = {"driving", "walking", "transit"}


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
    db: AsyncSession = Depends(get_db),
) -> ETAResponse:
    """Calculate real-time ETA for a Hyderabad location."""
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

    logger.info("ETA requested for %s", normalized_location)
    return await calculate_eta_for_location(
        normalized_location,
        distance_km,
        mode,
        db,
    )


@router.post(
    "/traffic/eta/batch",
    response_model=ETABatchResponse,
    status_code=status.HTTP_200_OK,
)
async def post_eta_batch(
    request: ETABatchRequest,
    db: AsyncSession = Depends(get_db),
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
            await calculate_eta_for_location(
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
    "/traffic/eta/locations",
    status_code=status.HTTP_200_OK,
)
async def get_eta_locations(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get all available monitored locations in Hyderabad for ETA queries."""
    stmt = select(TrafficRecord.location).distinct().order_by(
        TrafficRecord.location.asc()
    )
    result = await db.execute(stmt)
    locations = result.scalars().all() or []
    total = len(locations)

    logger.info("Returned %s available ETA locations", total)
    return {
        "locations": locations,
        "total": total,
        "message": "Use these names with /traffic/eta",
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
