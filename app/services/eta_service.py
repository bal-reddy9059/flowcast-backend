"""
ETA calculation services for FlowCast.

Provides real-time ETA calculations using stored traffic observations.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.predictor import TrafficRecord
from app.schemas.eta import ETAResponse

logger = logging.getLogger(__name__)

TRAFFIC_CONDITIONS = {
    "low": "Light traffic — smooth drive expected",
    "medium": "Moderate traffic — slight delays possible",
    "high": "Heavy traffic — significant delays expected",
}


def get_speed_for_congestion(congestion_level: str, mode: str) -> float:
    """Return the base travel speed for a congestion level and travel mode."""
    if mode == "walking":
        return 5.0

    if mode == "transit":
        return {
            "low": 45.0,
            "medium": 30.0,
            "high": 20.0,
        }.get(congestion_level, 40.0)

    return {
        "low": 60.0,
        "medium": 35.0,
        "high": 15.0,
    }.get(congestion_level, 40.0)


def calculate_eta_minutes(distance_km: float, speed_kmh: float) -> tuple[float, float]:
    """Calculate ETA and buffered ETA from distance and speed."""
    if speed_kmh <= 0:
        raise ValueError("speed_kmh must be greater than zero")

    eta_minutes = (distance_km / speed_kmh) * 60.0
    eta_with_buffer_minutes = eta_minutes * 1.1
    return round(eta_minutes, 1), round(eta_with_buffer_minutes, 1)


async def get_location_traffic(location: str, db: AsyncSession) -> tuple[Any, str]:
    """Fetch the latest traffic record for a location and derive confidence."""
    stmt = (
        select(TrafficRecord)
        .where(TrafficRecord.location.ilike(f"%{location}%"))
        .order_by(TrafficRecord.created_at.desc())
        .limit(1)
    )
    logger.debug("Querying latest traffic record for location: %s", location)
    result = await db.execute(stmt)
    record = result.scalars().first()

    if not record:
        logger.info("No traffic data for %s", location)
        return None, "low"

    now = datetime.now(timezone.utc)
    created_at = record.created_at
    if created_at is None:
        confidence = "low"
    else:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_minutes = (now - created_at).total_seconds() / 60.0
        if age_minutes < 15:
            confidence = "high"
        elif age_minutes < 60:
            confidence = "medium"
        else:
            confidence = "low"

    logger.debug(
        "Latest traffic record for %s age=%.1f minutes confidence=%s",
        location,
        age_minutes,
        confidence,
    )
    return record, confidence


async def calculate_eta_for_location(
    location: str,
    distance_km: float,
    mode: str,
    db: AsyncSession,
) -> ETAResponse:
    """Calculate ETA for a location using the latest traffic record and travel mode."""
    record, confidence = await get_location_traffic(location, db)

    if record:
        congestion_level = record.congestion_level or "medium"
        avg_speed = float(record.average_speed) if record.average_speed is not None else 0.0
        vehicle_count = int(record.vehicle_count) if record.vehicle_count is not None else 0
    else:
        congestion_level = "medium"
        avg_speed = 35.0
        vehicle_count = 0

    mode_speed = get_speed_for_congestion(congestion_level, mode)
    final_speed = mode_speed if avg_speed <= 0 else min(avg_speed, mode_speed)

    eta_minutes, eta_with_buffer_minutes = calculate_eta_minutes(distance_km, final_speed)
    traffic_condition = TRAFFIC_CONDITIONS.get(congestion_level, TRAFFIC_CONDITIONS["medium"])

    logger.info(
        "ETA for %s: %.1f mins congestion=%s confidence=%s",
        location,
        eta_minutes,
        congestion_level,
        confidence,
    )

    return ETAResponse(
        location=location,
        distance_km=distance_km,
        eta_minutes=eta_minutes,
        eta_with_buffer_minutes=eta_with_buffer_minutes,
        congestion_level=congestion_level,
        average_speed_kmh=final_speed,
        vehicle_count=vehicle_count,
        traffic_condition=traffic_condition,
        confidence=confidence,
        calculated_at=datetime.now(timezone.utc),
    )
