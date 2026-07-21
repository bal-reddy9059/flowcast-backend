"""
realtime.py
-----------
Service layer for real-time traffic analysis.
Provides congestion scoring, location summaries, and trend detection
on top of the data stored in the traffic_records table.
"""

from collections import Counter

from sqlalchemy import case, func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.predictor import TrafficRecord, Incident
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
    """Return 'low', 'medium', or 'high' based on vehicle count & speed.

    Prefer stored TrafficRecord.congestion_level when reading analytics —
    collectors already classify via TomTom speed ratio. This helper is a
    fallback for rows that never had a level written.
    """
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


def _dominant_level(levels: list[str], vehicle_count: int = 0, avg_speed: Optional[float] = None) -> str:
    """Prefer stored congestion levels; fall back to classify_congestion."""
    known = [lvl for lvl in levels if lvl in ("low", "medium", "high")]
    if known:
        return Counter(known).most_common(1)[0][0]
    return classify_congestion(vehicle_count, avg_speed)


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

    congestion = _dominant_level(
        [r.congestion_level for r in records],
        int(avg_vehicles),
        avg_speed,
    )

    incidents = (
        db.query(Incident)
        .filter(
            _location_filter(Incident.location, location),
            Incident.is_active == True,
            Incident.reported_at >= since,
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

    records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.timestamp >= since)
        .all()
    )

    by_location: dict[str, list[TrafficRecord]] = {}
    for r in records:
        by_location.setdefault(r.location, []).append(r)

    locations = []
    congestion_counts = {"low": 0, "medium": 0, "high": 0, "unknown": 0}

    for loc_name, rows in by_location.items():
        avg_vehicles = sum(r.vehicle_count for r in rows) / len(rows)
        speeds = [r.average_speed for r in rows if r.average_speed is not None]
        avg_speed = sum(speeds) / len(speeds) if speeds else None
        level = _dominant_level(
            [r.congestion_level for r in rows],
            int(avg_vehicles),
            avg_speed,
        )
        congestion_counts[level] = congestion_counts.get(level, 0) + 1
        locations.append({
            "location": loc_name,
            "record_count": len(rows),
            "avg_vehicle_count": round(float(avg_vehicles), 1),
            "avg_speed": round(float(avg_speed), 1) if avg_speed is not None else None,
            "congestion_level": level,
        })

    locations.sort(key=lambda x: x["avg_vehicle_count"], reverse=True)

    # Scope to the same look-back window — global is_active alone balloons into
    # thousands of stale TomTom/HERE rows and misrepresents the snapshot period.
    active_incidents = (
        db.query(Incident)
        .filter(
            Incident.is_active == True,
            Incident.reported_at >= since,
        )
        .count()
    )

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
    now = datetime.now(timezone.utc)
    # Align to clean clock-hour boundaries so labels read "04:00", "05:00", etc.
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    range_start = current_hour - timedelta(hours=intervals)
    bucket_expr = func.date_trunc(
        "hour", func.timezone("UTC", TrafficRecord.timestamp)
    )
    rows = (
        db.query(
            bucket_expr.label("bucket"),
            func.count(TrafficRecord.id).label("record_count"),
            func.avg(TrafficRecord.vehicle_count).label("avg_vehicles"),
            func.avg(TrafficRecord.average_speed).label("avg_speed"),
            func.sum(case((TrafficRecord.congestion_level == "low", 1), else_=0)).label("low_count"),
            func.sum(case((TrafficRecord.congestion_level == "medium", 1), else_=0)).label("medium_count"),
            func.sum(case((TrafficRecord.congestion_level == "high", 1), else_=0)).label("high_count"),
        )
        .filter(
            _location_filter(TrafficRecord.location, location),
            TrafficRecord.timestamp >= range_start,
            TrafficRecord.timestamp < current_hour,
        )
        .group_by(bucket_expr)
        .all()
    )
    by_bucket = {}
    for row in rows:
        bucket = row.bucket
        if bucket and bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        by_bucket[bucket] = row

    buckets = []
    for i in range(intervals, 0, -1):
        bucket_start = current_hour - timedelta(hours=i)
        row = by_bucket.get(bucket_start)
        has_data = row is not None and int(row.record_count or 0) > 0
        if has_data:
            avg_v = round(float(row.avg_vehicles or 0), 1)
            avg_s = round(float(row.avg_speed or 0), 1)
            level_counts = {
                "low": int(row.low_count or 0),
                "medium": int(row.medium_count or 0),
                "high": int(row.high_count or 0),
            }
            level = max(level_counts, key=level_counts.get)
            if not any(level_counts.values()):
                level = classify_congestion(int(avg_v), avg_s if avg_s else None)
        else:
            avg_v = 0.0
            avg_s = 0.0
            level = "unknown"

        buckets.append({
            "hour_start": bucket_start.strftime("%H:00"),
            "has_data": has_data,
            "avg_vehicle_count": avg_v,
            "avg_speed": avg_s,
            "congestion_level": level,
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
            TrafficRecord.timestamp >= since,
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )

    # matrix[day_of_week][hour] = list of congestion level strings
    matrix: list[list[list[str]]] = [[[] for _ in range(24)] for _ in range(7)]
    for r in records:
        ts = r.timestamp or r.created_at
        if ts is None:
            continue
        dow = ts.weekday()   # 0 = Monday
        hour = ts.hour
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
    range_start = current_hour - timedelta(hours=hours)
    bucket_expr = func.date_trunc(
        "hour", func.timezone("UTC", TrafficRecord.timestamp)
    )
    rows = (
        db.query(
            bucket_expr.label("bucket"),
            func.count(TrafficRecord.id).label("total"),
            func.sum(case((TrafficRecord.congestion_level == "low", 1), else_=0)).label("low_count"),
            func.sum(case((TrafficRecord.congestion_level == "medium", 1), else_=0)).label("medium_count"),
            func.sum(case((TrafficRecord.congestion_level == "high", 1), else_=0)).label("high_count"),
        )
        .filter(
            TrafficRecord.timestamp >= range_start,
            TrafficRecord.timestamp < current_hour,
            TrafficRecord.congestion_level.isnot(None),
        )
        .group_by(bucket_expr)
        .all()
    )
    by_bucket = {}
    for row in rows:
        bucket = row.bucket
        if bucket and bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        by_bucket[bucket] = row

    snapshots = []

    for i in range(hours, 0, -1):
        bucket_start = current_hour - timedelta(hours=i)
        row = by_bucket.get(bucket_start)
        total = int(row.total or 0) if row else 0

        if total:
            counts = {
                "low": int(row.low_count or 0),
                "medium": int(row.medium_count or 0),
                "high": int(row.high_count or 0),
            }
            dominant = max(counts, key=counts.get)
            high_pct = round(counts["high"] / total * 100, 1)
            medium_pct = round(counts["medium"] / total * 100, 1)
            low_pct = round(counts["low"] / total * 100, 1)
            health = round(max(0.0, 100 - high_pct * 0.7 - medium_pct * 0.25), 1)
            has_data = True
        else:
            # No records — do not invent "perfect" health / low congestion
            dominant = "unknown"
            high_pct = medium_pct = low_pct = None
            health = None
            has_data = False

        snapshots.append({
            "hour_start": bucket_start.strftime("%Y-%m-%dT%H:00"),
            "hour_label": bucket_start.strftime("%H:00"),
            "total_records": total,
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
    """Compute city health from one-hour data, falling back to six real hours."""
    now = datetime.now(timezone.utc)
    period_hours = 1
    since = now - timedelta(hours=period_hours)
    records = (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.timestamp >= since,
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )

    used_fallback = False
    if not records:
        period_hours = 6
        used_fallback = True
        records = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.timestamp >= now - timedelta(hours=period_hours),
                TrafficRecord.congestion_level.isnot(None),
            )
            .all()
        )

    if not records:
        return {
            "score": None,
            "grade": None,
            "status": "No Data",
            "color": "gray",
            "breakdown": {"low_pct": 0.0, "medium_pct": 0.0, "high_pct": 0.0},
            "total_records": 0,
            "period_hours": period_hours,
            "used_fallback_window": used_fallback,
            "message": "No traffic data available in the last 6 hours",
            "updated_at": now.isoformat(),
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
        "period_hours": period_hours,
        "used_fallback_window": used_fallback,
        "updated_at": now.isoformat(),
    }
