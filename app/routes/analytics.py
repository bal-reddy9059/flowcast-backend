from collections import Counter
from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
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

_ANALYTICS_CACHE_TTL_SECONDS = 10.0
_analytics_cache: dict[tuple, tuple[float, Any]] = {}
_analytics_locks: dict[tuple, threading.Lock] = {}
_analytics_lock_guard = threading.Lock()


def _cached(key: tuple, loader: Callable[[], Any]) -> Any:
    """Cache and coalesce duplicate dashboard queries for a short interval."""
    now = time.monotonic()
    cached = _analytics_cache.get(key)
    if cached and now - cached[0] < _ANALYTICS_CACHE_TTL_SECONDS:
        return cached[1]

    with _analytics_lock_guard:
        key_lock = _analytics_locks.setdefault(key, threading.Lock())
    with key_lock:
        now = time.monotonic()
        cached = _analytics_cache.get(key)
        if cached and now - cached[0] < _ANALYTICS_CACHE_TTL_SECONDS:
            return cached[1]
        value = loader()
        _analytics_cache[key] = (time.monotonic(), value)
        return value

# Cities shown on the multi-city health board (display name → INDIA_LOCATIONS city)
_CITY_MAP = [
    {"city": "Bengaluru", "loc_city": "Bengaluru"},
    {"city": "Chennai",   "loc_city": "Chennai"},
    {"city": "Pune",      "loc_city": "Pune"},
    {"city": "Hyderabad", "loc_city": "Hyderabad"},
    {"city": "Ahmedabad", "loc_city": "Ahmedabad"},
    {"city": "Mumbai",    "loc_city": "Mumbai"},
    {"city": "Delhi",     "loc_city": "New Delhi"},
    {"city": "Kolkata",   "loc_city": "Kolkata"},
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
    def load_snapshot() -> dict:
        result = get_network_snapshot(db, hours=hours)
        result["requested_period_hours"] = hours
        result["used_fallback_window"] = False
        if result.get("total_locations_observed", 0) == 0 and hours < 6:
            result = get_network_snapshot(db, hours=6)
            result["requested_period_hours"] = hours
            result["used_fallback_window"] = True
        return result

    return _cached(("snapshot", hours), load_snapshot)


@router.get("/location")
def location_summary(
    location: str = Query(..., description="Location name to query"),
    hours: int = Query(1, ge=1, le=24),
    db: Session = Depends(get_db),
):
    """Aggregated stats + active incidents for a specific location."""
    return _cached(
        ("location", location.strip().lower(), hours),
        lambda: get_location_summary(db, location=location, hours=hours),
    )


@router.get("/health")
def city_health_score(db: Session = Depends(get_db)):
    """Real-time city-wide traffic health score (0–100) with grade and congestion breakdown.

    Score formula: 100 − (high_pct × 0.7 + medium_pct × 0.25)
    Grades: A ≥ 80, B ≥ 65, C ≥ 50, D ≥ 35, F < 35
    """
    return _cached(("health",), lambda: get_city_health(db))


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
    return _cached(
        ("calendar", location.strip().lower(), days),
        lambda: get_congestion_calendar(db, location=location, days=days),
    )


@router.get("/timelapse")
def congestion_timelapse(
    hours: int = Query(24, ge=1, le=72, description="Hours of history to include"),
    db: Session = Depends(get_db),
):
    """Hourly congestion distribution snapshots for the last N hours.

    Returns one snapshot per hour with high/medium/low percentages and a health
    score. Ideal for animated timeline charts on a frontend dashboard.
    """
    return _cached(
        ("timelapse", hours),
        lambda: get_congestion_timelapse(db, hours=hours),
    )


@router.get("/trend")
def congestion_trend(
    location: str = Query(..., description="Location name to analyse"),
    intervals: int = Query(6, ge=2, le=24, description="Number of hourly buckets"),
    db: Session = Depends(get_db),
):
    """Hourly congestion trend for a location (for chart rendering)."""
    return _cached(
        ("trend", location.strip().lower(), intervals),
        lambda: get_congestion_trend(db, location=location, intervals=intervals),
    )


@router.get("/congestion-trend", include_in_schema=False)
@router.get("/trends")
def congestion_trends(
    hours: int = Query(24, ge=1, le=72, description="Look-back window in hours"),
    db: Session = Depends(get_db),
):
    """Network-wide hourly congestion data points for the last N hours.

    Returns `{ data_points: [{ hour, congestion_level, vehicle_count, has_data }] }` —
    compatible with the frontend trend chart.  congestion_level is 0–1 when data
    exists, otherwise null.
    """
    raw = _cached(
        ("timelapse", hours),
        lambda: get_congestion_timelapse(db, hours=hours),
    )
    data_points = []
    for snap in raw["snapshots"]:
        hour = int(snap["hour_label"].split(":")[0])
        if snap["has_data"]:
            congestion_level = round(
                (snap["high_pct"] * 0.7 + snap["medium_pct"] * 0.25) / 100, 3
            )
        else:
            congestion_level = None
        data_points.append({
            "hour": hour,
            "congestion_level": congestion_level,
            "vehicle_count": snap["total_records"],
            "has_data": snap["has_data"],
        })
    return {"hours_analysed": hours, "data_points": data_points}


@router.get("/city-health")
def city_health_multi(db: Session = Depends(get_db)):
    """Per-city traffic health scores (0–100) with trend direction.

    Uses one-hour data when fresh and a six-hour real-data fallback when the
    collector has not produced a sample in the latest hour.
    """
    from app.services.india_locations import INDIA_LOCATIONS

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    prev_since = since - timedelta(hours=1)
    fallback_since = datetime.now(timezone.utc) - timedelta(hours=6)

    # Build loc_city → list[location_name] lookup
    city_locs: dict[str, list[str]] = {}
    for loc in INDIA_LOCATIONS:
        city_locs.setdefault(loc["city"], []).append(loc["name"])

    all_names = {
        name
        for entry in _CITY_MAP
        for name in city_locs.get(entry["loc_city"], [])
    }
    rows = (
        db.query(
            TrafficRecord.location,
            TrafficRecord.congestion_level,
            TrafficRecord.timestamp,
        )
        .filter(
            TrafficRecord.location.in_(all_names),
            TrafficRecord.timestamp >= fallback_since,
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )
    rows_by_location: dict[str, list] = {}
    for row in rows:
        rows_by_location.setdefault(row.location, []).append(row)

    cities = []
    for entry in _CITY_MAP:
        loc_names = city_locs.get(entry["loc_city"], [])
        curr_score = None
        trend = 0.0
        city_rows = [
            row
            for name in loc_names
            for row in rows_by_location.get(name, [])
        ]
        current = [row for row in city_rows if row.timestamp >= since]
        period_hours = 1
        used_fallback = False
        if not current:
            current = city_rows
            period_hours = 6
            used_fallback = bool(current)

        curr_score = _health_score(current)
        if curr_score is not None and not used_fallback:
            previous = [
                row for row in city_rows
                if prev_since <= row.timestamp < since
            ]
            prev_score = _health_score(previous)
            if prev_score is not None:
                trend = round(curr_score - prev_score, 1)

        latest = max((row.timestamp for row in current), default=None)

        cities.append({
            "city": entry["city"],
            "score": curr_score,
            "trend": trend,
            "has_data": curr_score is not None,
            "period_hours": period_hours,
            "used_fallback_window": used_fallback,
            "latest_sample_at": latest.isoformat() if latest else None,
        })

    return {"cities": cities, "updated_at": datetime.now(timezone.utc).isoformat()}
