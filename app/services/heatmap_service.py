"""
Heatmap service for traffic heatmap data generation.

Provides intensity calculation and PostgreSQL queries for heatmap points
used by the frontend Google Maps heatmap layer.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.predictor import TrafficRecord
from app.schemas.heatmap import HeatmapPoint, HeatmapResponse

logger = logging.getLogger(__name__)

COVERAGE_AREA = "India"

# Max vehicle count used to normalise the vehicle score (district collector ceiling)
_MAX_VEHICLES = 3500.0
# Free-flow speed used to normalise the speed score
_FREE_FLOW_SPEED = 60.0


def calculate_intensity(
    vehicle_count: int,
    average_speed: float,
    congestion_level: str,
) -> float:
    """
    Calculate a normalised intensity score between 0.0 and 1.0.

    Uses continuous scaling so points with different speeds/counts produce
    distinct intensities (avoids all high-congestion points collapsing to 0.96).
    """
    # Speed: lower speed → higher intensity (linear, clamped 0-1)
    speed_score = max(0.0, min(1.0, 1.0 - (average_speed / _FREE_FLOW_SPEED))) if average_speed > 0 else 1.0

    # Vehicle count: more vehicles → higher intensity (linear, clamped 0-1)
    vehicle_score = max(0.0, min(1.0, vehicle_count / _MAX_VEHICLES)) if vehicle_count else 0.0

    # Congestion level: discrete weight
    congestion_score = {"low": 0.2, "medium": 0.5, "high": 1.0}.get(congestion_level, 0.5)

    intensity = speed_score * 0.4 + vehicle_score * 0.4 + congestion_score * 0.2
    return round(min(max(intensity, 0.0), 1.0), 2)


def _latest_records_query(lookback_time: datetime):
    """Build the subquery that picks the latest record per location."""
    latest_subquery = (
        select(
            TrafficRecord.location.label("location"),
            func.max(TrafficRecord.created_at).label("latest_created_at"),
        )
        .where(TrafficRecord.created_at > lookback_time)
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
    Retrieve heatmap points for Hyderabad traffic within a lookback window.

    Filters traffic records by age, optional congestion level, and intensity threshold.
    Returns top points ordered by intensity.
    """
    lookback_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = _latest_records_query(lookback_time)

    if congestion_filter is not None:
        query = query.where(TrafficRecord.congestion_level == congestion_filter)

    records = db.execute(query).scalars().all()

    points: List[HeatmapPoint] = []
    for record in records:
        intensity = calculate_intensity(
            vehicle_count=record.vehicle_count or 0,
            average_speed=record.average_speed or 0.0,
            congestion_level=record.congestion_level or "low",
        )
        if intensity < min_intensity:
            continue
        points.append(
            HeatmapPoint(
                latitude=record.latitude,
                longitude=record.longitude,
                intensity=intensity,
                congestion_level=record.congestion_level,
                location=record.location,
                vehicle_count=record.vehicle_count,
                average_speed=record.average_speed,
                timestamp=record.timestamp,
            )
        )

    points.sort(key=lambda p: p.intensity, reverse=True)
    points = points[:limit]

    total_points = len(points)
    high_congestion_count = sum(1 for p in points if p.intensity > 0.7)
    average_intensity = (
        round(sum(p.intensity for p in points) / total_points, 2)
        if total_points > 0 else 0.0
    )

    logger.info(
        "Heatmap data generated: hours=%s, filter=%s, min_intensity=%s, limit=%s, points=%s",
        hours, congestion_filter, min_intensity, limit, total_points,
    )

    return HeatmapResponse(
        points=points,
        total_points=total_points,
        high_congestion_count=high_congestion_count,
        average_intensity=average_intensity,
        coverage_area=COVERAGE_AREA,
        generated_at=datetime.now(timezone.utc),
        hours_lookback=hours,
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
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")

    lookback_time = datetime.now(timezone.utc) - timedelta(hours=1)
    query = _latest_records_query(lookback_time)
    records = db.execute(query).scalars().all()

    hotspot_list = []
    for record in records:
        intensity = calculate_intensity(
            vehicle_count=record.vehicle_count or 0,
            average_speed=record.average_speed or 0.0,
            congestion_level=record.congestion_level or "low",
        )
        sev = classify_severity(intensity)
        hotspot_list.append({
            "latitude": record.latitude,
            "longitude": record.longitude,
            "intensity": intensity,
            "severity": sev,
            "congestion_level": record.congestion_level,
            "location": record.location,
            "vehicle_count": record.vehicle_count,
            "average_speed": record.average_speed,
            "timestamp": record.timestamp,
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
        "coverage_area":    COVERAGE_AREA,
        "total_evaluated":  len(records),
        "severity_filter":  severity,
        "returned":         len(hotspots),
        "hotspots":         hotspots,
        "generated_at":     datetime.now(timezone.utc).astimezone(_IST).isoformat(),
    }
