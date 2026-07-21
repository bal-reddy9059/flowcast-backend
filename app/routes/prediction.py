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
_LIVE_MAX_AGE = timedelta(hours=6)

# Soft prior when history is sparse / uniformly "low"
_HOUR_PRIOR = {
    7: "medium", 8: "high", 9: "high", 10: "medium",
    11: "low", 12: "low", 13: "low", 14: "low", 15: "medium",
    16: "medium", 17: "high", 18: "high", 19: "high", 20: "medium",
    21: "medium", 22: "low", 23: "low",
    0: "low", 1: "low", 2: "low", 3: "low", 4: "low", 5: "low", 6: "medium",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hour_label(h: int) -> str:
    if h == 0:   return "12:00 AM"
    if h < 12:   return f"{h}:00 AM"
    if h == 12:  return "12:00 PM"
    return f"{h - 12}:00 PM"


def _resolve_area(name: str) -> tuple[str, str]:
    """Return (canonical_name, city) for an area, or raise 404."""
    name_lower = name.lower().strip()
    # Prefer longest name match to avoid "City" colliding oddly
    best = None
    best_len = 0
    for city, areas in CITY_AREAS.items():
        for a in areas:
            an = a["name"].lower()
            if name_lower == an or name_lower in an or an in name_lower:
                if len(an) > best_len:
                    best_len = len(an)
                    best = (a["name"], city)
    if best:
        return best
    raise HTTPException(
        status_code=404,
        detail=f"Area '{name}' not found. Use /traffic/area/search to discover available areas.",
    )


def _area_meta(canonical: str) -> Optional[dict]:
    """Return lat/lng dict for a canonical area name, or None."""
    for areas in CITY_AREAS.values():
        for a in areas:
            if a["name"] == canonical:
                return a
    return None


def _record_ts(r: TrafficRecord) -> Optional[datetime]:
    ts = r.timestamp or r.created_at
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


async def _area_live_snapshot(
    canonical: str,
    city: str,
    db: Session,
    since: datetime,
) -> dict:
    """Current traffic for an area: fresh DB record first, then on-demand flow fetch."""
    from app.services.traffic_flow_service import fetch_flow
    from app.services.tomtom_service import classify_congestion, estimate_vehicle_count
    from sqlalchemy import or_

    now = datetime.now(timezone.utc)

    latest = (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.location.ilike(f"%{canonical}%"),
            or_(
                TrafficRecord.timestamp >= since,
                TrafficRecord.created_at >= since,
            ),
        )
        .order_by(TrafficRecord.timestamp.desc().nullslast(), TrafficRecord.created_at.desc())
        .first()
    )
    if latest:
        ts = _record_ts(latest)
        age_min = round((now - ts).total_seconds() / 60, 1) if ts else None
        is_live = bool(ts and ts >= now - _LIVE_MAX_AGE)
        return {
            "area": canonical,
            "city": city,
            "congestion_level": latest.congestion_level or "unknown",
            "avg_speed_kmh": round(latest.average_speed, 1) if latest.average_speed else None,
            "vehicle_count": latest.vehicle_count,
            "updated_at": ts.astimezone(_IST).isoformat() if ts else None,
            "data_age_minutes": age_min,
            "data_source": "live" if is_live else "recent",
            "is_live": is_live,
        }

    meta = _area_meta(canonical)
    if meta:
        try:
            flow = await fetch_flow(meta["lat"], meta["lng"])
        except Exception as exc:
            logger.debug("On-demand flow failed for %s: %s", canonical, exc)
            flow = None
        if flow:
            cur = float(flow["currentSpeed"])
            free = float(flow["freeFlowSpeed"])
            return {
                "area": canonical,
                "city": city,
                "congestion_level": classify_congestion(cur, free),
                "avg_speed_kmh": round(cur, 1),
                "vehicle_count": estimate_vehicle_count(cur, free),
                "updated_at": now.astimezone(_IST).isoformat(),
                "data_age_minutes": 0,
                "data_source": flow.get("source", "live"),
                "is_live": True,
            }

    return {
        "area": canonical,
        "city": city,
        "congestion_level": "unknown",
        "avg_speed_kmh": None,
        "vehicle_count": None,
        "updated_at": None,
        "data_age_minutes": None,
        "data_source": "unavailable",
        "is_live": False,
    }


