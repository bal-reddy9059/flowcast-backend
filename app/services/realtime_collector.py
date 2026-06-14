"""
Real-time India traffic collector — runs as a background task.

Data source priority (first available wins per location):
  1. HERE Maps Traffic API  — 250,000 free calls/month, no credit card
  2. TomTom Traffic API     — 2,500 free calls/day
  3. Physics-based simulation — always available as last resort

Cycle: every 30 minutes, fetches live flow data for all India locations
and persists fresh TrafficRecord rows.

Rate-limit budgets:
  HERE    : 80 loc × 48 cycles/day = 3,840/day — well under 250K/month limit
  TomTom  : batch capped at 50/cycle to stay within 2,500/day free tier
"""

import asyncio
import logging
import math
import random
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.predictor import Incident, TrafficRecord
from app.services.india_locations import INDIA_LOCATIONS
from app.services import here_traffic_service
from app.services.tomtom_service import (
    classify_congestion,
    estimate_vehicle_count,
    fetch_flow as tomtom_fetch_flow,
    fetch_incidents as tomtom_fetch_incidents,
    parse_incident_type,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE   = 50     # locations per cycle (TomTom free-tier guard)
_INTERVAL_SEC = 1800   # 30 min between full collection cycles
_CALL_DELAY   = 0.3    # seconds between individual API calls


# ── Simulation fallback ───────────────────────────────────────────────────────

def _simulate_flow(lat: float, lng: float) -> dict:
    """
    Physics-inspired traffic simulation used only when all real APIs are unavailable.
    Produces realistic rush-hour / off-peak patterns with location variance.
    """
    now   = datetime.now(timezone.utc)
    hour  = now.hour
    is_we = now.weekday() >= 5

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

    geo_seed = (int(lat * 100) + int(lng * 100)) % 20
    speed = max(8.0, speed + geo_seed - 10 + random.uniform(-3, 3))
    free_flow = min(80.0, speed * random.uniform(1.4, 2.0))

    return {
        "currentSpeed":  round(speed, 1),
        "freeFlowSpeed": round(free_flow, 1),
        "confidence":    round(random.uniform(0.70, 0.95), 2),
        "roadClosure":   False,
        "source":        "simulated",
    }


# ── Incident upsert ───────────────────────────────────────────────────────────

def _upsert_incident(db, location: str, lat: float, lon: float,
                     inc_type: str, severity: str, description: str) -> None:
    existing = (
        db.query(Incident)
        .filter(
            Incident.location == location,
            Incident.incident_type == inc_type,
            Incident.is_active.is_(True),
        )
        .first()
    )
    if not existing:
        db.add(Incident(
            location      = location,
            latitude      = lat,
            longitude     = lon,
            incident_type = inc_type,
            severity      = severity,
            description   = description,
            is_active     = True,
            reported_at   = datetime.now(timezone.utc),
        ))


def _upsert_tomtom_incident(db, raw: dict, location_name: str) -> None:
    props  = raw.get("properties", {})
    coords = raw.get("geometry", {}).get("coordinates", [[]])
    if not coords:
        return

    lon, lat = (coords[0][0], coords[0][1]) if isinstance(coords[0], list) else (coords[0], coords[1])
    category  = props.get("iconCategory", 0)
    inc_type, severity = parse_incident_type(category)

    events = props.get("events", [])
    desc   = events[0].get("description", "Traffic incident reported") if events else "Traffic incident"
    roads  = ", ".join(props.get("roadNumbers", [])) or location_name

    _upsert_incident(db, roads, lat, lon, inc_type, severity, desc)


# ── Main collection cycle ─────────────────────────────────────────────────────

async def collect_india_traffic() -> None:
    """
    One full collection cycle:
    1. Rotate through INDIA_LOCATIONS in batches of _BATCH_SIZE.
    2. For each location: try HERE → TomTom → simulation.
    3. Persist a new TrafficRecord row.
    4. Fetch incidents (HERE preferred, TomTom fallback) per unique city.
    5. Auto-resolve incidents older than 6 hours.
    """
    db  = SessionLocal()
    now = datetime.now(timezone.utc)

    source_counts = {"here": 0, "tomtom": 0, "simulated": 0}

    try:
        total    = len(INDIA_LOCATIONS)
        cycle_id = int(now.timestamp() / _INTERVAL_SEC) % math.ceil(total / _BATCH_SIZE)
        start    = cycle_id * _BATCH_SIZE
        batch    = INDIA_LOCATIONS[start: start + _BATCH_SIZE]

        logger.info(
            "India traffic collect: cycle=%d  locations %d–%d / %d  "
            "[HERE=%s TomTom=%s]",
            cycle_id, start, start + len(batch), total,
            "on" if here_traffic_service.is_available() else "off",
            "on" if _tomtom_available() else "off",
        )

        records     = []
        seen_cities: set[str] = set()

        for loc in batch:
            flow        = None
            data_source = "simulated"

            # 1. Try HERE (250K free/month)
            flow = await here_traffic_service.fetch_flow(loc["lat"], loc["lng"])
            if flow is not None:
                data_source = "here"

            # 2. Fall back to TomTom (2,500 free/day)
            if flow is None:
                flow = await tomtom_fetch_flow(loc["lat"], loc["lng"])
                if flow is not None:
                    data_source = "tomtom"

            # 3. Fall back to physics simulation
            if flow is None:
                flow        = _simulate_flow(loc["lat"], loc["lng"])
                data_source = "simulated"

            source_counts[data_source] += 1

            cur_speed  = float(flow.get("currentSpeed",  35))
            free_speed = float(flow.get("freeFlowSpeed", 60))

            congestion = classify_congestion(cur_speed, free_speed)
            vehicles   = estimate_vehicle_count(cur_speed, free_speed)

            records.append(TrafficRecord(
                location         = loc["name"],
                latitude         = loc["lat"],
                longitude        = loc["lng"],
                vehicle_count    = vehicles,
                average_speed    = round(cur_speed, 1),
                congestion_level = congestion,
                road_type        = loc["road_type"],
                timestamp        = now,
                created_at       = now,
            ))

            seen_cities.add(loc["city"])

            if _CALL_DELAY > 0:
                await asyncio.sleep(_CALL_DELAY)

        db.bulk_save_objects(records)
        db.commit()
        logger.info(
            "Inserted %d traffic records  [HERE=%d TomTom=%d simulated=%d]",
            len(records),
            source_counts["here"], source_counts["tomtom"], source_counts["simulated"],
        )

        # ── Fetch incidents for each unique city in this batch ────────────────
        city_locs = {
            loc["city"]: loc
            for loc in batch
            if loc["city"] in seen_cities
        }

        for city, ref in city_locs.items():
            delta = 0.3  # ~33 km bounding box

            # Prefer HERE incidents; fall back to TomTom
            if here_traffic_service.is_available():
                raw_incidents = await here_traffic_service.fetch_incidents(
                    ref["lat"] - delta, ref["lng"] - delta,
                    ref["lat"] + delta, ref["lng"] + delta,
                )
                for raw in raw_incidents:
                    parsed = here_traffic_service.parse_incident(raw, city)
                    if parsed:
                        _upsert_incident(
                            db,
                            parsed["location"],
                            parsed["latitude"],
                            parsed["longitude"],
                            parsed["incident_type"],
                            parsed["severity"],
                            parsed["description"],
                        )
                if raw_incidents:
                    db.commit()
                    logger.info("HERE incidents for %s: %d", city, len(raw_incidents))
            else:
                raw_incidents = await tomtom_fetch_incidents(
                    ref["lat"] - delta, ref["lng"] - delta,
                    ref["lat"] + delta, ref["lng"] + delta,
                )
                for raw in raw_incidents:
                    _upsert_tomtom_incident(db, raw, city)
                if raw_incidents:
                    db.commit()
                    logger.info("TomTom incidents for %s: %d", city, len(raw_incidents))

        # ── Auto-resolve stale incidents (older than 6 hours) ─────────────────
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


def _tomtom_available() -> bool:
    """Check if TomTom key is configured (mirrors tomtom_service logic)."""
    from app.services.tomtom_service import TOMTOM_API_KEY, _key_invalid as _tt_invalid
    return bool(TOMTOM_API_KEY) and not _tt_invalid


async def run_india_traffic_collector() -> None:
    """Infinite loop: collect immediately on startup, then every 30 minutes."""
    logger.info(
        "India real-time traffic collector started (%d locations) — "
        "source priority: HERE → TomTom → simulation",
        len(INDIA_LOCATIONS),
    )
    while True:
        await collect_india_traffic()
        await asyncio.sleep(_INTERVAL_SEC)
