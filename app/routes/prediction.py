"""
Area-level traffic prediction endpoints.

GET /api/v1/traffic/area/predict?area=Gachibowli
  → current status, 12-hour forecast, best/worst travel time, hourly & weekly patterns

GET /api/v1/traffic/area/search?city=hyderabad&q=gachi
  → search areas within a city

GET /api/v1/traffic/area/cities
  → list all supported cities with area counts
"""

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Optional
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import TrafficRecord

router = APIRouter(prefix="/traffic/area", tags=["Area Prediction"])
logger = logging.getLogger(__name__)

# ── City → area registry ──────────────────────────────────────────────────────

CITY_AREAS: dict[str, list[dict]] = {
    "Hyderabad": [
        {"name": "Hitech City",   "lat": 17.4486, "lng": 78.3908},
        {"name": "Gachibowli",    "lat": 17.4401, "lng": 78.3489},
        {"name": "Madhapur",      "lat": 17.4504, "lng": 78.3908},
        {"name": "Banjara Hills", "lat": 17.4156, "lng": 78.4485},
        {"name": "Jubilee Hills", "lat": 17.4324, "lng": 78.4073},
        {"name": "Kondapur",      "lat": 17.4706, "lng": 78.3487},
        {"name": "Kukatpally",    "lat": 17.4848, "lng": 78.4138},
        {"name": "LB Nagar",      "lat": 17.3490, "lng": 78.5480},
        {"name": "Secunderabad",  "lat": 17.4399, "lng": 78.4983},
        {"name": "Ameerpet",      "lat": 17.4375, "lng": 78.4483},
        {"name": "KPHB Colony",   "lat": 17.4914, "lng": 78.3942},
        {"name": "Mehdipatnam",   "lat": 17.3956, "lng": 78.4307},
        {"name": "Begumpet",      "lat": 17.4402, "lng": 78.4687},
        {"name": "Dilsukhnagar",  "lat": 17.3688, "lng": 78.5271},
        {"name": "Miyapur",       "lat": 17.4964, "lng": 78.3376},
    ],
    "Bangalore": [
        {"name": "MG Road, Bangalore", "lat": 12.9758, "lng": 77.6082},
        {"name": "Koramangala",        "lat": 12.9352, "lng": 77.6245},
        {"name": "Indiranagar",        "lat": 12.9784, "lng": 77.6408},
        {"name": "Whitefield",         "lat": 12.9698, "lng": 77.7500},
        {"name": "Electronic City",    "lat": 12.8399, "lng": 77.6770},
        {"name": "Silk Board Junction","lat": 12.9174, "lng": 77.6228},
        {"name": "Hebbal Flyover",     "lat": 13.0450, "lng": 77.5966},
    ],
    "Mumbai": [
        {"name": "Dadar",              "lat": 19.0178, "lng": 72.8478},
        {"name": "Worli Sea Link",     "lat": 19.0176, "lng": 72.8146},
        {"name": "Powai",              "lat": 19.1176, "lng": 72.9060},
        {"name": "Thane",              "lat": 19.2183, "lng": 72.9781},
        {"name": "Marine Drive, Mumbai","lat": 18.9438,"lng": 72.8230},
        {"name": "Bandra Kurla Complex","lat": 19.0660,"lng": 72.8654},
        {"name": "Andheri West",       "lat": 19.1197, "lng": 72.8468},
    ],
    "Delhi": [
        {"name": "Connaught Place",    "lat": 28.6315, "lng": 77.2167},
        {"name": "Lajpat Nagar",       "lat": 28.5673, "lng": 77.2378},
        {"name": "Rohini",             "lat": 28.7041, "lng": 77.1025},
        {"name": "Dwarka",             "lat": 28.5921, "lng": 77.0460},
        {"name": "Noida Expressway",   "lat": 28.5355, "lng": 77.3910},
        {"name": "Cyber City Gurgaon", "lat": 28.4952, "lng": 77.0928},
        {"name": "Faridabad",          "lat": 28.4089, "lng": 77.3178},
    ],
    "Chennai": [
        {"name": "Anna Nagar",         "lat": 13.0850, "lng": 80.2101},
        {"name": "Anna Salai, Chennai","lat": 13.0569, "lng": 80.2425},
        {"name": "T Nagar Chennai",    "lat": 13.0418, "lng": 80.2341},
        {"name": "Tambaram",           "lat": 12.8996, "lng": 80.2209},
        {"name": "Guindy",             "lat": 13.0067, "lng": 80.2206},
        {"name": "OMR Road Chennai",   "lat": 12.9279, "lng": 80.2304},
    ],
    "Kolkata": [
        {"name": "Howrah Bridge",      "lat": 22.5851, "lng": 88.3468},
        {"name": "Park Street, Kolkata","lat": 22.5520,"lng": 88.3600},
        {"name": "Salt Lake Sector V", "lat": 22.5753, "lng": 88.4307},
        {"name": "Dum Dum",            "lat": 22.6542, "lng": 88.3942},
    ],
}

