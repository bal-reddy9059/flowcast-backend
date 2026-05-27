"""
WebSocket endpoint for real-time all-India district traffic.

Connect: ws://host/api/v1/india/ws/districts

On connect  → full snapshot of all cached district readings sent immediately.
On update   → individual district update broadcast by district_collector.
Ping/pong   → server sends {"type":"ping"} every 30 s; client should reply
              {"type":"pong"} (or the connection stays alive via TCP keepalive).

REST fallback:
  GET /api/v1/india/districts          — paginated list of all districts
  GET /api/v1/india/districts/{name}   — single district latest reading
  GET /api/v1/india/districts/state/{state} — filter by state
"""

import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import TrafficRecord
from app.services.district_collector import get_district_snapshot, _simulate_district, _estimate_vehicles
from app.services.india_districts import INDIA_DISTRICTS, STATE_NAMES

import asyncio
from typing import List

router = APIRouter(prefix="/india", tags=["India Districts"])
logger = logging.getLogger(__name__)

# Common Indian state abbreviations → full names
_STATE_ABBR: dict[str, str] = {
    "ap": "Andhra Pradesh",
    "ar": "Arunachal Pradesh",
    "as": "Assam",
    "br": "Bihar",
    "cg": "Chhattisgarh",
    "ga": "Goa",
    "gj": "Gujarat",
    "hr": "Haryana",
    "hp": "Himachal Pradesh",
    "jk": "Jammu and Kashmir",
    "jh": "Jharkhand",
    "ka": "Karnataka",
    "kl": "Kerala",
    "mp": "Madhya Pradesh",
    "mh": "Maharashtra",
    "mn": "Manipur",
    "ml": "Meghalaya",
    "mz": "Mizoram",
    "nl": "Nagaland",
    "or": "Odisha",
    "pb": "Punjab",
    "rj": "Rajasthan",
    "sk": "Sikkim",
    "tn": "Tamil Nadu",
    "ts": "Telangana",
    "tr": "Tripura",
    "up": "Uttar Pradesh",
    "uk": "Uttarakhand",
    "wb": "West Bengal",
    "dl": "Delhi",
    "ch": "Chandigarh",
}


def _district_matches(search: str, district: str) -> bool:
    """Fuzzy district name match — handles Indian alternate spellings (e.g. Anantapur/Ananthapur).

    Returns True if:
    - search is a substring of district (or vice versa), OR
    - SequenceMatcher similarity >= 0.75 (catches off-by-one-letter variants)
    """
    s, d = search.lower(), district.lower()
    if s in d or d in s:
        return True
    return SequenceMatcher(None, s, d).ratio() >= 0.75


# Anonymous WebSocket pool for district broadcast (no auth required)
_district_sockets: List[WebSocket] = []


async def _district_broadcast(message: dict) -> None:
    dead = []
    for ws in list(_district_sockets):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _district_sockets:
            _district_sockets.remove(ws)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/ws/districts")
async def district_ws(websocket: WebSocket):
    """
    Real-time WebSocket stream of all-India district traffic.
    Sends full snapshot on connect, then live updates as they arrive.
    """
    await websocket.accept()
    _district_sockets.append(websocket)
    logger.info("District WS client connected: %s  (total=%d)", websocket.client, len(_district_sockets))

    try:
        # Send full snapshot immediately on connect
        snapshot = get_district_snapshot()
        await websocket.send_json({
            "type": "snapshot",
            "total_districts": len(INDIA_DISTRICTS),
            "cached_districts": len(snapshot),
            "districts": snapshot,
        })

        # Keep connection alive, sending pings every 30 s
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                if data.get("type") == "pong":
                    pass
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        logger.info("District WS client disconnected: %s", websocket.client)
    except Exception as exc:
        logger.error("District WS error: %s", exc)
    finally:
        if websocket in _district_sockets:
            _district_sockets.remove(websocket)


# ── REST fallbacks ────────────────────────────────────────────────────────────

