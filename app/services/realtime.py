"""
realtime.py
-----------
Service layer for real-time traffic analysis.
Provides congestion scoring, location summaries, and trend detection
on top of the data stored in the traffic_records table.
"""

from collections import Counter

from sqlalchemy import func, desc
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.predictor import TrafficRecord, PredictionResult, Incident
from app.services.city_aliases import location_filter as _location_filter


# ─── Congestion helpers ────────────────────────────────────────────────────────

CONGESTION_THRESHOLDS = {
    "low":    (0,   30),   # vehicle_count range
    "medium": (31,  80),
    "high":   (81,  9999),
}

SPEED_THRESHOLDS = {
    "low":    (61,  999),   # avg speed km/h
    "medium": (26,  60),
    "high":   (0,   25),
}


def classify_congestion(vehicle_count: int, avg_speed: Optional[float] = None) -> str:
    """Return 'low', 'medium', or 'high' based on vehicle count & speed."""
    if avg_speed is not None:
        for level, (lo, hi) in SPEED_THRESHOLDS.items():
            if lo <= avg_speed <= hi:
                speed_level = level
                break
        else:
            speed_level = "low"
    else:
        speed_level = None

    for level, (lo, hi) in CONGESTION_THRESHOLDS.items():
        if lo <= vehicle_count <= hi:
            count_level = level
            break
    else:
        count_level = "low"

    # Worst of the two indicators wins
    priority = {"high": 2, "medium": 1, "low": 0}
    if speed_level and priority[speed_level] > priority[count_level]:
        return speed_level
    return count_level


# ─── Location summary ──────────────────────────────────────────────────────────

def get_location_summary(db: Session, location: str, hours: int = 1) -> dict:
    """
    Aggregate stats for a location over the last N hours.
    Returns avg speed, total vehicles, dominant congestion level,
    and active incidents.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    records = (
        db.query(TrafficRecord)
        .filter(
            _location_filter(TrafficRecord.location, location),
            TrafficRecord.timestamp >= since,
        )
        .all()
    )

    if not records:
        return {
            "location": location,
            "period_hours": hours,
            "record_count": 0,
            "avg_vehicle_count": 0,
            "avg_speed": None,
            "congestion_level": "unknown",
            "active_incidents": [],
        }

    total_vehicles = sum(r.vehicle_count for r in records)
    avg_vehicles = total_vehicles / len(records)

    speeds = [r.average_speed for r in records if r.average_speed is not None]
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    congestion = classify_congestion(int(avg_vehicles), avg_speed)

    incidents = (
        db.query(Incident)
        .filter(
            _location_filter(Incident.location, location),
            Incident.is_active == True,
        )
        .all()
    )

    return {
        "location": location,
        "period_hours": hours,
        "record_count": len(records),
        "avg_vehicle_count": round(avg_vehicles, 1),
        "avg_speed": round(avg_speed, 1) if avg_speed else None,
        "congestion_level": congestion,
        "active_incidents": [
            {
                "id": i.id,
                "type": i.incident_type,
                "severity": i.severity,
                "description": i.description,
            }
            for i in incidents
        ],
    }


# ─── Network-wide snapshot ────────────────────────────────────────────────────

def get_network_snapshot(db: Session, hours: int = 1) -> dict:
    """
    Returns congestion distribution across all locations
    recorded in the last N hours.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = (
        db.query(
            TrafficRecord.location,
            func.count(TrafficRecord.id).label("count"),
            func.avg(TrafficRecord.vehicle_count).label("avg_vehicles"),
            func.avg(TrafficRecord.average_speed).label("avg_speed"),
        )
        .filter(TrafficRecord.timestamp >= since)
        .group_by(TrafficRecord.location)
        .order_by(desc("avg_vehicles"))
        .all()
    )

    locations = []
    congestion_counts = {"low": 0, "medium": 0, "high": 0, "unknown": 0}

    for row in rows:
        level = classify_congestion(
            int(row.avg_vehicles or 0),
            float(row.avg_speed) if row.avg_speed else None,
        )
        congestion_counts[level] = congestion_counts.get(level, 0) + 1
        locations.append({
            "location": row.location,
            "record_count": row.count,
            "avg_vehicle_count": round(float(row.avg_vehicles or 0), 1),
            "avg_speed": round(float(row.avg_speed), 1) if row.avg_speed else None,
            "congestion_level": level,
        })

    active_incidents = db.query(Incident).filter(Incident.is_active == True).count()

    return {
        "snapshot_time": datetime.now(timezone.utc).isoformat(),
        "period_hours": hours,
        "total_locations_observed": len(locations),
        "active_incidents": active_incidents,
        "congestion_distribution": congestion_counts,
        "locations": locations,
    }


