"""All-India real-time traffic endpoints."""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import Incident, TrafficRecord
from app.services.india_locations import CITIES, INDIA_LOCATIONS, LOCATION_MAP, STATES
from app.services.realtime_collector import _simulate_flow
from app.services.tomtom_service import classify_congestion, estimate_vehicle_count

router = APIRouter(prefix="/india", tags=["India Traffic"])
logger = logging.getLogger(__name__)

_CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}


def _latest_records(locations: list[str], db: Session) -> dict[str, TrafficRecord]:
    """Fetch one latest observation per location in a single round trip."""
    rows = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.location.in_(locations))
        .distinct(TrafficRecord.location)
        .order_by(TrafficRecord.location, TrafficRecord.created_at.desc())
        .all()
    )
    return {row.location: row for row in rows}


def _grade(score: float) -> tuple[str, str]:
    if score >= 80: return "A", "green"
    if score >= 65: return "B", "light-green"
    if score >= 50: return "C", "yellow"
    if score >= 35: return "D", "orange"
    return "F", "red"


# ── 1. Live snapshot for every monitored location ─────────────────────────────

@router.get("/live", status_code=status.HTTP_200_OK)
def india_live_snapshot(
    state: Optional[str] = Query(None, description="Filter by state name"),
    city:  Optional[str] = Query(None, description="Filter by city name"),
    congestion: Optional[str] = Query(None, description="Filter: low / medium / high"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Live traffic status for all India monitoring points.
    Returns current speed, congestion level and data freshness for each location.
    """
    locs = INDIA_LOCATIONS
    if state:
        locs = [l for l in locs if state.lower() in l["state"].lower()]
    if city:
        locs = [l for l in locs if city.lower() in l["city"].lower()]

    now = datetime.now(timezone.utc)
    all_items = []
    latest_records = _latest_records([loc["name"] for loc in locs], db)
    for loc in locs:
        rec = latest_records.get(loc["name"])
        if rec is None:
            flow       = _simulate_flow(loc["lat"], loc["lng"])
            cur_speed  = float(flow["currentSpeed"])
            free_speed = float(flow["freeFlowSpeed"])
            item = {
                "location":          loc["name"],
                "city":              loc["city"],
                "state":             loc["state"],
                "lat":               loc["lat"],
                "lng":               loc["lng"],
                "congestion_level":  classify_congestion(cur_speed, free_speed),
                "average_speed_kmh": round(cur_speed, 1),
                "vehicle_count":     estimate_vehicle_count(cur_speed, free_speed),
                "data_age_minutes":  None,
                "road_type":         loc["road_type"],
                "data_source":       "simulated",
            }
        else:
            if rec.created_at:
                ts  = rec.created_at if rec.created_at.tzinfo else rec.created_at.replace(tzinfo=timezone.utc)
                age = round((now - ts).total_seconds() / 60, 1)
            else:
                age = None
            item = {
                "location":          loc["name"],
                "city":              loc["city"],
                "state":             loc["state"],
                "lat":               loc["lat"],
                "lng":               loc["lng"],
                "congestion_level":  rec.congestion_level or "unknown",
                "average_speed_kmh": rec.average_speed,
                "vehicle_count":     rec.vehicle_count,
                "data_age_minutes":  age,
                "road_type":         loc["road_type"],
                "data_source":       "live",
            }
        all_items.append(item)

    # Apply congestion filter; fall back to all items if the filter matches nothing
    # (e.g. current hour has no "low" congestion anywhere — avoid a silent empty list).
    congestion_note = None
    if congestion:
        filtered = [i for i in all_items if i["congestion_level"] == congestion]
        if filtered:
            results = filtered
        else:
            results = all_items
            actual = list({i["congestion_level"] for i in all_items})
            congestion_note = (
                f"No locations with '{congestion}' congestion right now. "
                f"Showing all {len(results)} matched location(s). "
                f"Current levels: {', '.join(actual) or 'unknown'}."
            )
    else:
        results = all_items

    high_count   = sum(1 for r in results if r["congestion_level"] == "high")
    medium_count = sum(1 for r in results if r["congestion_level"] == "medium")
    low_count    = sum(1 for r in results if r["congestion_level"] == "low")

    response = {
        "total_locations": len(results),
        "summary":         {"high": high_count, "medium": medium_count, "low": low_count},
        "snapshot_at":     now.isoformat(),
        "locations":       results,
    }
    if congestion_note:
        response["congestion_filter_note"] = congestion_note
    return response


# ── 2. City-level summary ─────────────────────────────────────────────────────

@router.get("/cities", status_code=status.HTTP_200_OK)
def india_cities_summary(
    db: Session = Depends(get_db),
) -> dict:
    """
    Aggregated traffic health per city — average speed, dominant congestion,
    health score A–F, and active incident count.
    """
    now    = datetime.now(timezone.utc)
    since  = now - timedelta(hours=1)
    result = []

    city_groups: dict[str, list[dict]] = {}
    for loc in INDIA_LOCATIONS:
        city_groups.setdefault(loc["city"], []).append(loc)

    all_names = [loc["name"] for loc in INDIA_LOCATIONS]
    recent_records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.location.in_(all_names), TrafficRecord.created_at >= since)
        .all()
    )
    records_by_location: dict[str, list[TrafficRecord]] = {}
    for record in recent_records:
        records_by_location.setdefault(record.location, []).append(record)
    incident_counts = Counter(
        location
        for (location,) in db.query(Incident.location)
        .filter(Incident.location.in_(all_names), Incident.is_active.is_(True))
        .all()
    )

    for city, locs in city_groups.items():
        records = [
            record
            for loc in locs
            for record in records_by_location.get(loc["name"], [])
        ]
        state = locs[0]["state"]
        incidents = sum(incident_counts.get(loc["name"], 0) for loc in locs)

        if not records:
            result.append({
                "city": city, "state": state, "monitored_points": len(locs),
                "health_score": 50, "grade": "C", "color": "yellow",
                "avg_speed_kmh": None, "dominant_congestion": "unknown",
                "active_incidents": incidents, "data_available": False,
            })
            continue

        speeds   = [r.average_speed for r in records if r.average_speed]
        counts   = Counter(r.congestion_level for r in records if r.congestion_level)
        total    = len(records)
        high_pct = counts.get("high", 0) / total * 100
        med_pct  = counts.get("medium", 0) / total * 100
        score    = round(max(0, 100 - high_pct * 0.6 - med_pct * 0.2 - incidents * 5), 1)
        grade, color = _grade(score)
        dominant = counts.most_common(1)[0][0] if counts else "unknown"

        result.append({
            "city": city, "state": state, "monitored_points": len(locs),
            "health_score": score, "grade": grade, "color": color,
            "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else None,
            "dominant_congestion": dominant,
            "active_incidents": incidents, "data_available": True,
        })

    result.sort(key=lambda x: x["health_score"])
    return {
        "total_cities": len(result),
        "worst_city":   result[0]["city"]  if result else None,
        "best_city":    result[-1]["city"] if result else None,
        "generated_at": now.isoformat(),
        "cities": result,
    }


# ── 3. State-level heatmap data ───────────────────────────────────────────────

@router.get("/states", status_code=status.HTTP_200_OK)
def india_states_summary(
    db: Session = Depends(get_db),
) -> dict:
    """State-level aggregated traffic health — useful for a choropleth map."""
    now   = datetime.now(timezone.utc)
    since = now - timedelta(hours=1)

    state_groups: dict[str, list[dict]] = {}
    for loc in INDIA_LOCATIONS:
        state_groups.setdefault(loc["state"], []).append(loc)

    all_names = [loc["name"] for loc in INDIA_LOCATIONS]
    recent_records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.location.in_(all_names), TrafficRecord.created_at >= since)
        .all()
    )
    records_by_location: dict[str, list[TrafficRecord]] = {}
    for record in recent_records:
        records_by_location.setdefault(record.location, []).append(record)

    result = []
    for state, locs in state_groups.items():
        records = [
            record
            for loc in locs
            for record in records_by_location.get(loc["name"], [])
        ]
        if not records:
            result.append({"state": state, "cities": len({l["city"] for l in locs}),
                           "health_score": 50, "grade": "C", "avg_speed_kmh": None})
            continue

        speeds = [r.average_speed for r in records if r.average_speed]
        counts = Counter(r.congestion_level for r in records if r.congestion_level)
        total  = len(records)
        score  = round(max(0, 100
                           - counts.get("high", 0) / total * 60
                           - counts.get("medium", 0) / total * 20), 1)
        grade, _ = _grade(score)
        result.append({
            "state": state,
            "cities": len({l["city"] for l in locs}),
            "monitored_points": len(locs),
            "health_score": score,
            "grade": grade,
            "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else None,
        })

    result.sort(key=lambda x: x["health_score"])
    return {"total_states": len(result), "generated_at": now.isoformat(), "states": result}


# ── 4. National congestion overview ──────────────────────────────────────────

@router.get("/overview", status_code=status.HTTP_200_OK)
def india_national_overview(
    db: Session = Depends(get_db),
) -> dict:
    """
    Single-number national traffic health index for India.
    Aggregates last-hour data from all monitored locations.
    """
    now   = datetime.now(timezone.utc)
    since = now - timedelta(hours=1)

    records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.created_at >= since,
                TrafficRecord.congestion_level.isnot(None))
        .all()
    )
    incidents = db.query(Incident).filter(Incident.is_active.is_(True)).count()

    if not records:
        return {
            "national_health_score": 50, "grade": "C", "color": "yellow",
            "total_active_incidents": incidents,
            "message": "No recent data — collector runs every 30 min",
            "generated_at": now.isoformat(),
        }

    total  = len(records)
    counts = Counter(r.congestion_level for r in records)
    speeds = [r.average_speed for r in records if r.average_speed]
    score  = round(max(0, 100
                       - counts.get("high", 0) / total * 60
                       - counts.get("medium", 0) / total * 20
                       - min(incidents * 0.5, 10)), 1)
    grade, color = _grade(score)

    return {
        "national_health_score": score,
        "grade": grade,
        "color": color,
        "total_records_last_hour": total,
        "congestion_breakdown": {
            "high":   counts.get("high", 0),
            "medium": counts.get("medium", 0),
            "low":    counts.get("low", 0),
        },
        "avg_speed_kmh_national": round(sum(speeds) / len(speeds), 1) if speeds else None,
        "total_active_incidents": incidents,
        "monitored_locations": len(INDIA_LOCATIONS),
        "monitored_cities": len(set(l["city"] for l in INDIA_LOCATIONS)),
        "monitored_states": len(set(l["state"] for l in INDIA_LOCATIONS)),
        "generated_at": now.isoformat(),
    }


# ── 5. Worst congestion hotspots (top N) ─────────────────────────────────────

@router.get("/hotspots", status_code=status.HTTP_200_OK)
def india_hotspots(
    limit: int = Query(10, ge=1, le=50, description="Number of worst locations to return"),
    db: Session = Depends(get_db),
) -> dict:
    """Top N most congested locations across India right now.

    Falls back to a 6-hour window when the last 2 hours have no data (e.g. fresh server start).
    """
    now = datetime.now(timezone.utc)
    results = []
    six_hours_ago = now - timedelta(hours=6)
    two_hours_ago = now - timedelta(hours=2)
    rows = (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.location.in_([loc["name"] for loc in INDIA_LOCATIONS]),
            TrafficRecord.created_at >= six_hours_ago,
        )
        .order_by(TrafficRecord.location, TrafficRecord.created_at.desc())
        .all()
    )
    records_by_location: dict[str, list[TrafficRecord]] = {}
    for row in rows:
        records_by_location.setdefault(row.location, []).append(row)

    for loc in INDIA_LOCATIONS:
        available = records_by_location.get(loc["name"], [])
        recent = []
        for row in available:
            created = row.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and created >= two_hours_ago:
                recent.append(row)
        recs = (recent or available)[:3]
        if not recs:
            continue
        latest = recs[0]
        speeds = [r.average_speed for r in recs if r.average_speed]
        avg_speed = sum(speeds) / len(speeds) if speeds else latest.average_speed
        score = _CONGESTION_SCORE.get(latest.congestion_level or "low", 0)
        results.append({
            "location": loc["name"], "city": loc["city"], "state": loc["state"],
            "lat": loc["lat"], "lng": loc["lng"],
            "congestion_level": latest.congestion_level or "low",
            "avg_speed_kmh": round(avg_speed, 1) if avg_speed else None,
            "vehicle_count": latest.vehicle_count,
            "_score": score,
        })

    results.sort(key=lambda x: (-x["_score"], x.get("avg_speed_kmh") or 999))
    for r in results:
        r.pop("_score", None)

    top = results[:limit]
    return {
        "total_evaluated": len(results),
        "hotspots": top,          # primary key (frontend-friendly)
        "top_congested": top,     # backward-compat alias
        "generated_at": now.isoformat(),
    }


# ── 6. Available locations / cities / states ──────────────────────────────────

@router.get("/locations", status_code=status.HTTP_200_OK)
def list_locations(
    city:  Optional[str] = Query(None),
    state: Optional[str] = Query(None),
) -> dict:
    """List all monitored India locations with coordinates."""
    locs = INDIA_LOCATIONS
    if city:
        locs = [l for l in locs if l["city"].lower() == city.lower()]
    if state:
        locs = [l for l in locs if l["state"].lower() == state.lower()]
    return {
        "total": len(locs),
        "cities": sorted({l["city"] for l in locs}),
        "states": sorted({l["state"] for l in locs}),
        "locations": locs,
    }