# Reverse map: area name → city
_AREA_TO_CITY: dict[str, str] = {
    a["name"].lower(): city
    for city, areas in CITY_AREAS.items()
    for a in areas
}

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_CONGESTION_ORDER = {"low": 0, "medium": 1, "high": 2}
_SPEED_FOR_CONGESTION = {"low": 45.0, "medium": 28.0, "high": 12.0}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hour_label(h: int) -> str:
    if h == 0:   return "12:00 AM"
    if h < 12:   return f"{h}:00 AM"
    if h == 12:  return "12:00 PM"
    return f"{h - 12}:00 PM"


def _resolve_area(name: str) -> tuple[str, str]:
    """Return (canonical_name, city) for an area, or raise 404."""
    name_lower = name.lower()
    for city, areas in CITY_AREAS.items():
        for a in areas:
            if name_lower in a["name"].lower() or a["name"].lower() in name_lower:
                return a["name"], city
    raise HTTPException(
        status_code=404,
        detail=f"Area '{name}' not found. Use /traffic/area/search to discover available areas.",
    )


def _fetch_records(area: str, db: Session, days: int = 30) -> list:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.location.ilike(f"%{area}%"),
            TrafficRecord.created_at >= since,
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )


def _record_hour_ist(r) -> int:
    """Return the IST hour for a traffic record's created_at."""
    ts = r.created_at
    if ts is None:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(_IST).hour


def _build_hourly_pattern(records: list) -> dict:
    """
    Returns {hour: {congestion, avg_speed_kmh, sample_size}} for hours 0-23.
    Uses only same-weekday-type (weekday vs weekend) records for accuracy.
    """
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() >= 5

    # Prefer same-type day records; fall back to all records if too few
    typed = [r for r in records if (r.created_at.weekday() >= 5) == is_weekend]
    pool  = typed if len(typed) >= 20 else records

    by_hour: dict[int, list] = defaultdict(list)
    for r in pool:
        by_hour[_record_hour_ist(r)].append(r)

    pattern = {}
    for h in range(24):
        hrs = by_hour.get(h, [])
        if not hrs:
            pattern[h] = {"congestion": "unknown", "avg_speed_kmh": None, "sample_size": 0}
            continue
        counts = Counter(r.congestion_level for r in hrs)
        dominant = counts.most_common(1)[0][0]
        speeds = [r.average_speed for r in hrs if r.average_speed]
        pattern[h] = {
            "congestion": dominant,
            "avg_speed_kmh": round(mean(speeds), 1) if speeds else _SPEED_FOR_CONGESTION.get(dominant),
            "sample_size": len(hrs),
        }
    return pattern


def _build_weekly_pattern(records: list) -> dict:
    by_day: dict[int, list] = defaultdict(list)
    for r in records:
        by_day[r.created_at.weekday()].append(r)

    result = {}
    for idx, day in enumerate(_DAY_NAMES):
        recs = by_day.get(idx, [])
        if not recs:
            result[day] = {"congestion": "unknown", "avg_speed_kmh": None, "sample_size": 0}
            continue
        counts = Counter(r.congestion_level for r in recs)
        dominant = counts.most_common(1)[0][0]
        speeds = [r.average_speed for r in recs if r.average_speed]
        result[day] = {
            "congestion":    dominant,
            "avg_speed_kmh": round(mean(speeds), 1) if speeds else _SPEED_FOR_CONGESTION.get(dominant),
            "sample_size":   len(recs),
        }
    return result