# ─── Trend detection ──────────────────────────────────────────────────────────

def get_congestion_trend(db: Session, location: str, intervals: int = 6) -> dict:
    """
    Splits the last N hours into hourly buckets and returns
    vehicle count / speed per bucket for trend visualisation.
    """
    buckets = []
    now = datetime.now(timezone.utc)
    # Align to clean clock-hour boundaries so labels read "04:00", "05:00", etc.
    current_hour = now.replace(minute=0, second=0, microsecond=0)

    for i in range(intervals, 0, -1):
        bucket_start = current_hour - timedelta(hours=i)
        bucket_end   = current_hour - timedelta(hours=i - 1)

        rows = (
            db.query(
                func.avg(TrafficRecord.vehicle_count).label("avg_v"),
                func.avg(TrafficRecord.average_speed).label("avg_s"),
            )
            .filter(
                _location_filter(TrafficRecord.location, location),
                TrafficRecord.timestamp >= bucket_start,
                TrafficRecord.timestamp < bucket_end,
            )
            .first()
        )

        has_data = bool(rows and rows.avg_v is not None)
        avg_v = round(float(rows.avg_v), 1) if has_data else 0.0
        avg_s = round(float(rows.avg_s), 1) if (has_data and rows.avg_s is not None) else 0.0

        buckets.append({
            "hour_start": bucket_start.strftime("%H:00"),
            "has_data": has_data,
            "avg_vehicle_count": avg_v,
            "avg_speed": avg_s,
            "congestion_level": classify_congestion(int(avg_v), avg_s) if has_data else "low",
        })

    return {
        "location": location,
        "intervals_hours": intervals,
        "trend": buckets,
    }


# ─── Congestion calendar ──────────────────────────────────────────────────────

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_LEVEL_SCORE = {"low": 0, "medium": 1, "high": 2}


