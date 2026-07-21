"""
ETA calculation services for FlowCast.

Provides real-time ETA calculations using stored traffic observations,
with live TomTom/HERE upgrade when available. Designed to respond in <1.5s.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

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


class _TrafficSnap:
    __slots__ = ("average_speed", "vehicle_count", "congestion_level", "created_at")

    def __init__(
        self,
        average_speed: float,
        vehicle_count: int,
        congestion_level: str,
        created_at: datetime | None,
    ) -> None:
        self.average_speed = average_speed
        self.vehicle_count = vehicle_count
        self.congestion_level = congestion_level
        self.created_at = created_at


def _age_minutes(created_at: datetime | None) -> float:
    if created_at is None:
        return 999.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - created_at.astimezone(timezone.utc)).total_seconds() / 60.0


def _confidence_from_age(age_minutes: float) -> str:
    if age_minutes < 15:
        return "high"
    if age_minutes < 60:
        return "medium"
    return "low"


def _latest_record_for_name(db: Session, name: str) -> Any | None:
    """Index-friendly lookup: exact match first, then prefix. No lower()/ilike scans."""
    # Cap wait — never sit on a locked traffic_records table
    db.execute(text("SET LOCAL statement_timeout = '800ms'"))
    db.execute(text("SET LOCAL lock_timeout = '400ms'"))

    row = db.execute(
        text(
            "SELECT average_speed, vehicle_count, congestion_level, created_at "
            "FROM traffic_records WHERE location = :loc "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"loc": name},
    ).mappings().first()
    if row:
        return _TrafficSnap(
            float(row["average_speed"] or 0),
            int(row["vehicle_count"] or 0),
            row["congestion_level"] or "medium",
            row["created_at"],
        )

    # Case-insensitive exact via citext-free pattern: try title/upper variants quickly
    row = db.execute(
        text(
            "SELECT average_speed, vehicle_count, congestion_level, created_at "
            "FROM traffic_records WHERE location ILIKE :loc "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"loc": name},
    ).mappings().first()
    if row:
        return _TrafficSnap(
            float(row["average_speed"] or 0),
            int(row["vehicle_count"] or 0),
            row["congestion_level"] or "medium",
            row["created_at"],
        )

    # Prefix only (uses btree index better than %name%)
    row = db.execute(
        text(
            "SELECT average_speed, vehicle_count, congestion_level, created_at "
            "FROM traffic_records WHERE location ILIKE :pfx "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"pfx": f"{name}%"},
    ).mappings().first()
    if row:
        return _TrafficSnap(
            float(row["average_speed"] or 0),
            int(row["vehicle_count"] or 0),
            row["congestion_level"] or "medium",
            row["created_at"],
        )
    return None


def get_location_traffic(location: str, db: Session) -> tuple[Any, str, float]:
    """Fetch the latest traffic record for a location and derive confidence."""
    aliases = _CITY_ALIASES.get(location.lower())

    try:
        if aliases:
            db.execute(text("SET LOCAL statement_timeout = '800ms'"))
            db.execute(text("SET LOCAL lock_timeout = '400ms'"))
            # Limit to recent rows so aggregation stays cheap
            rows = db.execute(
                text(
                    """
                    SELECT AVG(average_speed) AS avg_speed,
                           AVG(vehicle_count) AS avg_vehicles,
                           MAX(created_at) AS latest
                    FROM (
                      SELECT average_speed, vehicle_count, created_at
                      FROM traffic_records
                      WHERE location = ANY(:names)
                        AND average_speed IS NOT NULL
                        AND created_at > NOW() - INTERVAL '6 hours'
                      ORDER BY created_at DESC
                      LIMIT 200
                    ) recent
                    """
                ),
                {"names": list(aliases)},
            ).mappings().first()

            if rows and rows["avg_speed"]:
                avg_speed = float(rows["avg_speed"])
                if avg_speed >= 50:
                    congestion = "low"
                elif avg_speed >= 25:
                    congestion = "medium"
                else:
                    congestion = "high"
                age = _age_minutes(rows["latest"])
                return (
                    _TrafficSnap(
                        avg_speed,
                        int(rows["avg_vehicles"] or 0),
                        congestion,
                        rows["latest"],
                    ),
                    _confidence_from_age(age),
                    round(age, 1),
                )

        record = _latest_record_for_name(db, location.strip())
        if not record:
            logger.info("No traffic data for %s", location)
            return None, "low", 0.0

        age = _age_minutes(record.created_at)
        return record, _confidence_from_age(age), round(age, 1)
    except Exception as exc:
        # Locked / slow DB — caller falls back to defaults instantly
        logger.warning("ETA DB lookup skipped for %s: %s", location, type(exc).__name__)
        try:
            db.rollback()
        except Exception:
            pass
        return None, "low", 0.0


async def fetch_live_location_flow(location: str) -> Optional[dict]:
    """
    Look up a location by name in INDIA_LOCATIONS and fetch live traffic flow
    via TomTom (preferred) / HERE — bypasses the DB cache for freshness.

    Hard-capped at ~0.7s so request handlers stay snappy when APIs stall.
    """
    import asyncio

    from app.services.india_locations import INDIA_LOCATIONS
    from app.services.traffic_flow_service import fetch_flow
    from app.services.tomtom_service import classify_congestion as _classify

    loc = next(
        (l for l in INDIA_LOCATIONS if l["name"].lower() == location.lower()),
        None,
    )
    if not loc:
        return None

    try:
        flow = await asyncio.wait_for(fetch_flow(loc["lat"], loc["lng"]), timeout=0.7)
    except asyncio.TimeoutError:
        logger.debug("Live flow timeout for %s", location)
        return None

    if flow is None:
        return None

    cur = float(flow.get("currentSpeed", 35))
    free = float(flow.get("freeFlowSpeed", 60))
    return {
        "speed_kmh": cur,
        "congestion_level": _classify(cur, free),
        "source": flow.get("source", "tomtom"),
    }


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


def calculate_eta_from_snapshot(
    location: str,
    distance_km: float,
    mode: str,
    *,
    speed_kmh: float,
    congestion_level: str,
    confidence: str = "high",
    data_age_minutes: float = 0.0,
    vehicle_count: int = 0,
) -> ETAResponse:
    """Build an ETAResponse from an already-known speed/congestion snapshot."""
    mode_speed = get_speed_for_congestion(congestion_level, mode)
    final_speed = mode_speed if speed_kmh <= 0 else min(speed_kmh, mode_speed)
    eta_minutes, eta_with_buffer_minutes = calculate_eta_minutes(distance_km, final_speed)
    now_ist = datetime.now(_IST)
    return ETAResponse(
        location=location,
        distance_km=distance_km,
        eta_minutes=eta_minutes,
        eta_with_buffer_minutes=eta_with_buffer_minutes,
        congestion_level=congestion_level,
        average_speed_kmh=final_speed,
        vehicle_count=vehicle_count,
        traffic_condition=TRAFFIC_CONDITIONS.get(congestion_level, TRAFFIC_CONDITIONS["medium"]),
        confidence=confidence,
        data_age_minutes=data_age_minutes,
        arrival_time=now_ist + timedelta(minutes=eta_with_buffer_minutes),
        calculated_at=now_ist,
    )