def _predict_hour(hour_pattern: dict, target_hour: int, records: list) -> dict:
    p = hour_pattern.get(target_hour, {})
    if not p or p["congestion"] == "unknown" or p["sample_size"] < 3:
        # Fall back to overall prediction
        if not records:
            return {"predicted_congestion": "medium", "confidence": 0.1,
                    "avg_speed_kmh": 28.0}
        counts = Counter(r.congestion_level for r in records)
        dominant = counts.most_common(1)[0][0]
        return {"predicted_congestion": dominant, "confidence": 0.3,
                "avg_speed_kmh": _SPEED_FOR_CONGESTION.get(dominant, 28.0)}

    n = p["sample_size"]
    congestion = p["congestion"]
    # confidence scales with sample size
    confidence = min(0.95, 0.5 + (n / 60) * 0.45)
    return {
        "predicted_congestion": congestion,
        "confidence": round(confidence, 2),
        "avg_speed_kmh": p["avg_speed_kmh"],
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/predict", status_code=status.HTTP_200_OK)
def predict_area_traffic(
    area: str = Query(..., min_length=2, description="Area/neighbourhood name (e.g. Gachibowli)"),
    hours_ahead: int = Query(12, ge=1, le=24, description="How many hours to forecast"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Full traffic prediction for a specific city area.

    Returns:
    - **current** — live snapshot (latest DB record)
    - **forecast** — hourly predictions for the next N hours
    - **best_time** — lowest-congestion hour in forecast window
    - **worst_time** — highest-congestion hour in forecast window
    - **hourly_pattern** — 24-hour typical pattern from 30-day history
    - **weekly_pattern** — congestion by day of week
    - **recommendation** — plain-English travel advice
    """
    canonical, city = _resolve_area(area)
    records = _fetch_records(canonical, db)

    # ── Current (latest record) ───────────────────────────────────────────────
    latest = None
    if records:
        latest = max(records, key=lambda r: r.created_at)

    current = {
        "congestion_level": latest.congestion_level if latest else "unknown",
        "avg_speed_kmh":    round(latest.average_speed, 1) if latest and latest.average_speed else None,
        "vehicle_count":    latest.vehicle_count if latest else None,
        "updated_at":       latest.created_at.isoformat() if latest else None,
        "data_age_minutes": round(
            (datetime.now(timezone.utc) - (
                latest.created_at if latest.created_at.tzinfo
                else latest.created_at.replace(tzinfo=timezone.utc)
            )).total_seconds() / 60, 1
        ) if latest else None,
    }

    # ── Patterns ─────────────────────────────────────────────────────────────
    hourly_pattern = _build_hourly_pattern(records)
    weekly_pattern = _build_weekly_pattern(records)

    # ── Forecast ─────────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)
    forecast = []
    for h_offset in range(1, hours_ahead + 1):
        target_dt   = now + timedelta(hours=h_offset)
        target_hour = target_dt.hour
        pred        = _predict_hour(hourly_pattern, target_hour, records)
        forecast.append({
            "offset_hours":          h_offset,
            "time_label":            _hour_label(target_hour),
            "predicted_congestion":  pred["predicted_congestion"],
            "confidence":            pred["confidence"],
            "avg_speed_kmh":         pred["avg_speed_kmh"],
        })

    # ── Best / worst travel window ────────────────────────────────────────────
    def _sort_key(f):
        return (_CONGESTION_ORDER.get(f["predicted_congestion"], 1), -(f["avg_speed_kmh"] or 0))

    sorted_forecast = sorted(forecast, key=_sort_key)
    best  = sorted_forecast[0]  if sorted_forecast else None
    worst = sorted_forecast[-1] if sorted_forecast else None

    # ── Plain-English recommendation ─────────────────────────────────────────
    curr_level = current["congestion_level"]
    if curr_level == "high":
        rec = (
            f"Traffic in {canonical} is currently heavy. "
            f"Best time to travel is around {best['time_label']} "
            f"({best['predicted_congestion']} congestion)."
            if best else f"Avoid {canonical} right now — heavy congestion."
        )
    elif curr_level == "medium":
        rec = (
            f"Moderate traffic in {canonical}. "
            f"Consider leaving around {best['time_label']} for lighter conditions."
            if best else f"Moderate traffic in {canonical} — plan for some delay."
        )
    else:
        rec = f"Traffic is light in {canonical}. Good time to travel."

    # ── Summary stats ─────────────────────────────────────────────────────────
    high_hours = sorted(h for h, p in hourly_pattern.items() if p["congestion"] == "high")

    def _detect_windows(hours: list[int]) -> list[tuple[int, int]]:
        """Group consecutive hours into windows, e.g. [7,8,9,17,18] → [(7,9),(17,18)]."""
        if not hours:
            return []
        windows, start, prev = [], hours[0], hours[0]
        for h in hours[1:]:
            if h - prev > 1:
                windows.append((start, prev))
                start = h
            prev = h
        windows.append((start, prev))
        return windows

    windows = _detect_windows(high_hours)
    if not windows:
        peak_hours_label = "No peak detected"
    else:
        peak_hours_label = ", ".join(
            f"{_hour_label(s)} – {_hour_label(e)}" for s, e in windows
        )

    return {
        "area":              canonical,
        "city":              city,
        "current":           current,
        "forecast":          forecast,
        "best_travel_time":  {
            "offset_hours":         best["offset_hours"],
            "time_label":           best["time_label"],
            "predicted_congestion": best["predicted_congestion"],
            "avg_speed_kmh":        best["avg_speed_kmh"],
        } if best else None,
        "worst_travel_time": {
            "offset_hours":         worst["offset_hours"],
            "time_label":           worst["time_label"],
            "predicted_congestion": worst["predicted_congestion"],
            "avg_speed_kmh":        worst["avg_speed_kmh"],
        } if worst else None,
        "hourly_pattern":    hourly_pattern,
        "weekly_pattern":    weekly_pattern,
        "peak_hours":        peak_hours_label,
        "historical_records_used": len(records),
        "recommendation":    rec,
        "generated_at":      now.isoformat(),
    }


@router.get("/compare", status_code=status.HTTP_200_OK)
def compare_areas(
    areas: str = Query(..., description="Comma-separated area names, e.g. Gachibowli,Hitech City,Ameerpet"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Compare current traffic conditions across multiple areas in the same city.
    Useful for choosing the least congested route or neighbourhood.
    """
    area_list = [a.strip() for a in areas.split(",") if a.strip()]
    if len(area_list) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 comma-separated area names")
    if len(area_list) > 8:
        raise HTTPException(status_code=400, detail="Maximum 8 areas per comparison")

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=2)
    results = []

    for area_name in area_list:
        try:
            canonical, city = _resolve_area(area_name)
        except HTTPException:
            results.append({"area": area_name, "error": "Area not found"})
            continue

        latest = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.location.ilike(f"%{canonical}%"),
                TrafficRecord.created_at >= since,
            )
            .order_by(TrafficRecord.created_at.desc())
            .first()
        )
        if not latest:
            # Simulation fallback so the area is still included in best/worst ranking
            from app.services.realtime_collector import _simulate_flow
            from app.services.tomtom_service import classify_congestion, estimate_vehicle_count
            area_meta = next(
                (a for areas in CITY_AREAS.values() for a in areas if a["name"] == canonical),
                None,
            )
            if area_meta:
                flow = _simulate_flow(area_meta["lat"], area_meta["lng"])
                cur = float(flow["currentSpeed"])
                free = float(flow["freeFlowSpeed"])
                cong = classify_congestion(cur, free)
                vc = estimate_vehicle_count(cur, free)
            else:
                cur, cong, vc = 35.0, "medium", 500
            results.append({
                "area": canonical, "city": city,
                "congestion_level": cong,
                "avg_speed_kmh": round(cur, 1),
                "vehicle_count": vc,
                "updated_at": now.astimezone(_IST).isoformat(),
                "data_source": "simulated",
            })
        else:
            ts = latest.created_at
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            results.append({
                "area": canonical, "city": city,
                "congestion_level": latest.congestion_level,
                "avg_speed_kmh": round(latest.average_speed, 1) if latest.average_speed else None,
                "vehicle_count": latest.vehicle_count,
                "updated_at": ts.astimezone(_IST).isoformat() if ts else None,
                "data_source": "live",
            })

    known = [r for r in results if "error" not in r and r["congestion_level"] not in ("unknown", None)]
    # Tiebreak by avg_speed_kmh: best = lowest congestion + highest speed; worst = reverse
    _cmp_key = lambda r: (_CONGESTION_ORDER.get(r["congestion_level"], 1), -(r["avg_speed_kmh"] or 0))
    best  = min(known, key=_cmp_key) if known else None
    worst = max(known, key=_cmp_key) if known else None

    return {
        "areas_compared": len(results),
        "best_area":  best["area"]  if best  else None,
        "worst_area": worst["area"] if worst else None,
        "results": results,
        "generated_at": now.isoformat(),
    }


