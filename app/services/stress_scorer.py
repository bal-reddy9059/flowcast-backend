"""Commute stress scorer — measures how stressful a commute is right now (0–100)."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_LABELS = [
    (0,  25,  "Calm",      "green",  "Great conditions — smooth commute expected."),
    (25, 50,  "Moderate",  "yellow", "Some friction ahead. Minor delays likely."),
    (50, 75,  "Stressful", "orange", "Heavy traffic. Build in extra time and stay patient."),
    (75, 100, "Intense",   "red",    "Severe conditions. Consider delaying or taking an alternate route."),
]


def _label_for(score: int) -> tuple:
    for lo, hi, label, color, verdict in _LABELS:
        if lo <= score < hi or (hi == 100 and score >= lo):
            return (lo, hi, label, color, verdict)
    return _LABELS[1]


def _fetch_records(location: str, db, since):
    """Fetch likely location matches in one bounded database query."""
    from app.models.predictor import TrafficRecord
    from app.services.city_aliases import location_filter
    from sqlalchemy import or_

    now = datetime.now(timezone.utc)

    def _with_age(records, matched):
        if not records:
            return records, matched, None
        latest = max((r.timestamp or r.created_at for r in records if (r.timestamp or r.created_at)), default=None)
        if latest is None:
            age = None
        else:
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            age = max(0, round((now - latest).total_seconds() / 3600, 1))
        return records, matched, age

    candidates = [location]
    try:
        from app.routes.route import _geocode
        geo = _geocode(location)
        if geo:
            candidates.insert(0, geo["name"])
    except Exception:
        pass

    candidates.extend(p.strip() for p in location.split(",") if len(p.strip()) > 2)
    candidates.extend(
        word for word in location.replace(",", " ").split() if len(word) > 3
    )
    candidates = list(dict.fromkeys(candidate for candidate in candidates if candidate))
    conditions = [location_filter(TrafficRecord.location, candidate) for candidate in candidates]
    since_24h = now - timedelta(hours=24)
    records = (
        db.query(TrafficRecord)
        .filter(or_(*conditions), TrafficRecord.timestamp >= since_24h)
        .order_by(TrafficRecord.timestamp.desc())
        .limit(100)
        .all()
    )
    if not records:
        return [], location, None

    recent = []
    for record in records:
        observed_at = record.timestamp or record.created_at
        if observed_at and observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if observed_at and observed_at >= since:
            recent.append(record)
    selected = (recent or records)[:20]
    matched = selected[0].location if selected else location
    return _with_age(selected, matched)


def _fetch_incidents(location: str, db) -> int:
    """Count recent active incidents near the location."""
    from app.models.predictor import Incident
    from app.services.city_aliases import location_filter
    from sqlalchemy import or_

    since = datetime.now(timezone.utc) - timedelta(hours=6)
    parts = [location] + [p.strip() for p in location.split(",") if len(p.strip()) > 2]
    conditions = [location_filter(Incident.location, part) for part in dict.fromkeys(parts)]
    return (
        db.query(Incident)
        .filter(
            or_(*conditions),
            Incident.is_active.is_(True),
            Incident.reported_at >= since,
        )
        .count()
    )


def calculate_stress_score(
    location: str,
    distance_km: float,
    mode: str,
    db: Session,
    user_id: str = None,
) -> dict:
    """Calculate a commute stress score (0 = calm, 100 = intense) for a route right now."""
    from collections import Counter
    from app.services.eta_service import get_speed_for_congestion

    now = datetime.now(timezone.utc)
    since_1h = now - timedelta(hours=1)

    records, matched_location, data_age_hours = _fetch_records(location, db, since_1h)
    active_incidents = _fetch_incidents(location, db)

    if not records:
        return _fallback_score(location, active_incidents, now)

    congestion_levels = [r.congestion_level for r in records if r.congestion_level in ("low", "medium", "high")]
    speeds = [r.average_speed for r in records if r.average_speed]

    # Majority congestion (not just the newest row)
    if congestion_levels:
        current_congestion = Counter(congestion_levels).most_common(1)[0][0]
    else:
        current_congestion = "medium"

    avg_speed = sum(speeds) / len(speeds) if speeds else get_speed_for_congestion(current_congestion, mode)
    free_flow_speed = get_speed_for_congestion("low", mode)
    expected_eta = (distance_km / max(free_flow_speed, 1)) * 60
    current_eta = (distance_km / max(avg_speed, 1)) * 60
    pct_over = max(0, (current_eta - expected_eta) / max(expected_eta, 1) * 100)

    # Cap duration stress when stored congestion is already low (speed estimate noise)
    duration_pts = min(40, pct_over * 0.7)
    if current_congestion == "low":
        duration_pts = min(duration_pts, 18)
    elif current_congestion == "medium":
        duration_pts = min(duration_pts, 28)

    incident_pts = min(25, active_incidents * 9)

    variability_pts = 0.0
    speed_var_label = "low"
    if len(speeds) > 2:
        mean = sum(speeds) / len(speeds)
        std = (sum((s - mean) ** 2 for s in speeds) / len(speeds)) ** 0.5
        if std > 15:
            variability_pts, speed_var_label = 20.0, "high"
        elif std > 8:
            variability_pts, speed_var_label = 12.0, "medium"
        elif std > 4:
            variability_pts, speed_var_label = 6.0, "low"

    high_count = congestion_levels.count("high")
    congestion_pts = min(15, (high_count / max(len(congestion_levels), 1)) * 15)

    score = min(100, round(duration_pts + incident_pts + variability_pts + congestion_pts))
    label_entry = _label_for(score)

    personal_comparison = None
    if user_id:
        from app.models.trip import TripHistory
        from sqlalchemy import func
        avg_past = (
            db.query(func.avg(TripHistory.predicted_eta_minutes))
            .filter(
                TripHistory.user_id == user_id,
                TripHistory.origin_name.ilike(f"%{matched_location}%"),
                TripHistory.created_at >= now - timedelta(days=30),
            )
            .scalar()
        )
        if avg_past is not None:
            diff = round(current_eta - float(avg_past), 1)
            if diff > 2:
                personal_comparison = f"{abs(diff):.0f} min longer than your usual commute here"
            elif diff < -2:
                personal_comparison = f"{abs(diff):.0f} min faster than your usual commute here"
            else:
                personal_comparison = "About the same as your usual commute here"

    tips = {
        "Calm":      "Perfect time to travel — enjoy the open road.",
        "Moderate":  "Leave a 10-min buffer and you'll be fine.",
        "Stressful": "Check the departure coach for a better window, or consider an alternate route.",
        "Intense":   "Strongly consider waiting 45–60 min or working from home if possible.",
    }

    logger.info(
        "Stress score for '%s' (matched '%s', %d records, age=%s): %d (%s)",
        location, matched_location, len(records), data_age_hours, score, label_entry[2],
    )
    return {
        "location": location,
        "matched_location": matched_location,
        "stress_score": score,
        "label": label_entry[2],
        "color": label_entry[3],
        "verdict": label_entry[4],
        "breakdown": {
            "duration_vs_freeflow_pct": round(pct_over, 1),
            "active_incidents": active_incidents,
            "speed_variability": speed_var_label,
            "congestion_level": current_congestion,
            "avg_speed_kmh": round(avg_speed, 1) if speeds else None,
            "records_used": len(records),
            "data_age_hours": data_age_hours,
        },
        "personal_comparison": personal_comparison,
        "tip": tips.get(label_entry[2], ""),
        "active_incidents": active_incidents,
        "evaluated_at": now.isoformat(),
    }


def _fallback_score(location: str, active_incidents: int, now: datetime) -> dict:
    """Return an estimated score when absolutely no DB data is found."""
    hour = now.hour
    is_peak = (7 <= hour <= 10) or (17 <= hour <= 21)
    is_weekend = now.weekday() >= 5

    if is_weekend:
        score, congestion = 25, "low"
    elif is_peak:
        score, congestion = 62, "high"
    else:
        score, congestion = 38, "medium"

    score = min(100, score + active_incidents * 8)
    label_entry = _label_for(score)

    source = "peak-hour estimate" if is_peak else "off-peak estimate"
    verdict = f"{label_entry[4]} (based on {source} — no live data found for this location)"

    tips = {
        "Calm":      "Conditions look manageable — good time to travel.",
        "Moderate":  "Moderate stress expected based on time of day.",
        "Stressful": "Peak hours are typically heavy here — allow extra time.",
        "Intense":   "Peak-hour congestion expected — consider delaying if possible.",
    }

    logger.info("Stress score fallback for '%s': %d (%s, peak=%s)", location, score, label_entry[2], is_peak)
    return {
        "location": location,
        "matched_location": None,
        "stress_score": score,
        "label": label_entry[2],
        "color": label_entry[3],
        "verdict": verdict,
        "breakdown": {
            "duration_vs_freeflow_pct": None,
            "active_incidents": active_incidents,
            "speed_variability": "unknown",
            "congestion_level": congestion,
            "avg_speed_kmh": None,
            "records_used": 0,
            "data_age_hours": None,
        },
        "personal_comparison": None,
        "tip": tips.get(label_entry[2], ""),
        "active_incidents": active_incidents,
        "evaluated_at": now.isoformat(),
    }
