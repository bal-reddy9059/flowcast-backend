from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import TrafficRecord
from app.services.realtime import (
    get_city_health,
    get_congestion_calendar,
    get_congestion_timelapse,
    get_congestion_trend,
    get_location_summary,
    get_network_snapshot,
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])

# City display name → INDIA_LOCATIONS city name mapping
_CITY_MAP = [
    {"city": "Bengaluru", "loc_city": "Bengaluru", "score": 92, "trend":  3},
    {"city": "Chennai",   "loc_city": "Chennai",   "score": 87, "trend": -1},
    {"city": "Pune",      "loc_city": "Pune",      "score": 84, "trend":  2},
    {"city": "Hyderabad", "loc_city": "Hyderabad", "score": 79, "trend": -2},
    {"city": "Ahmedabad", "loc_city": "Ahmedabad", "score": 76, "trend":  1},
    {"city": "Mumbai",    "loc_city": "Mumbai",    "score": 68, "trend": -4},
    {"city": "Delhi",     "loc_city": "New Delhi", "score": 61, "trend": -3},
    {"city": "Kolkata",   "loc_city": "Kolkata",   "score": 73, "trend":  2},
]


def _health_score(records: list) -> float | None:
    if not records:
        return None
    c = Counter(r.congestion_level for r in records)
    t = len(records)
    return round(
        max(0.0, min(100.0,
            100 - c.get("high", 0) / t * 100 * 0.7
                - c.get("medium", 0) / t * 100 * 0.25
        )), 1
    )


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


@router.get("/trends")
def congestion_trends(
    hours: int = Query(24, ge=1, le=72, description="Look-back window in hours"),
    db: Session = Depends(get_db),
):
    """Network-wide hourly congestion data points for the last N hours.

    Returns `{ data_points: [{ hour, congestion_level, vehicle_count }] }` —
    compatible with the frontend trend chart.  congestion_level is 0–1.
    """
    raw = get_congestion_timelapse(db, hours=hours)
    data_points = []
    for snap in raw["snapshots"]:
        hour = int(snap["hour_label"].split(":")[0])
        congestion_level = round(
            (snap["high_pct"] * 0.7 + snap["medium_pct"] * 0.25) / 100, 3
        )
        data_points.append({
            "hour": hour,
            "congestion_level": congestion_level,
            "vehicle_count": snap["total_records"],
        })
    return {"hours_analysed": hours, "data_points": data_points}


@router.get("/city-health")
def city_health_multi(db: Session = Depends(get_db)):
    """Per-city traffic health scores (0–100) with trend direction.

    Uses real DB data where available; falls back to static baselines for
    cities with no recent records.
    """
    from app.services.india_locations import INDIA_LOCATIONS

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    prev_since = since - timedelta(hours=1)

    # Build loc_city → list[location_name] lookup
    city_locs: dict[str, list[str]] = {}
    for loc in INDIA_LOCATIONS:
        city_locs.setdefault(loc["city"], []).append(loc["name"])

    cities = []
    for entry in _CITY_MAP:
        loc_names = city_locs.get(entry["loc_city"], [])
        curr_score = None
        trend = entry["trend"]

        if loc_names:
            filters = [TrafficRecord.location.ilike(f"%{n}%") for n in loc_names]
            current = (
                db.query(TrafficRecord)
                .filter(
                    or_(*filters),
                    TrafficRecord.timestamp >= since,
                    TrafficRecord.congestion_level.isnot(None),
                )
                .all()
            )
            curr_score = _health_score(current)

            if curr_score is not None:
                previous = (
                    db.query(TrafficRecord)
                    .filter(
                        or_(*filters),
                        TrafficRecord.timestamp >= prev_since,
                        TrafficRecord.timestamp < since,
                        TrafficRecord.congestion_level.isnot(None),
                    )
                    .all()
                )
                prev_score = _health_score(previous)
                if prev_score is not None:
                    trend = round(curr_score - prev_score, 1)

        cities.append({
            "city": entry["city"],
            "score": curr_score if curr_score is not None else entry["score"],
            "trend": trend,
        })

    return {"cities": cities, "updated_at": datetime.now(timezone.utc).isoformat()}
