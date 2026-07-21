"""
Traffic heatmap endpoints for Hyderabad traffic visualization.

Provides public endpoints for heatmap points, congestion hotspots, and
city-wide summary statistics without requiring authentication.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import TrafficRecord
from app.schemas.heatmap import HeatmapPoint, HeatmapResponse
from app.services.heatmap_service import (
    calculate_intensity,
    classify_severity,
    get_heatmap_data,
    get_india_hotspots,
)
from app.services.cache_service import get_cache, set_cache

router = APIRouter(tags=["Traffic Heatmap"])
logger = logging.getLogger(__name__)
ALLOWED_CONGESTION_FILTERS = {"low", "medium", "high"}
ALLOWED_SEVERITY_FILTERS = {"critical", "high", "moderate", "low"}
_HEATMAP_CACHE_TTL = 120  # seconds


@router.get(
    "/traffic/heatmap",
    response_model=HeatmapResponse,
    status_code=status.HTTP_200_OK,
)
async def get_heatmap(
    hours: int = Query(1, ge=1, le=24, description="Hours of traffic history to include"),
    congestion_filter: Optional[str] = Query(
        None,
        description="Optional congestion level filter: low, medium, high",
    ),
    min_intensity: float = Query(
        0.0,
        ge=0.0,
        le=1.0,
        description="Minimum intensity (0.0–1.0). Use 0.0 for all points; high congestion typically scores ≥0.8",
    ),
    limit: int = Query(
        500,
        ge=1,
        le=1000,
        description="Maximum number of heatmap points to return",
    ),
    db: Session = Depends(get_db),
) -> HeatmapResponse:
    """
    Get traffic heatmap data for Google Maps HeatmapLayer visualization.

    Returns a list of heatmap points with lat/lng, intensity (0–1), speed, and vehicle count.
    Results are cached for 120 seconds.
    """
    if congestion_filter is not None and congestion_filter.lower() not in ALLOWED_CONGESTION_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="congestion_filter must be low, medium, or high",
        )

    congestion_filter = congestion_filter.lower() if congestion_filter else None

    cache_key = f"heatmap:v2:{hours}:{congestion_filter}:{min_intensity}:{limit}"
    cached = await get_cache(cache_key)
    if cached:
        logger.debug("Heatmap cache HIT for key=%s", cache_key)
        return HeatmapResponse(**cached)

    response = get_heatmap_data(
        hours=hours,
        congestion_filter=congestion_filter,
        min_intensity=min_intensity,
        limit=limit,
        db=db,
    )

    await set_cache(cache_key, response.model_dump(mode="json"), ttl=_HEATMAP_CACHE_TTL)

    logger.info(
        "Heatmap endpoint returned %s points (hours=%s, filter=%s, min_intensity=%s, limit=%s)",
        response.total_points,
        hours,
        congestion_filter,
        min_intensity,
        limit,
    )

    return response


@router.get(
    "/traffic/heatmap/hotspots",
    status_code=status.HTTP_200_OK,
)
async def get_heatmap_hotspots(
    limit: int = Query(10, ge=1, le=50, description="Number of top hotspots to return"),
    severity: Optional[str] = Query(
        None,
        description="Filter by severity tier: critical (≥0.8), high (0.6–0.79), moderate (0.4–0.59), low (<0.4)",
    ),
    db: Session = Depends(get_db),
) -> dict:
    """
    Get the top N highest congestion hotspot locations across India right now.

    Returns the most intense traffic points from the last hour, ranked by intensity.
    Each hotspot includes a `severity` field (critical / high / moderate / low).
    Pass `severity=critical` (or high/moderate/low) to filter by tier.
    """
    if severity is not None and severity not in ALLOWED_SEVERITY_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"severity must be one of: {', '.join(sorted(ALLOWED_SEVERITY_FILTERS))}",
        )
    result = get_india_hotspots(db, limit=limit, severity=severity)
    logger.info(
        "Hotspots endpoint returned %s / %s points (severity=%s)",
        result["returned"], result["total_evaluated"], severity,
    )
    return result


@router.get(
    "/traffic/heatmap/summary",
    status_code=status.HTTP_200_OK,
)
async def get_heatmap_summary(db: Session = Depends(get_db)) -> dict:
    """
    Get India-wide traffic summary for the heatmap dashboard.

    Computes counts and intensity statistics from the most recent traffic records
    across all monitored locations in India.
    """
    lookback_time = datetime.now(timezone.utc) - timedelta(hours=1)

    latest_subquery = (
        select(
            TrafficRecord.location.label("location"),
            func.max(TrafficRecord.created_at).label("latest_created_at"),
        )
        .where(TrafficRecord.created_at > lookback_time)
        .group_by(TrafficRecord.location)
        .subquery()
    )

    statement = (
        select(TrafficRecord)
        .join(
            latest_subquery,
            (TrafficRecord.location == latest_subquery.c.location)
            & (TrafficRecord.created_at == latest_subquery.c.latest_created_at),
        )
    )

    records = db.execute(statement).scalars().all()

    intensities = []
    for record in records:
        intensity = calculate_intensity(
            vehicle_count=record.vehicle_count,
            average_speed=record.average_speed or 0.0,
            congestion_level=record.congestion_level or "low",
        )
        intensities.append((intensity, record))

    total_locations = len(intensities)
    high_congestion_locations = len([i for i, _ in intensities if i > 0.7])
    medium_congestion_locations = len([i for i, _ in intensities if 0.4 <= i <= 0.7])
    low_congestion_locations = len([i for i, _ in intensities if i < 0.4])
    average_intensity = (
        round(sum(i for i, _ in intensities) / total_locations, 2)
        if total_locations > 0
        else 0.0
    )

    sorted_intensities = sorted(intensities, key=lambda pair: pair[0])
    worst_location = None
    best_location = None
    worst_intensity = 0.0
    best_intensity = 0.0

    if sorted_intensities:
        best_intensity, best_record = sorted_intensities[0]
        worst_intensity, worst_record = sorted_intensities[-1]
        best_location = best_record.location
        worst_location = worst_record.location

    last_updated = None
    if records:
        last_updated = max(record.created_at for record in records)

    logger.info(
        "Heatmap summary computed: total=%s, high=%s, medium=%s, low=%s",
        total_locations,
        high_congestion_locations,
        medium_congestion_locations,
        low_congestion_locations,
    )

    from app.services.heatmap_service import COVERAGE_AREA
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")

    def _to_ist(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_IST).isoformat()

    return {
        "coverage_area": COVERAGE_AREA,
        "total_monitored_locations": total_locations,
        "congestion_breakdown": {
            "high":   high_congestion_locations,
            "medium": medium_congestion_locations,
            "low":    low_congestion_locations,
        },
        "average_intensity": average_intensity,
        "worst_location": worst_location,
        "worst_intensity": worst_intensity,
        "best_location": best_location,
        "best_intensity": best_intensity,
        "last_updated": _to_ist(last_updated),
        "generated_at": datetime.now(timezone.utc).astimezone(_IST).isoformat(),
    }


# SECTION A — curl test commands
# Get full heatmap (last 1 hour)
# curl http://localhost:8000/traffic/heatmap
#
# Get only high congestion last 2 hours
# curl "http://localhost:8000/traffic/heatmap?hours=2&congestion_filter=high"
#
# Get high intensity points only
# curl "http://localhost:8000/traffic/heatmap?min_intensity=0.7"
#
# Get hotspots
# curl http://localhost:8000/traffic/heatmap/hotspots
#
# Get city summary
# curl http://localhost:8000/traffic/heatmap/summary


# SECTION B — Google Maps HeatmapLayer integration
# const response = await fetch("http://localhost:8000/traffic/heatmap")
# const data = await response.json()
# const heatmapData = data.points.map(point => ({
#   location: new google.maps.LatLng(point.latitude, point.longitude),
#   weight: point.intensity
# }))
# const heatmapLayer = new google.maps.visualization.HeatmapLayer({
#   data: heatmapData,
#   map: map,
#   radius: 30,
#   opacity: 0.8,
#   gradient: [
#     "rgba(0, 255, 0, 0)",
#     "rgba(0, 255, 0, 1)",
#     "rgba(255, 255, 0, 1)",
#     "rgba(255, 0, 0, 1)"
#   ]
# })
#
# setInterval(async () => {
#   const newData = await fetch("http://localhost:8000/traffic/heatmap")
#   const newJson = await newData.json()
#   heatmapLayer.setData(newJson.points.map(p => ({
#     location: new google.maps.LatLng(p.latitude, p.longitude),
#     weight: p.intensity
#   })))
# }, 60000)
