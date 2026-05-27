"""
ETA calculation services for FlowCast.

Provides real-time ETA calculations using stored traffic observations.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.predictor import TrafficRecord
from app.schemas.eta import ETAResponse
from app.services.city_aliases import CITY_ALIASES as _CITY_ALIASES

_IST = ZoneInfo("Asia/Kolkata")

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


def get_location_traffic(location: str, db: Session) -> tuple[Any, str, float]:
    """Fetch the latest traffic record for a location and derive confidence.

    For city names (e.g. 'Hyderabad'), aggregates across all known neighbourhoods.
    Returns (record_or_aggregate, confidence, age_minutes).
    """
    from sqlalchemy import or_

    now = datetime.now(_IST)
    aliases = _CITY_ALIASES.get(location.lower())

    if aliases:
        # City-level: average across all neighbourhood records from last 2 hours
        rows = (
            db.query(
                func.avg(TrafficRecord.average_speed).label("avg_speed"),
                func.avg(TrafficRecord.vehicle_count).label("avg_vehicles"),
                func.max(TrafficRecord.created_at).label("latest"),
                func.count(TrafficRecord.id).label("cnt"),
            )
            .filter(
                or_(*[TrafficRecord.location.ilike(f"%{a}%") for a in aliases]),
                TrafficRecord.average_speed.isnot(None),
            )
            .first()
        )

        if not rows or not rows.avg_speed:
            logger.info("No city-level traffic data for %s", location)
            return None, "low", 0.0

        # Build a pseudo-record dict
        latest = rows.latest
        if latest and latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_minutes = (now.astimezone(timezone.utc) - latest.astimezone(timezone.utc)).total_seconds() / 60.0 if latest else 999.0

        # Determine congestion from average speed
        avg_speed = float(rows.avg_speed)
        if avg_speed >= 50:
            congestion = "low"
        elif avg_speed >= 25:
            congestion = "medium"
        else:
            congestion = "high"

        class _Aggregate:
            average_speed = avg_speed
            vehicle_count = int(rows.avg_vehicles or 0)
            congestion_level = congestion
            created_at = latest

        confidence = "high" if age_minutes < 15 else "medium" if age_minutes < 60 else "low"
        logger.info("City ETA for %s: avg_speed=%.1f age=%.0f min confidence=%s", location, avg_speed, age_minutes, confidence)
        return _Aggregate(), confidence, round(age_minutes, 1)

    # Single location
    stmt = (
        select(TrafficRecord)
        .where(TrafficRecord.location.ilike(f"%{location}%"))
        .order_by(TrafficRecord.created_at.desc())
        .limit(1)
    )
    result = db.execute(stmt)
    record = result.scalars().first()

    if not record:
        logger.info("No traffic data for %s", location)
        return None, "low", 0.0

    created_at = record.created_at
    age_minutes = 0.0
    if created_at is None:
        confidence = "low"
    else:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_minutes = (now.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds() / 60.0
        confidence = "high" if age_minutes < 15 else "medium" if age_minutes < 60 else "low"

    logger.debug("ETA record for %s age=%.1f min confidence=%s", location, age_minutes, confidence)
    return record, confidence, round(age_minutes, 1)


def calculate_eta_for_location(
    location: str,
    distance_km: float,
    mode: str,
    db: Session,
) -> ETAResponse:
    """Calculate ETA for a location using the latest traffic record and travel mode."""
    record, confidence, data_age_minutes = get_location_traffic(location, db)

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

    now_ist = datetime.now(_IST)
    from datetime import timedelta
    arrival_time = now_ist + timedelta(minutes=eta_with_buffer_minutes)

    logger.info(
        "ETA for %s: %.1f mins congestion=%s confidence=%s data_age=%.0f min",
        location, eta_minutes, congestion_level, confidence, data_age_minutes,
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
        data_age_minutes=data_age_minutes,
        arrival_time=arrival_time,
        calculated_at=now_ist,
    )
