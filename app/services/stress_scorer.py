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
    """Try progressively wider location matches until we find records."""
    from app.models.predictor import TrafficRecord
    from app.services.city_aliases import location_filter

    # 1. Exact / alias match on full string
    records = (
        db.query(TrafficRecord)
        .filter(location_filter(TrafficRecord.location, location), TrafficRecord.created_at >= since)
        .order_by(TrafficRecord.created_at.desc())
        .limit(20)
        .all()
    )
    if records:
        return records, location

    # 2. Try each comma-separated part individually (e.g. "Silk Board, Bangalore" → "Silk Board")
    parts = [p.strip() for p in location.split(",") if len(p.strip()) > 2]
    for part in parts:
        records = (
            db.query(TrafficRecord)
            .filter(location_filter(TrafficRecord.location, part), TrafficRecord.created_at >= since)
            .order_by(TrafficRecord.created_at.desc())
            .limit(20)
            .all()
        )
        if records:
            return records, part

    # 3. Widen time window to 24 h and retry with each part
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    for part in [location] + parts:
        records = (
            db.query(TrafficRecord)
            .filter(location_filter(TrafficRecord.location, part), TrafficRecord.created_at >= since_24h)
            .order_by(TrafficRecord.created_at.desc())
            .limit(20)
            .all()
        )
        if records:
            return records, part

    # 4. Raw ILIKE on any word in the location string
    words = [w for w in location.replace(",", " ").split() if len(w) > 3]
    for word in words:
        records = (
            db.query(TrafficRecord)
            .filter(TrafficRecord.location.ilike(f"%{word}%"), TrafficRecord.created_at >= since_24h)
            .order_by(TrafficRecord.created_at.desc())
            .limit(20)
            .all()
        )
        if records:
            return records, word

    return [], location


def _fetch_incidents(location: str, db) -> int:
    """Count active incidents, trying same fallback strategy."""
    from app.models.predictor import Incident
    from app.services.city_aliases import location_filter

    parts = [location] + [p.strip() for p in location.split(",") if len(p.strip()) > 2]
    for part in parts:
        count = (
            db.query(Incident)
            .filter(location_filter(Incident.location, part), Incident.is_active.is_(True))
            .count()
        )
        if count:
            return count
    return 0


def calculate_stress_score(
    location: str,
    distance_km: float,
    mode: str,
    db: Session,
    user_id: str = None,
) -> dict:
    """Calculate a commute stress score (0 = calm, 100 = intense) for a route right now."""
    from app.services.eta_service import get_speed_for_congestion

    now = datetime.now(timezone.utc)
    since_1h = now - timedelta(hours=1)

    records, matched_location = _fetch_records(location, db, since_1h)
    active_incidents = _fetch_incidents(location, db)
    data_age_hours = 1 if matched_location == location else 24

    if not records:
        # Absolute fallback — infer from city-level if possible
        return _fallback_score(location, active_incidents, now)

    congestion_levels = [r.congestion_level for r in records if r.congestion_level]
    speeds = [r.average_speed for r in records if r.average_speed]

    # ── Duration component (0–40 pts) ─────────────────────────────────────────
    current_congestion = congestion_levels[0] if congestion_levels else "medium"
    current_speed = speeds[0] if speeds else get_speed_for_congestion("medium", mode)
    free_flow_speed = get_speed_for_congestion("low", mode)
    expected_eta = (distance_km / max(free_flow_speed, 1)) * 60
    current_eta  = (distance_km / max(current_speed, 1)) * 60
    pct_over = max(0, (current_eta - expected_eta) / max(expected_eta, 1) * 100)
    duration_pts = min(40, pct_over * 0.7)

    # ── Incident component (0–25 pts) ─────────────────────────────────────────
    incident_pts = min(25, active_incidents * 9)

    # ── Speed variability (0–20 pts) ──────────────────────────────────────────
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

    # ── Congestion component (0–15 pts) ───────────────────────────────────────
    high_count = congestion_levels.count("high")
    congestion_pts = min(15, (high_count / max(len(congestion_levels), 1)) * 15)

    score = min(100, round(duration_pts + incident_pts + variability_pts + congestion_pts))
    label_entry = _label_for(score)

    # ── Personal comparison ────────────────────────────────────────────────────
    personal_comparison = None
    if user_id:
        from app.models.trip import TripHistory
        past = (
            db.query(TripHistory)
            .filter(
                TripHistory.user_id == user_id,
                TripHistory.origin_name.ilike(f"%{matched_location}%"),
                TripHistory.created_at >= now - timedelta(days=30),
            )
            .all()
        )
        if past:
            past_etas = [t.predicted_eta_minutes for t in past if t.predicted_eta_minutes]
            if past_etas:
                avg_past = sum(past_etas) / len(past_etas)
                diff = round(current_eta - avg_past, 1)
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
        "Stress score for '%s' (matched '%s', %d records, age=%dh): %d (%s)",
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
            "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else None,
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