@router.get("/districts", status_code=status.HTTP_200_OK)
def list_districts(
    state:  Optional[str] = Query(None, description="Filter by state name"),
    search: Optional[str] = Query(None, description="Search district name (partial match)"),
    congestion: Optional[str] = Query(None, description="Filter: low / medium / high"),
    page:   int = Query(1, ge=1),
    size:   int = Query(50, ge=1, le=200),
) -> dict:
    """
    All Indian districts with their latest cached traffic reading.
    Falls back to district metadata (no live data) if collector hasn't run yet.
    """
    cache = {d["district"]: d for d in get_district_snapshot()}

    districts = INDIA_DISTRICTS
    if state:
        # Resolve abbreviation (e.g. "AP" → "Andhra Pradesh") then partial match
        resolved_state = _STATE_ABBR.get(state.lower(), state)
        districts = [d for d in districts if resolved_state.lower() in d["state"].lower()]
    if search:
        districts = [d for d in districts if _district_matches(search, d["district"])]

    all_entries = []
    for d in districts:
        cached = cache.get(d["district"])
        if cached:
            entry = dict(cached)
            entry.setdefault("district", d["district"])
            entry.setdefault("state",    d["state"])
            entry.setdefault("lat",      d["lat"])
            entry.setdefault("lng",      d["lng"])
        else:
            # Collector hasn't reached this district yet — use simulation
            sim = _simulate_district(d["lat"], d["lng"])
            entry = {
                "district":         d["district"],
                "state":            d["state"],
                "lat":              d["lat"],
                "lng":              d["lng"],
                "speed_kmh":        sim["speed_kmh"],
                "congestion_level": sim["congestion_level"],
                "vehicle_count":    _estimate_vehicles(sim["speed_kmh"]),
                "congestion_ratio": sim["congestion_ratio"],
                "source":           "simulated",
                "updated_at":       datetime.now(timezone.utc).isoformat(),
            }
        all_entries.append(entry)

    # Apply congestion filter; fall back to all entries when filter matches nothing
    congestion_note = None
    if congestion:
        filtered = [e for e in all_entries if e["congestion_level"] == congestion]
        if filtered:
            results = filtered
        else:
            results = all_entries
            actual = list({e["congestion_level"] for e in all_entries})
            congestion_note = (
                f"No districts with '{congestion}' congestion right now. "
                f"Showing all {len(results)} matched district(s). "
                f"Current levels: {', '.join(actual) or 'unknown'}."
            )
    else:
        results = all_entries

    total  = len(results)
    start  = (page - 1) * size
    paginated = results[start: start + size]

    response = {
        "total":     total,
        "page":      page,
        "size":      size,
        "pages":     max(1, -(-total // size)),
        "districts": paginated,
    }
    if congestion_note:
        response["congestion_filter_note"] = congestion_note
    return response


@router.get("/districts/state/{state_name}", status_code=status.HTTP_200_OK)
def districts_by_state(state_name: str) -> dict:
    """All districts in a given state with live traffic data."""
    cache = {d["district"]: d for d in get_district_snapshot()}
    matched = [d for d in INDIA_DISTRICTS if d["state"].lower() == state_name.lower()]
    if not matched:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"State '{state_name}' not found")

    results = []
    for d in matched:
        cached = cache.get(d["district"])
        if cached:
            results.append(dict(cached))
        else:
            sim = _simulate_district(d["lat"], d["lng"])
            results.append({
                "district":         d["district"],
                "state":            d["state"],
                "lat":              d["lat"],
                "lng":              d["lng"],
                "speed_kmh":        sim["speed_kmh"],
                "congestion_level": sim["congestion_level"],
                "vehicle_count":    _estimate_vehicles(sim["speed_kmh"]),
                "congestion_ratio": sim["congestion_ratio"],
                "source":           "simulated",
                "updated_at":       datetime.now(timezone.utc).isoformat(),
            })

    high   = sum(1 for r in results if r.get("congestion_level") == "high")
    medium = sum(1 for r in results if r.get("congestion_level") == "medium")
    low    = sum(1 for r in results if r.get("congestion_level") == "low")

    return {
        "state": matched[0]["state"],
        "total_districts": len(results),
        "congestion_summary": {"high": high, "medium": medium, "low": low},
        "districts": results,
    }


@router.get("/districts/{district_name}", status_code=status.HTTP_200_OK)
def district_detail(district_name: str) -> dict:
    """Latest traffic data for a specific district."""
    cache = {d["district"].lower(): d for d in get_district_snapshot()}
    entry = cache.get(district_name.lower())
    if entry:
        return entry

    # Fall back to metadata if not yet collected
    meta = next(
        (d for d in INDIA_DISTRICTS if d["district"].lower() == district_name.lower()),
        None,
    )
    if not meta:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"District '{district_name}' not found")

    sim = _simulate_district(meta["lat"], meta["lng"])
    return {
        "district":         meta["district"],
        "state":            meta["state"],
        "lat":              meta["lat"],
        "lng":              meta["lng"],
        "speed_kmh":        sim["speed_kmh"],
        "congestion_level": sim["congestion_level"],
        "vehicle_count":    _estimate_vehicles(sim["speed_kmh"]),
        "congestion_ratio": sim["congestion_ratio"],
        "source":           "simulated",
        "updated_at":       datetime.now(timezone.utc).isoformat(),
    }


@router.get("/districts-states", status_code=status.HTTP_200_OK)
def list_district_states() -> dict:
    """List all Indian states available in the districts dataset."""
    return {
        "total_states": len(STATE_NAMES),
        "states": sorted(STATE_NAMES),
    }