@router.get("/search", status_code=status.HTTP_200_OK)
def search_areas(
    city: Optional[str] = Query(None, description="City name (e.g. Hyderabad)"),
    q:    Optional[str] = Query(None, description="Partial area name search"),
    db: Session = Depends(get_db),
) -> dict:
    """Search for areas/neighbourhoods with live traffic status, optionally filtered by city."""
    from app.services.realtime_collector import _simulate_flow
    from app.services.tomtom_service import classify_congestion, estimate_vehicle_count

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=2)

    matched = []
    for c, areas in CITY_AREAS.items():
        if city and city.lower() not in c.lower():
            continue
        for a in areas:
            if q and q.lower() not in a["name"].lower():
                continue
            matched.append((c, a))

    results = []
    for c, a in matched:
        latest = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.location.ilike(f"%{a['name']}%"),
                TrafficRecord.created_at >= since,
            )
            .order_by(TrafficRecord.created_at.desc())
            .first()
        )
        if latest:
            ts = latest.created_at
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            results.append({
                "area":             a["name"],
                "city":             c,
                "lat":              a["lat"],
                "lng":              a["lng"],
                "congestion_level": latest.congestion_level,
                "avg_speed_kmh":    round(latest.average_speed, 1) if latest.average_speed else None,
                "vehicle_count":    latest.vehicle_count,
                "updated_at":       ts.astimezone(_IST).isoformat() if ts else None,
                "data_source":      "live",
            })
        else:
            flow = _simulate_flow(a["lat"], a["lng"])
            cur  = float(flow["currentSpeed"])
            free = float(flow["freeFlowSpeed"])
            results.append({
                "area":             a["name"],
                "city":             c,
                "lat":              a["lat"],
                "lng":              a["lng"],
                "congestion_level": classify_congestion(cur, free),
                "avg_speed_kmh":    round(cur, 1),
                "vehicle_count":    estimate_vehicle_count(cur, free),
                "updated_at":       now.astimezone(_IST).isoformat(),
                "data_source":      "simulated",
            })

    return {
        "total": len(results),
        "areas": results,
    }