def _fetch_records(area: str, db: Session, days: int = 30) -> list:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    from sqlalchemy import or_
    return (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.location.ilike(f"%{area}%"),
            or_(
                TrafficRecord.timestamp >= since,
                TrafficRecord.created_at >= since,
            ),
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )


def _record_hour_ist(r) -> int:
    """Return the IST hour for a traffic record."""
    ts = _record_ts(r)
    if ts is None:
        return 0
    return ts.astimezone(_IST).hour


def _build_hourly_pattern(records: list) -> dict:
    """
    Returns {hour: {congestion, avg_speed_kmh, sample_size}} for hours 0-23.
    """
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() >= 5

    typed = [
        r for r in records
        if (_record_ts(r) or r.created_at) and ((_record_ts(r) or r.created_at).weekday() >= 5) == is_weekend
    ]
    pool = typed if len(typed) >= 20 else records

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
        ts = _record_ts(r) or r.created_at
        if ts is None:
            continue
        by_day[ts.weekday()].append(r)

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


def _apply_hour_prior(predicted: str, target_hour: int, sample_size: int) -> tuple[str, float]:
    """Bump sparse/uniform-low history toward rush-hour prior."""
    prior = _HOUR_PRIOR.get(target_hour % 24, "medium")
    if _CONGESTION_ORDER.get(prior, 0) <= _CONGESTION_ORDER.get(predicted, 0):
        return predicted, 1.0
    if sample_size < 8 or predicted == "low":
        return prior, 0.65
    return predicted, 1.0