def get_congestion_calendar(db: Session, location: str, days: int = 30) -> dict:
    """Build a 7-day × 24-hour congestion pattern matrix from historical data.

    Each cell shows the most common congestion level for that day/hour slot
    and how many records contributed to the calculation.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    records = (
        db.query(TrafficRecord)
        .filter(
            _location_filter(TrafficRecord.location, location),
            TrafficRecord.created_at >= since,
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )

    # matrix[day_of_week][hour] = list of congestion level strings
    matrix: list[list[list[str]]] = [[[] for _ in range(24)] for _ in range(7)]
    for r in records:
        dow = r.created_at.weekday()   # 0 = Monday
        hour = r.created_at.hour
        matrix[dow][hour].append(r.congestion_level)

    calendar = []
    for dow in range(7):
        hours = []
        for hour in range(24):
            bucket = matrix[dow][hour]
            if bucket:
                counts = Counter(bucket)
                dominant = counts.most_common(1)[0][0]
                avg_score = sum(_LEVEL_SCORE.get(l, 1) for l in bucket) / len(bucket)
            else:
                dominant = "unknown"
                avg_score = None
            hours.append({
                "hour": hour,
                "congestion_level": dominant,
                "avg_score": round(avg_score, 2) if avg_score is not None else None,
                "sample_count": len(bucket),
            })
        calendar.append({"day": _DAYS[dow], "day_index": dow, "hours": hours})

    return {
        "location": location,
        "days_analyzed": days,
        "total_records": len(records),
        "peak_hour": _find_peak(matrix),
        "calendar": calendar,
    }


def _find_peak(matrix: list) -> Optional[dict]:
    """Return the day+hour combination with the highest average congestion score."""
    best = None
    best_score = -1.0
    for dow, hours in enumerate(matrix):
        for hour, bucket in enumerate(hours):
            if not bucket:
                continue
            score = sum(_LEVEL_SCORE.get(l, 1) for l in bucket) / len(bucket)
            if score > best_score:
                best_score = score
                best = {"day": _DAYS[dow], "hour": hour, "avg_score": round(score, 2)}
    return best


# ─── Congestion time-lapse ────────────────────────────────────────────────────

def get_congestion_timelapse(db: Session, hours: int = 24) -> dict:
    """Return hourly congestion distribution snapshots for the last N hours.

    Each snapshot contains the congestion breakdown (low/medium/high percentages)
    and dominant level for that one-hour bucket. Useful for animated charts.
    """
    now = datetime.now(timezone.utc)
    # Align buckets to clean hour boundaries so labels read "07:00", "08:00", etc.
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    snapshots = []

    for i in range(hours, 0, -1):
        bucket_start = current_hour - timedelta(hours=i)
        bucket_end = current_hour - timedelta(hours=i - 1)

        records = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.timestamp >= bucket_start,
                TrafficRecord.timestamp < bucket_end,
                TrafficRecord.congestion_level.isnot(None),
            )
            .all()
        )

        if records:
            counts = Counter(r.congestion_level for r in records)
            total = len(records)
            dominant = counts.most_common(1)[0][0]
            high_pct = round(counts.get("high", 0) / total * 100, 1)
            medium_pct = round(counts.get("medium", 0) / total * 100, 1)
            low_pct = round(counts.get("low", 0) / total * 100, 1)
            health = round(max(0.0, 100 - high_pct * 0.7 - medium_pct * 0.25), 1)
            has_data = True
        else:
            # No records in this bucket — no congestion detected means perfect health
            dominant = "low"
            high_pct = medium_pct = 0.0
            low_pct = 100.0
            health = 100.0
            has_data = False

        snapshots.append({
            "hour_start": bucket_start.strftime("%Y-%m-%dT%H:00"),
            "hour_label": bucket_start.strftime("%H:00"),
            "total_records": len(records),
            "has_data": has_data,
            "dominant_congestion": dominant,
            "high_pct": high_pct,
            "medium_pct": medium_pct,
            "low_pct": low_pct,
            "health_score": health,
        })

    peak_snapshot = max(
        (s for s in snapshots if s["has_data"]),
        key=lambda s: s["high_pct"],
        default=None,
    )

    return {
        "hours_analysed": hours,
        "generated_at": now.isoformat(),
        "peak_congestion_snapshot": peak_snapshot,
        "snapshots": snapshots,
    }


# ─── City health score ────────────────────────────────────────────────────────

def get_city_health(db: Session) -> dict:
    """Compute a 0–100 traffic health score for the whole city based on the last hour."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    records = (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.timestamp >= since,
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )

    if not records:
        return {
            "score": 100,
            "grade": "A",
            "status": "No Data",
            "color": "gray",
            "breakdown": {"low_pct": 0.0, "medium_pct": 0.0, "high_pct": 0.0},
            "total_records": 0,
            "message": "No recent traffic data available",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    counts = Counter(r.congestion_level for r in records)
    total = len(records)
    high_pct = counts.get("high", 0) / total * 100
    medium_pct = counts.get("medium", 0) / total * 100
    low_pct = counts.get("low", 0) / total * 100

    score = round(max(0.0, min(100.0, 100 - high_pct * 0.7 - medium_pct * 0.25)), 1)

    if score >= 80:
        grade, traffic_status, color = "A", "Excellent", "green"
    elif score >= 65:
        grade, traffic_status, color = "B", "Good", "light-green"
    elif score >= 50:
        grade, traffic_status, color = "C", "Moderate", "yellow"
    elif score >= 35:
        grade, traffic_status, color = "D", "Poor", "orange"
    else:
        grade, traffic_status, color = "F", "Critical", "red"

    return {
        "score": score,
        "grade": grade,
        "status": traffic_status,
        "color": color,
        "breakdown": {
            "low_pct": round(low_pct, 1),
            "medium_pct": round(medium_pct, 1),
            "high_pct": round(high_pct, 1),
        },
        "total_records": total,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
