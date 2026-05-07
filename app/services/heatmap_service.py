"""
Heatmap service for traffic heatmap data generation.

Provides intensity calculation and PostgreSQL queries for heatmap points
used by the frontend Google Maps heatmap layer.
"""

from datetime import datetime, timedelta
import logging
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.predictor import TrafficRecord
from app.schemas.heatmap import HeatmapPoint, HeatmapResponse

logger = logging.getLogger(__name__)

COVERAGE_AREA = "Hyderabad, Telangana, India"


def calculate_intensity(
    vehicle_count: int,
    average_speed: float,
    congestion_level: str,
) -> float:
    """
    Calculate a normalized intensity score between 0.0 and 1.0.

    The score is based on speed, vehicle count, and congestion level.
    Lower speed, higher vehicle count, and higher congestion all increase intensity.
    """
    if average_speed > 60:
        speed_score = 0.1
    elif 40 <= average_speed <= 60:
        speed_score = 0.3
    elif 25 <= average_speed <= 39:
        speed_score = 0.6
    else:
        speed_score = 0.9

    if vehicle_count < 20:
        vehicle_score = 0.1
    elif 20 <= vehicle_count <= 50:
        vehicle_score = 0.4
    elif 51 <= vehicle_count <= 100:
        vehicle_score = 0.7
    else:
        vehicle_score = 1.0

    if congestion_level == "low":
        congestion_score = 0.2
    elif congestion_level == "medium":
        congestion_score = 0.5
    else:
        congestion_score = 1.0

    intensity = (
        speed_score * 0.4
        + vehicle_score * 0.4
        + congestion_score * 0.2
    )
    intensity = round(intensity, 2)
    return min(max(intensity, 0.0), 1.0)


async def get_heatmap_data(
    hours: int,
    congestion_filter: Optional[str],
    min_intensity: float,
    limit: int,
    db: AsyncSession,
) -> HeatmapResponse:
    """
    Retrieve heatmap points for Hyderabad traffic within a lookback window.

    Filters traffic records by age, optional congestion level, and intensity threshold.
    Returns top points ordered by intensity.
    """
    lookback_time = datetime.utcnow() - timedelta(hours=hours)

    latest_subquery = (
        select(
            TrafficRecord.location.label("location"),
            func.max(TrafficRecord.created_at).label("latest_created_at"),
        )
        .where(TrafficRecord.created_at > lookback_time)
        .group_by(TrafficRecord.location)
        .subquery()
    )

    query = (
        select(TrafficRecord)
        .join(
            latest_subquery,
            (TrafficRecord.location == latest_subquery.c.location)
            & (TrafficRecord.created_at == latest_subquery.c.latest_created_at),
        )
    )

    if congestion_filter is not None:
        query = query.where(TrafficRecord.congestion_level == congestion_filter)

    query = query.order_by(TrafficRecord.created_at.desc())

    result = await db.execute(query)
    records = result.scalars().all()

    points: List[HeatmapPoint] = []

    for record in records:
        intensity = calculate_intensity(
            vehicle_count=record.vehicle_count,
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

    points.sort(key=lambda point: point.intensity, reverse=True)
    points = points[:limit]

    total_points = len(points)
    high_congestion_count = len([point for point in points if point.intensity > 0.7])
    average_intensity = (
        round(sum(point.intensity for point in points) / total_points, 2)
        if total_points > 0
        else 0.0
    )

    logger.info(
        "Heatmap data generated: hours=%s, filter=%s, min_intensity=%s, limit=%s, points=%s",
        hours,
        congestion_filter,
        min_intensity,
        limit,
        total_points,
    )

    return HeatmapResponse(
        points=points,
        total_points=total_points,
        high_congestion_count=high_congestion_count,
        average_intensity=average_intensity,
        coverage_area=COVERAGE_AREA,
        generated_at=datetime.utcnow(),
        hours_lookback=hours,
    )


async def get_hyderabad_hotspots(db: AsyncSession) -> List[HeatmapPoint]:
    """
    Get top 10 highest intensity traffic hotspots in Hyderabad.

    Uses the last hour of traffic records and returns the most congested locations.
    """
    lookback_time = datetime.utcnow() - timedelta(hours=1)

    latest_subquery = (
        select(
            TrafficRecord.location.label("location"),
            func.max(TrafficRecord.created_at).label("latest_created_at"),
        )
        .where(TrafficRecord.created_at > lookback_time)
        .group_by(TrafficRecord.location)
        .subquery()
    )

    query = (
        select(TrafficRecord)
        .join(
            latest_subquery,
            (TrafficRecord.location == latest_subquery.c.location)
            & (TrafficRecord.created_at == latest_subquery.c.latest_created_at),
        )
        .order_by(TrafficRecord.created_at.desc())
    )

    result = await db.execute(query)
    records = result.scalars().all()

    points: List[HeatmapPoint] = []
    for record in records:
        intensity = calculate_intensity(
            vehicle_count=record.vehicle_count,
            average_speed=record.average_speed or 0.0,
            congestion_level=record.congestion_level or "low",
        )

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

    points.sort(key=lambda point: point.intensity, reverse=True)
    hotspots = points[:10]

    logger.info("Hotspots fetched: %s locations checked", len(records))

    return hotspots
