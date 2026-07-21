"""Station listing with live (or baseline) crowd enrichment."""

import asyncio
import uuid
from typing import Optional

from app.utils import crowd_live_cache
from app.utils.crowd_predictor import baseline_crowd_score, now_ist

# Friendly aliases used in older docs / clients → canonical UUIDs
STATION_ALIASES = {
    "blr-rail-01": "341fed3e-210b-5aba-9846-149f991b9a10",
    "blr-bus-01":  "feebf092-004f-5b71-b156-bb26c4533492",
    "blr-bus-02":  "5c36b0c7-3c47-59eb-908b-c77f2871d590",
    "hyd-rail-01": "2683f4a8-0414-5c84-9f3a-6d876bc7fe01",
    "hyd-bus-01":  "b6596665-ae90-53e1-8ef5-7a707f470185",
    "hyd-rail-02": "86c7d3f0-7889-5d9a-8134-c1ed9ff24147",
}


def resolve_station_id(station_id: str) -> str:
    key = (station_id or "").strip().lower()
    return STATION_ALIASES.get(key, station_id.strip())


def _as_uuid(station_id: str):
    """asyncpg prefers UUID objects for UUID columns."""
    resolved = resolve_station_id(station_id)
    try:
        return uuid.UUID(resolved)
    except (ValueError, AttributeError, TypeError):
        return resolved


def _serialize_station(row) -> dict:
    d = dict(row)
    return {
        "id": str(d["id"]),
        "name": d["name"],
        "type": d["type"],
        "city": d["city"],
        "state": d["state"],
        "capacity": d["capacity"],
        "peak_hours": d.get("peak_hours"),
        "lat": float(d["lat"]) if d.get("lat") is not None else None,
        "lng": float(d["lng"]) if d.get("lng") is not None else None,
        "amenities": list(d["amenities"]) if d.get("amenities") else [],
        "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
    }


async def _add_crowd(station: dict, hour: int, dow: int) -> dict:
    cached = crowd_live_cache.get_station_crowd(station["id"])
    if cached and cached.get("crowd_score") is not None:
        return {
            **station,
            "crowd_score": cached.get("crowd_score"),
            "crowd_level": cached.get("crowd_level"),
            "estimated_people": cached.get("estimated_people"),
            "data_source": cached.get("data_source", "live"),
            "traffic_speed_kmh": cached.get("traffic_speed_kmh"),
        }

    city = station.get("city") or "Bangalore"
    stype = station.get("type") or "bus"
    # Request paths never wait for external traffic providers. The background
    # updater replaces this deterministic baseline with live cached values.
    pred = baseline_crowd_score(stype, hour, dow, city)

    score = pred.get("score")
    return {
        **station,
        "crowd_score": score,
        "crowd_level": pred.get("level"),
        "estimated_people": round((score / 100) * station["capacity"]) if score is not None else None,
        "data_source": pred.get("source", "baseline"),
        "traffic_speed_kmh": pred.get("current_speed_kmh"),
    }


async def get_all_stations(pool) -> list:
    now = now_ist()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM stations ORDER BY city, name")
    stations = [_serialize_station(r) for r in rows]
    return list(await asyncio.gather(*[
        _add_crowd(s, now.hour, now.weekday()) for s in stations
    ]))


async def get_station_by_id(pool, station_id: str) -> Optional[dict]:
    sid = _as_uuid(station_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM stations WHERE id = $1", sid)
    if not row:
        return None
    now = now_ist()
    return await _add_crowd(_serialize_station(row), now.hour, now.weekday())


async def get_stations_by_city(pool, city: str) -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM stations WHERE LOWER(city) = LOWER($1) ORDER BY name", city
        )
    now = now_ist()
    stations = [_serialize_station(r) for r in rows]
    return list(await asyncio.gather(*[
        _add_crowd(s, now.hour, now.weekday()) for s in stations
    ]))


async def get_stations_by_type(pool, station_type: str) -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM stations WHERE type = $1 ORDER BY city, name", station_type
        )
    now = now_ist()
    stations = [_serialize_station(r) for r in rows]
    return list(await asyncio.gather(*[
        _add_crowd(s, now.hour, now.weekday()) for s in stations
    ]))
