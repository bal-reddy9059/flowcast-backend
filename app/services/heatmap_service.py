"""
Heatmap service for traffic heatmap data generation.

Provides intensity calculation and PostgreSQL queries for heatmap points
used by the frontend Google Maps heatmap layer.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import logging
from typing import List, Optional

from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from app.models.predictor import TrafficRecord
from app.schemas.heatmap import HeatmapPoint, HeatmapResponse

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
COVERAGE_AREA = "India"

# Typical vehicle estimate ceiling from TomTom/HERE collectors
_MAX_VEHICLES = 1200.0
_FREE_FLOW_SPEED = 60.0


def _to_ist_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()


def calculate_intensity(
    vehicle_count: int,
    average_speed: float,
    congestion_level: str,
) -> float:
    """
    Calculate a normalised intensity score between 0.0 and 1.0.

    High congestion + slow traffic can reach 1.0 so filters like
    min_intensity=1 still return the worst hotspots.
    """
    level = (congestion_level or "medium").lower()
    base = {"low": 0.25, "medium": 0.55, "high": 0.80}.get(level, 0.50)

    # Lower speed → higher boost (up to +0.20)
    if average_speed and average_speed > 0:
        speed_boost = max(0.0, min(0.20, (1.0 - average_speed / _FREE_FLOW_SPEED) * 0.20))
    else:
        speed_boost = 0.20

    # More vehicles → higher boost (up to +0.15)
    vehicle_boost = max(0.0, min(0.15, (max(vehicle_count, 0) / _MAX_VEHICLES) * 0.15))

    intensity = base + speed_boost + vehicle_boost
    return round(min(max(intensity, 0.0), 1.0), 2)


def _latest_records_query(lookback_time: datetime):
    """Build the subquery that picks the latest record per location."""
    latest_subquery = (
        select(
            TrafficRecord.location.label("location"),
            func.max(TrafficRecord.created_at).label("latest_created_at"),
        )
        .where(
            or_(
                TrafficRecord.created_at > lookback_time,
                TrafficRecord.timestamp > lookback_time,
            ),
            TrafficRecord.latitude.isnot(None),
            TrafficRecord.longitude.isnot(None),
        )
        .group_by(TrafficRecord.location)
        .subquery()
    )
    return (
        select(TrafficRecord)
        .join(
            latest_subquery,
            (TrafficRecord.location == latest_subquery.c.location)
            & (TrafficRecord.created_at == latest_subquery.c.latest_created_at),
        )
        .where(
            TrafficRecord.latitude.isnot(None),
            TrafficRecord.longitude.isnot(None),
        )
        .order_by(TrafficRecord.created_at.desc())
    )


def get_heatmap_data(
    hours: int,
    congestion_filter: Optional[str],
    min_intensity: float,
    limit: int,
    db: Session,
) -> HeatmapResponse:
    """
    Retrieve heatmap points within a lookback window.

    Filters traffic records by age, optional congestion level, and intensity threshold.
    Returns top points ordered by intensity.
    """
    lookback_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = _latest_records_query(lookback_time)

    if congestion_filter is not None:
        query = query.where(TrafficRecord.congestion_level == congestion_filter.lower())

    records = db.execute(query).scalars().all()

    points: List[HeatmapPoint] = []
    for record in records:
        if record.latitude is None or record.longitude is None:
            continue

        intensity = calculate_intensity(
            vehicle_count=record.vehicle_count or 0,
            average_speed=float(record.average_speed or 0.0),
            congestion_level=record.congestion_level or "medium",
        )
        if intensity < min_intensity:
            continue

        points.append(
            HeatmapPoint(
                latitude=float(record.latitude),
                longitude=float(record.longitude),
                intensity=intensity,
                congestion_level=record.congestion_level or "medium",
                location=record.location,
                vehicle_count=int(record.vehicle_count or 0),
                average_speed=round(float(record.average_speed or 0.0), 1),
                timestamp=_to_ist_iso(record.timestamp or record.created_at) or _to_ist_iso(datetime.now(timezone.utc)),
            )
        )

    points.sort(key=lambda p: p.intensity, reverse=True)
    points = points[:limit]

    total_points = len(points)
    high_congestion_count = sum(
        1 for p in points if p.congestion_level == "high" or p.intensity >= 0.7
    )
    average_intensity = (
        round(sum(p.intensity for p in points) / total_points, 2)
        if total_points > 0 else 0.0
    )

    logger.info(
        "Heatmap data generated: hours=%s, filter=%s, min_intensity=%s, limit=%s, points=%s (scanned=%s)",
        hours, congestion_filter, min_intensity, limit, total_points, len(records),
    )

    return HeatmapResponse(
        success=True,
        points=points,
        total_points=total_points,
        high_congestion_count=high_congestion_count,
        average_intensity=average_intensity,
        coverage_area=COVERAGE_AREA,
        generated_at=_to_ist_iso(datetime.now(timezone.utc)),
        hours_lookback=hours,
        congestion_filter=congestion_filter,
        min_intensity=min_intensity,
    )


def classify_severity(intensity: float) -> str:
    """Map a normalised intensity score to a 4-tier severity label."""
    if intensity >= 0.8:
        return "critical"
    if intensity >= 0.6:
        return "high"
    if intensity >= 0.4:
        return "moderate"
    return "low"


def get_india_hotspots(db: Session, limit: int = 10, severity: Optional[str] = None) -> dict:
    """
    Get top N highest intensity traffic hotspots across India.

    Uses the last hour of traffic records and returns the most congested locations.
    Optionally filter by severity tier: critical, high, moderate, low.
    """
    lookback_time = datetime.now(timezone.utc) - timedelta(hours=1)
    query = _latest_records_query(lookback_time)
    records = db.execute(query).scalars().all()

    hotspot_list = []
    for record in records:
        if record.latitude is None or record.longitude is None:
            continue
        intensity = calculate_intensity(
            vehicle_count=record.vehicle_count or 0,
            average_speed=float(record.average_speed or 0.0),
            congestion_level=record.congestion_level or "medium",
        )
        sev = classify_severity(intensity)
        hotspot_list.append({
            "latitude": float(record.latitude),
            "longitude": float(record.longitude),
            "intensity": intensity,
            "severity": sev,
            "congestion_level": record.congestion_level,
            "location": record.location,
            "vehicle_count": int(record.vehicle_count or 0),
            "average_speed": round(float(record.average_speed or 0.0), 1),
            "timestamp": _to_ist_iso(record.timestamp or record.created_at),
        })

    hotspot_list.sort(key=lambda p: p["intensity"], reverse=True)

    if severity:
        hotspot_list = [h for h in hotspot_list if h["severity"] == severity]

    hotspots = hotspot_list[:limit]

    logger.info(
        "Hotspots fetched: %s locations evaluated, severity_filter=%s, returning %s",
        len(records), severity, len(hotspots),
    )
    return {
        "success": True,
        "coverage_area": COVERAGE_AREA,
        "total_evaluated": len(records),
        "severity_filter": severity,
        "returned": len(hotspots),
        "hotspots": hotspots,
        "generated_at": _to_ist_iso(datetime.now(timezone.utc)),
    }