def _predict_hour(hour_pattern: dict, target_hour: int, records: list) -> dict:
    p = hour_pattern.get(target_hour, {})
    if not p or p["congestion"] == "unknown" or p["sample_size"] < 3:
        if not records:
            pred, _ = _apply_hour_prior("medium", target_hour, 0)
            return {
                "predicted_congestion": pred,
                "confidence": 0.15,
                "avg_speed_kmh": _SPEED_FOR_CONGESTION.get(pred, 28.0),
            }
        counts = Counter(r.congestion_level for r in records)
        dominant = counts.most_common(1)[0][0]
        pred, conf_scale = _apply_hour_prior(dominant, target_hour, len(records))
        return {
            "predicted_congestion": pred,
            "confidence": round(0.3 * conf_scale, 2),
            "avg_speed_kmh": _SPEED_FOR_CONGESTION.get(pred, 28.0),
        }

    n = p["sample_size"]
    congestion, conf_scale = _apply_hour_prior(p["congestion"], target_hour, n)
    confidence = min(0.95, (0.5 + (n / 60) * 0.45) * conf_scale)
    return {
        "predicted_congestion": congestion,
        "confidence": round(confidence, 2),
        "avg_speed_kmh": (
            p["avg_speed_kmh"]
            if congestion == p["congestion"]
            else _SPEED_FOR_CONGESTION.get(congestion)
        ),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/predict", status_code=status.HTTP_200_OK)
async def predict_area_traffic(
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

    # ── Current (fresh snapshot — not the oldest "max" of 30 days) ────────────
    now = datetime.now(timezone.utc)
    since = now - _LIVE_MAX_AGE
    live = await _area_live_snapshot(canonical, city, db, since)

    if live["data_source"] != "unavailable":
        current = {
            "congestion_level": live["congestion_level"],
            "avg_speed_kmh": live["avg_speed_kmh"],
            "vehicle_count": live["vehicle_count"],
            "updated_at": live["updated_at"],
            "data_age_minutes": live.get("data_age_minutes"),
            "data_source": live["data_source"],
            "is_live": live.get("is_live", False),
        }
    elif records:
        latest = max(records, key=lambda r: _record_ts(r) or datetime.min.replace(tzinfo=timezone.utc))
        ts = _record_ts(latest)
        current = {
            "congestion_level": latest.congestion_level,
            "avg_speed_kmh": round(latest.average_speed, 1) if latest.average_speed else None,
            "vehicle_count": latest.vehicle_count,
            "updated_at": ts.astimezone(_IST).isoformat() if ts else None,
            "data_age_minutes": round((now - ts).total_seconds() / 60, 1) if ts else None,
            "data_source": "historical",
            "is_live": False,
        }
    else:
        current = {
            "congestion_level": "unknown",
            "avg_speed_kmh": None,
            "vehicle_count": None,
            "updated_at": None,
            "data_age_minutes": None,
            "data_source": "unavailable",
            "is_live": False,
        }

    # ── Patterns ─────────────────────────────────────────────────────────────
    hourly_pattern = _build_hourly_pattern(records)
    weekly_pattern = _build_weekly_pattern(records)

    # ── Forecast (IST clock hours) ───────────────────────────────────────────
    now_ist = now.astimezone(_IST)
    forecast = []
    for h_offset in range(1, hours_ahead + 1):
        target_dt = now_ist + timedelta(hours=h_offset)
        target_hour = target_dt.hour
        pred = _predict_hour(hourly_pattern, target_hour, records)
        forecast.append({
            "offset_hours":          h_offset,
            "time_label":            _hour_label(target_hour),
            "predicted_congestion":  pred["predicted_congestion"],
            "confidence":            pred["confidence"],
            "avg_speed_kmh":         pred["avg_speed_kmh"],
        })

    # ── Best / worst travel window ────────────────────────────────────────────
    def _sort_key(f):
        # Prefer true congestion difference; then speed; then rush-hour for "worst"
        rush = 1 if f["time_label"] and any(
            x in f["time_label"] for x in ("8:00 AM", "9:00 AM", "5:00 PM", "6:00 PM", "7:00 PM")
        ) else 0
        return (
            _CONGESTION_ORDER.get(f["predicted_congestion"], 1),
            -(f["avg_speed_kmh"] or 0),
            -rush,
        )

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
    elif curr_level == "unknown":
        rec = f"Limited live data for {canonical}. Check again after the next collection cycle."
    else:
        rec = f"Traffic is light in {canonical}. Good time to travel."

    # ── Summary stats — include medium peaks when high is missing ─────────────
    high_hours = sorted(h for h, p in hourly_pattern.items() if p["congestion"] == "high")
    if not high_hours:
        high_hours = sorted(
            h for h, p in hourly_pattern.items()
            if p["congestion"] == "medium" and p.get("sample_size", 0) > 0
        )

    def _detect_windows(hours: list[int]) -> list[tuple[int, int]]:
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
        # Soft peak from forecast rush hours
        rush_fc = [f for f in forecast if f["predicted_congestion"] in ("medium", "high")]
        peak_hours_label = (
            ", ".join(f["time_label"] for f in rush_fc[:3])
            if rush_fc else "No peak detected"
        )
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
async def compare_areas(
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
    since = now - _LIVE_MAX_AGE
    results = []

    for area_name in area_list:
        try:
            canonical, city = _resolve_area(area_name)
        except HTTPException:
            results.append({"area": area_name, "error": "Area not found"})
            continue

        snapshot = await _area_live_snapshot(canonical, city, db, since)
        if snapshot["data_source"] == "unavailable":
            # Last-resort: any historical reading for the area (not inventing congestion)
            hist = _fetch_records(canonical, db, days=7)
            if hist:
                latest = max(hist, key=lambda r: _record_ts(r) or datetime.min.replace(tzinfo=timezone.utc))
                ts = _record_ts(latest)
                results.append({
                    "area": canonical,
                    "city": city,
                    "congestion_level": latest.congestion_level,
                    "avg_speed_kmh": round(latest.average_speed, 1) if latest.average_speed else None,
                    "vehicle_count": latest.vehicle_count,
                    "updated_at": ts.astimezone(_IST).isoformat() if ts else None,
                    "data_age_minutes": round((now - ts).total_seconds() / 60, 1) if ts else None,
                    "data_source": "historical",
                    "is_live": False,
                })
            else:
                results.append({
                    "area": canonical,
                    "city": city,
                    "error": "No live or historical data available",
                    "data_source": "unavailable",
                })
            continue
        results.append(snapshot)

    known = [r for r in results if "error" not in r and r.get("congestion_level") not in ("unknown", None)]
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
async def search_areas(
    city: Optional[str] = Query(None, description="City name (e.g. Hyderabad)"),
    q:    Optional[str] = Query(None, description="Partial area name search"),
    db: Session = Depends(get_db),
) -> dict:
    """Search for areas/neighbourhoods with live traffic status, optionally filtered by city.

    Omit both `city` and `q` to list every registered area.
    """
    now = datetime.now(timezone.utc)
    since = now - _LIVE_MAX_AGE

    city_aliases = {
        "bengaluru": "bangalore",
        "bangalore": "bangalore",
        "delhi ncr": "delhi",
        "ncr": "delhi",
    }
    city_q = city.lower().strip() if city else None
    if city_q:
        city_q = city_aliases.get(city_q, city_q)

    matched = []
    for c, areas in CITY_AREAS.items():
        if city_q and city_q not in c.lower() and c.lower() not in city_q:
            continue
        for a in areas:
            if q and q.lower() not in a["name"].lower():
                continue
            matched.append((c, a))

    results = []
    for c, a in matched:
        snapshot = await _area_live_snapshot(a["name"], c, db, since)
        results.append({
            "area":             a["name"],
            "city":             c,
            "lat":              a["lat"],
            "lng":              a["lng"],
            "congestion_level": snapshot["congestion_level"],
            "avg_speed_kmh":    snapshot["avg_speed_kmh"],
            "vehicle_count":    snapshot["vehicle_count"],
            "updated_at":       snapshot["updated_at"],
            "data_source":      snapshot["data_source"],
            "is_live":          snapshot.get("is_live", False),
        })

    return {
        "total": len(results),
        "areas": results,
    }


@router.get("/cities", status_code=status.HTTP_200_OK)
async def list_cities(db: Session = Depends(get_db)) -> dict:
    """List all supported cities with area counts and live traffic summary."""
    from sqlalchemy import or_

    now   = datetime.now(timezone.utc)
    since = now - _LIVE_MAX_AGE
    cities = []

    for c, areas in CITY_AREAS.items():
        area_names = [a["name"] for a in areas]

        records = (
            db.query(TrafficRecord)
            .filter(
                or_(*[TrafficRecord.location.ilike(f"%{n}%") for n in area_names]),
                or_(
                    TrafficRecord.timestamp >= since,
                    TrafficRecord.created_at >= since,
                ),
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
            first = areas[0]
            snapshot = await _area_live_snapshot(first["name"], c, db, since)
            if snapshot["data_source"] == "unavailable":
                dominant = "unknown"
                avg_speed = None
                health = None
                data_source = "unavailable"
            else:
                dominant = snapshot["congestion_level"]
                avg_speed = snapshot["avg_speed_kmh"]
                health = {"low": 85.0, "medium": 60.0, "high": 30.0}.get(dominant, 60.0)
                data_source = snapshot["data_source"]

        cities.append({
            "city":               c,
            "area_count":         len(areas),
            "areas":              area_names,
            "dominant_congestion": dominant,
            "avg_speed_kmh":      avg_speed,
            "health_score":       health,
            "has_data":           health is not None,
            "data_source":        data_source,
        })

    # None health_score sorts last; higher score first among known cities
    cities.sort(key=lambda x: (x["health_score"] is None, -(x["health_score"] or 0)))
    return {
        "total_cities": len(cities),
        "generated_at": now.astimezone(_IST).isoformat(),
        "cities": cities,
    }
