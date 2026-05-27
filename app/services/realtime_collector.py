"""
Real-time India traffic collector — runs as a background task.

Cycle: every 30 minutes it fetches TomTom Flow data for all India locations
and persists fresh TrafficRecord rows.  When TOMTOM_API_KEY is absent it
falls back to a physics-based simulation so the app always has data.

Rate-limit budget (TomTom free tier: 2 500 req/day):
  80 locations × 48 cycles/day = 3 840  →  batch size capped at 50 per cycle
  or use a paid key for full coverage.
"""

import asyncio
import logging
import math
import random
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.predictor import Incident, TrafficRecord
from app.services.india_locations import INDIA_LOCATIONS
from app.services.tomtom_service import (
    classify_congestion,
    estimate_vehicle_count,
    fetch_flow,
    fetch_incidents,
    parse_incident_type,
)

logger = logging.getLogger(__name__)

# How many locations to fetch per cycle (stay within free-tier budget)
_BATCH_SIZE   = 50
# Seconds between full collection cycles (30 min)
_INTERVAL_SEC = 1800
# Seconds between individual API calls (avoid bursting)
_CALL_DELAY   = 0.3


# ── Simulation fallback ───────────────────────────────────────────────────────

def _simulate_flow(lat: float, lng: float) -> dict:
    """
    Physics-inspired traffic simulation when TomTom key is unavailable.
    Produces realistic rush-hour / off-peak patterns with location variance.
    """
    now    = datetime.now(timezone.utc)
    hour   = now.hour
    is_we  = now.weekday() >= 5   # Saturday / Sunday

    # Base speed curve (km/h) by hour
    if is_we:
        base = {range(0,6): 58, range(6,10): 42, range(10,14): 34,
                range(14,18): 30, range(18,22): 22, range(22,24): 50}
    else:
        base = {range(0,6): 60, range(6,8): 30, range(8,10): 18,
                range(10,16): 38, range(16,19): 15, range(19,21): 28,
                range(21,24): 52}

    speed = 35.0
    for rng, v in base.items():
        if hour in rng:
            speed = v
            break

    # Geographic jitter (India is large — different cities behave differently)
    geo_seed = (int(lat * 100) + int(lng * 100)) % 20
    speed = max(8.0, speed + geo_seed - 10 + random.uniform(-3, 3))

    free_flow = min(80.0, speed * random.uniform(1.4, 2.0))
    confidence = round(random.uniform(0.70, 0.95), 2)

    return {
        "currentSpeed":      round(speed, 1),
        "freeFlowSpeed":     round(free_flow, 1),
        "confidence":        confidence,
        "roadClosure":       False,
    }


# ── Incident upsert ───────────────────────────────────────────────────────────

def _upsert_incident(db, raw: dict, location_name: str) -> None:
    props = raw.get("properties", {})
    coords = raw.get("geometry", {}).get("coordinates", [[]])
    if not coords:
        return

    # TomTom coordinates: [[lon, lat], ...] for LineString
    lon, lat = (coords[0][0], coords[0][1]) if isinstance(coords[0], list) else (coords[0], coords[1])
    category  = props.get("iconCategory", 0)
    inc_type, severity = parse_incident_type(category)

    events = props.get("events", [])
    desc   = events[0].get("description", "Traffic incident reported") if events else "Traffic incident"
    roads  = ", ".join(props.get("roadNumbers", [])) or location_name

    # Only insert if no similar active incident exists nearby
    existing = (
        db.query(Incident)
        .filter(
            Incident.location == roads,
            Incident.incident_type == inc_type,
            Incident.is_active.is_(True),
        )
        .first()
    )
    if not existing:
        db.add(Incident(
            location      = roads,
            latitude      = lat,
            longitude     = lon,
            incident_type = inc_type,
            severity      = severity,
            description   = desc,
            is_active     = True,
            reported_at   = datetime.now(timezone.utc),
        ))


# ── Main collection cycle ─────────────────────────────────────────────────────

async def collect_india_traffic() -> None:
    """
    One full collection cycle:
    1. Rotate through INDIA_LOCATIONS in batches of _BATCH_SIZE.
    2. For each location, try TomTom → fallback to simulation.
    3. Persist a new TrafficRecord row.
    4. Fetch incidents for city-level bounding boxes.
    5. Auto-resolve incidents older than 6 hours.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Round-robin: pick which batch to process this cycle
        total    = len(INDIA_LOCATIONS)
        cycle_id = int(now.timestamp() / _INTERVAL_SEC) % math.ceil(total / _BATCH_SIZE)
        start    = cycle_id * _BATCH_SIZE
        batch    = INDIA_LOCATIONS[start: start + _BATCH_SIZE]

        logger.info(
            "India traffic collect: cycle=%d  locations %d–%d / %d",
            cycle_id, start, start + len(batch), total,
        )

        records  = []
        seen_cities: set[str] = set()

        for loc in batch:
            flow = await fetch_flow(loc["lat"], loc["lng"])
            if flow is None:
                flow = _simulate_flow(loc["lat"], loc["lng"])

            cur_speed  = float(flow.get("currentSpeed",  35))
            free_speed = float(flow.get("freeFlowSpeed", 60))
            confidence = float(flow.get("confidence",   0.8))

            congestion = classify_congestion(cur_speed, free_speed)
            vehicles   = estimate_vehicle_count(cur_speed, free_speed)

            records.append(TrafficRecord(
                location        = loc["name"],
                latitude        = loc["lat"],
                longitude       = loc["lng"],
                vehicle_count   = vehicles,
                average_speed   = round(cur_speed, 1),
                congestion_level= congestion,
                road_type       = loc["road_type"],
                timestamp       = now,
                created_at      = now,
            ))

            # Queue incident fetch for each unique city (one bbox per city)
            seen_cities.add(loc["city"])

            if _CALL_DELAY > 0:
                await asyncio.sleep(_CALL_DELAY)

        # Bulk insert traffic records
        db.bulk_save_objects(records)
        db.commit()
        logger.info("Inserted %d traffic records", len(records))

        # Fetch & store incidents for each city in this batch
        city_locs = {
            loc["city"]: loc
            for loc in batch
            if loc["city"] in seen_cities
        }
        for city, ref in city_locs.items():
            delta = 0.3  # ~33 km bounding box
            raw_incidents = await fetch_incidents(
                ref["lat"] - delta, ref["lng"] - delta,
                ref["lat"] + delta, ref["lng"] + delta,
            )
            for raw in raw_incidents:
                _upsert_incident(db, raw, city)
            if raw_incidents:
                db.commit()
                logger.info("Incidents for %s: %d", city, len(raw_incidents))

        # Auto-resolve stale incidents (older than 6 hours)
        cutoff = now - timedelta(hours=6)
        stale  = db.query(Incident).filter(
            Incident.is_active.is_(True),
            Incident.reported_at < cutoff,
        ).all()
        for inc in stale:
            inc.is_active   = False
            inc.resolved_at = now
        if stale:
            db.commit()
            logger.info("Auto-resolved %d stale incidents", len(stale))

    except Exception as exc:
        db.rollback()
        logger.error("India traffic collect error: %s", exc, exc_info=True)
    finally:
        db.close()


async def run_india_traffic_collector() -> None:
    """Infinite loop: collect immediately on startup, then every 30 minutes."""
    logger.info("India real-time traffic collector started (%d locations)", len(INDIA_LOCATIONS))
    while True:
        await collect_india_traffic()
        await asyncio.sleep(_INTERVAL_SEC)
