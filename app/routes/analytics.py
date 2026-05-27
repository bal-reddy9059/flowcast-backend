from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.realtime import (
    get_city_health,
    get_congestion_calendar,
    get_congestion_timelapse,
    get_congestion_trend,
    get_location_summary,
    get_network_snapshot,
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


@router.get("/health")
def city_health_score(db: Session = Depends(get_db)):
    """Real-time city-wide traffic health score (0–100) with grade and congestion breakdown.

    Score formula: 100 − (high_pct × 0.7 + medium_pct × 0.25)
    Grades: A ≥ 80, B ≥ 65, C ≥ 50, D ≥ 35, F < 35
    """
    return get_city_health(db)


@router.get("/calendar")
def congestion_calendar(
    location: str = Query(..., description="Location name to analyse"),
    days: int = Query(30, ge=7, le=90, description="Days of history to include"),
    db: Session = Depends(get_db),
):
    """Hour-of-day × day-of-week congestion pattern matrix for a location.

    Useful for charting weekly traffic patterns and identifying rush-hour peaks.
    Returns a 7 × 24 grid with the dominant congestion level per slot.
    """
    return get_congestion_calendar(db, location=location, days=days)


@router.get("/timelapse")
def congestion_timelapse(
    hours: int = Query(24, ge=1, le=72, description="Hours of history to include"),
    db: Session = Depends(get_db),
):
    """Hourly congestion distribution snapshots for the last N hours.

    Returns one snapshot per hour with high/medium/low percentages and a health
    score. Ideal for animated timeline charts on a frontend dashboard.
    """
    return get_congestion_timelapse(db, hours=hours)


@router.get("/trend")
def congestion_trend(
    location: str = Query(..., description="Location name to analyse"),
    intervals: int = Query(6, ge=2, le=24, description="Number of hourly buckets"),
    db: Session = Depends(get_db),
):
    """Hourly congestion trend for a location (for chart rendering)."""
    return get_congestion_trend(db, location=location, intervals=intervals)
