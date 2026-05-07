from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.realtime import (
    get_location_summary,
    get_network_snapshot,
    get_congestion_trend,
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/snapshot")
def network_snapshot(
    hours: int = Query(1, ge=1, le=24, description="Look-back window in hours"),
    db: Session = Depends(get_db),
):
    """Network-wide congestion snapshot across all observed locations."""
    return get_network_snapshot(db, hours=hours)


@router.get("/location")
def location_summary(
    location: str = Query(..., description="Location name to query"),
    hours: int = Query(1, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """Aggregated stats + active incidents for a specific location."""
    return get_location_summary(db, location=location, hours=hours)


@router.get("/trend")
def congestion_trend(
    location: str = Query(..., description="Location name to analyse"),
    intervals: int = Query(6, ge=2, le=24, description="Number of hourly buckets"),
    db: Session = Depends(get_db),
):
    """Hourly congestion trend for a location (for chart rendering)."""
    return get_congestion_trend(db, location=location, intervals=intervals)
