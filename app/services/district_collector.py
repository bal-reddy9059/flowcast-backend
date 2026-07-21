"""
All-India district traffic collector.

Cycles through all 766 Indian districts using Google Directions API
(departure_time=now) to get real congestion data. Falls back to
time-of-day simulation when the API is unavailable or rate-limited.

Rate budget: ~40 districts per 30-min cycle → 766 districts in ~9.5 hrs.
Google Maps free credit covers ~1,900 req/day comfortably.
"""

import asyncio
import logging
import math
import random
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.predictor import TrafficRecord
from app.services.traffic_flow_service import REAL_DATA_ONLY
from app.services.india_districts import INDIA_DISTRICTS
from app.services.google_traffic_service import fetch_district_traffic

logger = logging.getLogger(__name__)

_BATCH_SIZE   = 40          # districts per cycle
_INTERVAL_SEC = 1800        # 30 minutes between cycles
_PARALLEL     = 6           # concurrent Google/live lookups
_STARTUP_DELAY = 90         # keep API snappy at boot
_CALL_DELAY   = 0.0         # unused when parallel

# Shared in-memory state: latest reading per district (for WS snapshots)
_district_cache: dict[str, dict] = {}

# Optional async broadcast callback — injected by india_ws at startup
_broadcast_fn = None


def set_broadcast_fn(fn) -> None:
    """Register an async callable(message: dict) to push updates to WS clients."""
    global _broadcast_fn
    _broadcast_fn = fn


def get_district_snapshot() -> list[dict]:
    """Return current cached snapshot (all districts that have been fetched)."""
    return list(_district_cache.values())


# ── Simulation fallback ───────────────────────────────────────────────────────

_FREE_FLOW_SPEED_KMPH = 55.0  # typical district free-flow speed used for ratio calculation


def _simulate_district(lat: float, lng: float) -> dict:
    now = datetime.now(timezone.utc)
    hour = now.hour
    is_we = now.weekday() >= 5

    if is_we:
        base_speeds = {
            range(0, 6): 55, range(6, 10): 38, range(10, 14): 30,
            range(14, 18): 27, range(18, 22): 20, range(22, 24): 48,
        }
    else:
        base_speeds = {
            range(0, 6): 58, range(6, 8): 28, range(8, 10): 16,
            range(10, 16): 36, range(16, 19): 14, range(19, 21): 26,
            range(21, 24): 50,
        }

    speed = 35.0
    for rng, v in base_speeds.items():
        if hour in rng:
            speed = v
            break

    geo_seed = (int(lat * 100) + int(lng * 100)) % 18
    speed = max(8.0, speed + geo_seed - 9 + random.uniform(-4, 4))
    speed = round(speed, 1)

    if speed >= 40:
        congestion = "low"
    elif speed >= 22:
        congestion = "medium"
    else:
        congestion = "high"

    # congestion_ratio mirrors Google Maps: duration_in_traffic / free_flow_duration
    # = free_flow_speed / current_speed  (higher = more congested; 1.0 = no delay)
    congestion_ratio = round(_FREE_FLOW_SPEED_KMPH / speed, 2) if speed > 0 else None

    return {
        "speed_kmh":            speed,
        "congestion_level":     congestion,
        "duration_s":           None,
        "duration_in_traffic_s": None,
        "distance_m":           None,
        "congestion_ratio":     congestion_ratio,
        "source":               "simulated",
    }


# ── Vehicle count estimate ────────────────────────────────────────────────────

def _estimate_vehicles(speed_kmh: float) -> int:
    if speed_kmh >= 50:
        return random.randint(200, 600)
    if speed_kmh >= 30:
        return random.randint(600, 1200)
    if speed_kmh >= 15:
        return random.randint(1200, 2000)
    return random.randint(2000, 3500)


# ── Main collection cycle ─────────────────────────────────────────────────────

async def collect_district_batch() -> None:
    """Fetch one batch of districts, update cache, broadcast via WebSocket."""
    now = datetime.now(timezone.utc)
    total = len(INDIA_DISTRICTS)
    cycle_id = int(now.timestamp() / _INTERVAL_SEC) % math.ceil(total / _BATCH_SIZE)
    start = cycle_id * _BATCH_SIZE
    batch = INDIA_DISTRICTS[start: start + _BATCH_SIZE]

    logger.info(
        "District collector: cycle=%d  districts %d–%d / %d",
        cycle_id, start, start + len(batch), total,
    )

    db = SessionLocal()
    records = []
    try:
        sem = asyncio.Semaphore(_PARALLEL)

        async def _one(district: dict):
            async with sem:
                flow = await fetch_district_traffic(
                    district["lat"], district["lng"],
                    district["dest_lat"], district["dest_lng"],
                )
            return district, flow

        fetched = await asyncio.gather(*[_one(d) for d in batch])

        for district, flow in fetched:
            source = "google"
            if flow is None:
                if REAL_DATA_ONLY:
                    logger.debug(
                        "Skipping district %s — no live API data",
                        district["district"],
                    )
                    continue
                flow = _simulate_district(district["lat"], district["lng"])
                source = "simulated"
            else:
                flow["source"] = source

            speed = flow["speed_kmh"]
            vehicles = _estimate_vehicles(speed)
            congestion = flow["congestion_level"]

            entry = {
                "district":        district["district"],
                "state":           district["state"],
                "lat":             district["lat"],
                "lng":             district["lng"],
                "speed_kmh":       speed,
                "congestion_level": congestion,
                "vehicle_count":   vehicles,
                "congestion_ratio": flow.get("congestion_ratio"),
                "source":          source,
                "updated_at":      now.isoformat(),
            }
            _district_cache[district["district"]] = entry

            if _broadcast_fn is not None:
                try:
                    await _broadcast_fn({
                        "type":    "district_update",
                        "payload": entry,
                    })
                except Exception:
                    pass

            records.append(TrafficRecord(
                location         = f"{district['district']}, {district['state']}",
                latitude         = district["lat"],
                longitude        = district["lng"],
                vehicle_count    = vehicles,
                average_speed    = speed,
                congestion_level = congestion,
                road_type        = "district",
                data_source      = source,
                timestamp        = now,
                created_at       = now,
            ))

        db.bulk_save_objects(records)
        db.commit()
        logger.info("Inserted %d district traffic records", len(records))

    except Exception as exc:
        db.rollback()
        logger.error("District collector error: %s", exc, exc_info=True)
    finally:
        db.close()


async def run_district_collector() -> None:
    """Infinite loop: delay briefly so API is ready, then collect every 30 min."""
    logger.info(
        "India district traffic collector started (%d districts, batch=%d)",
        len(INDIA_DISTRICTS), _BATCH_SIZE,
    )
    await asyncio.sleep(_STARTUP_DELAY)
    while True:
        await collect_district_batch()
        await asyncio.sleep(_INTERVAL_SEC)
