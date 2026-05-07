"""
realtime.py
-----------
Service layer for real-time traffic analysis.
Provides congestion scoring, location summaries, and trend detection
on top of the data stored in the traffic_records table.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import Optional

from app.models.predictor import TrafficRecord, PredictionResult, Incident


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
    since = datetime.utcnow() - timedelta(hours=hours)

    records = (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.location.ilike(f"%{location}%"),
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
            Incident.location.ilike(f"%{location}%"),
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
    since = datetime.utcnow() - timedelta(hours=hours)

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
        "snapshot_time": datetime.utcnow().isoformat(),
        "period_hours": hours,
        "total_locations_observed": len(locations),
        "active_incidents": active_incidents,
        "congestion_distribution": congestion_counts,
        "locations": locations,
    }


# ─── Trend detection ──────────────────────────────────────────────────────────

def get_congestion_trend(db: Session, location: str, intervals: int = 6) -> dict:
    """
    Splits the last 6 hours into hourly buckets and returns
    vehicle count / speed per bucket for trend visualisation.
    """
    buckets = []
    now = datetime.utcnow()

    for i in range(intervals, 0, -1):
        bucket_start = now - timedelta(hours=i)
        bucket_end   = now - timedelta(hours=i - 1)

        rows = (
            db.query(
                func.avg(TrafficRecord.vehicle_count).label("avg_v"),
                func.avg(TrafficRecord.average_speed).label("avg_s"),
            )
            .filter(
                TrafficRecord.location.ilike(f"%{location}%"),
                TrafficRecord.timestamp >= bucket_start,
                TrafficRecord.timestamp < bucket_end,
            )
            .first()
        )

        avg_v = round(float(rows.avg_v), 1) if rows and rows.avg_v else 0
        avg_s = round(float(rows.avg_s), 1) if rows and rows.avg_s else None

        buckets.append({
            "hour_start": bucket_start.strftime("%H:%M"),
            "avg_vehicle_count": avg_v,
            "avg_speed": avg_s,
            "congestion_level": classify_congestion(int(avg_v), avg_s),
        })

    return {
        "location": location,
        "intervals_hours": intervals,
        "trend": buckets,
    }