@router.get("/cities", status_code=status.HTTP_200_OK)
def list_cities(db: Session = Depends(get_db)) -> dict:
    """List all supported cities with area counts and live traffic summary."""
    from app.services.realtime_collector import _simulate_flow
    from app.services.tomtom_service import classify_congestion, estimate_vehicle_count

    now   = datetime.now(timezone.utc)
    since = now - timedelta(hours=2)
    cities = []

    for c, areas in CITY_AREAS.items():
        area_names = [a["name"] for a in areas]

        # Fetch latest record per area (last 2 hours)
        records = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.location.in_(area_names),
                TrafficRecord.created_at >= since,
                TrafficRecord.congestion_level.isnot(None),
            )
            .all()
        )

        if records:
            speeds  = [r.average_speed for r in records if r.average_speed]
            counts  = Counter(r.congestion_level for r in records)
            dominant = counts.most_common(1)[0][0]
            avg_speed = round(mean(speeds), 1) if speeds else None
            high_pct  = counts.get("high", 0) / len(records) * 100
            med_pct   = counts.get("medium", 0) / len(records) * 100
            health    = round(max(0, 100 - high_pct * 0.6 - med_pct * 0.2), 1)
            data_source = "live"
        else:
            # Simulate using centre point of first area
            first = areas[0]
            flow  = _simulate_flow(first["lat"], first["lng"])
            cur   = float(flow["currentSpeed"])
            free  = float(flow["freeFlowSpeed"])
            dominant  = classify_congestion(cur, free)
            avg_speed = round(cur, 1)
            health    = {"low": 85.0, "medium": 60.0, "high": 30.0}.get(dominant, 60.0)
            data_source = "simulated"

        cities.append({
            "city":               c,
            "area_count":         len(areas),
            "areas":              area_names,
            "dominant_congestion": dominant,
            "avg_speed_kmh":      avg_speed,
            "health_score":       health,
            "data_source":        data_source,
        })

    cities.sort(key=lambda x: x["health_score"])
    return {
        "total_cities": len(cities),
        "generated_at": now.astimezone(_IST).isoformat(),
        "cities": cities,
    }
