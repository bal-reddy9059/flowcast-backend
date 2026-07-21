"""Commute planner endpoints — rush hour forecast and best departure time."""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import TrafficRecord, Incident
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.city_aliases import location_filter
from app.services.eta_service import calculate_eta_for_location, get_speed_for_congestion
from app.services.prediction_service import predict_traffic_congestion

router = APIRouter(prefix="/commute", tags=["Commute Planner"])
logger = logging.getLogger(__name__)

_CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}
_CONGESTION_TO_UI_SCORE = {"low": 18, "medium": 52, "high": 82}


def _clock_label(dt: datetime) -> str:
    """Hour-aligned label like 13:00 (not wall-clock minutes)."""
    return dt.replace(minute=0, second=0, microsecond=0).strftime("%H:%M")


def _ampm_label(dt: datetime) -> str:
    return dt.replace(minute=0, second=0, microsecond=0).strftime("%I:%M %p").lstrip("0")


def _build_hourly_forecast(location: str, hours: int, db: Session, now: datetime) -> list[dict]:
    forecast = []
    for h in range(hours):
        slot = (now + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
        target_hour = slot.hour
        result = predict_traffic_congestion(location, target_hour, db)
        level = result["predicted_congestion"]
        conf = float(result["confidence_score"] or 0)
        base = _CONGESTION_TO_UI_SCORE.get(level, 40)
        # Slight confidence-aware spread so flat "all low" charts aren't identical bars
        ui_score = int(round(base + (conf - 0.5) * 8))
        forecast.append({
            "hour_offset": h,
            "hour_of_day": target_hour,
            "time_label": _clock_label(slot),
            "predicted_congestion": level,
            "confidence_score": conf,
            "sample_size": result.get("sample_size", 0),
            "ui_score": max(5, min(95, ui_score)),
        })
    return forecast


def _pick_peak(forecast: list[dict]) -> dict:
    """Worst congestion; break ties with rush-hour preference then sample size."""
    max_lvl = max(_CONGESTION_SCORE.get(f["predicted_congestion"], 1) for f in forecast)
    candidates = [f for f in forecast if _CONGESTION_SCORE.get(f["predicted_congestion"], 1) == max_lvl]

    def _rank(f: dict) -> tuple:
        # Prefer evening rush (17-20), then morning (8-10), then larger samples
        hod = f["hour_of_day"]
        rush = 2 if 17 <= hod <= 20 else (1 if 8 <= hod <= 10 else 0)
        return (rush, f.get("sample_size", 0), f.get("confidence_score", 0))

    return max(candidates, key=_rank)


def _pick_best(forecast: list[dict]) -> dict:
    """Best (lowest) congestion; break ties with confidence then soonest."""
    min_lvl = min(_CONGESTION_SCORE.get(f["predicted_congestion"], 1) for f in forecast)
    candidates = [f for f in forecast if _CONGESTION_SCORE.get(f["predicted_congestion"], 1) == min_lvl]
    return min(
        candidates,
        key=lambda f: (-f.get("confidence_score", 0), f["hour_offset"], -f.get("sample_size", 0)),
    )


def _fetch_recent_traffic(location: str, db: Session, hours: int = 6):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    records = (
        db.query(TrafficRecord)
        .filter(
            location_filter(TrafficRecord.location, location),
            TrafficRecord.timestamp >= since,
        )
        .all()
    )
    if records:
        return records
    return (
        db.query(TrafficRecord)
        .filter(
            location_filter(TrafficRecord.location, location),
            TrafficRecord.created_at >= since,
        )
        .all()
    )


def _count_recent_incidents(location: str, db: Session, hours: int = 6) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return (
        db.query(Incident)
        .filter(
            location_filter(Incident.location, location),
            Incident.is_active.is_(True),
            Incident.reported_at >= since,
        )
        .count()
    )


def _score_from_records(records: list, active_incidents: int) -> tuple[float, dict]:
    counts = Counter(r.congestion_level for r in records if r.congestion_level)
    total = max(len(records), 1)
    high_pct = counts.get("high", 0) / total * 100
    medium_pct = counts.get("medium", 0) / total * 100
    low_pct = counts.get("low", 0) / total * 100
    score = round(max(0.0, min(100.0, 100 - high_pct * 0.6 - medium_pct * 0.2 - active_incidents * 5)), 1)
    speeds = [r.average_speed for r in records if r.average_speed]
    avg_speed = sum(speeds) / len(speeds) if speeds else None
    return score, {
        "high_pct": round(high_pct, 1),
        "medium_pct": round(medium_pct, 1),
        "low_pct": round(low_pct, 1),
        "avg_speed_kmh": round(avg_speed, 1) if avg_speed else None,
        "total_records": len(records),
    }


def _grade_verdict(score: float, *, no_data: bool = False) -> tuple[str, str, str]:
    if no_data:
        return "C", "No recent data", "gray"
    if score >= 80:
        return "A", "Great time to commute", "green"
    if score >= 65:
        return "B", "Good — minor delays likely", "light-green"
    if score >= 50:
        return "C", "Moderate traffic — plan extra time", "yellow"
    if score >= 35:
        return "D", "Heavy traffic — delays expected", "orange"
    return "F", "Avoid if possible — severe congestion", "red"


@router.get("/forecast", status_code=status.HTTP_200_OK)
def get_rush_hour_forecast(
    location: str = Query(..., min_length=2, description="City or area name (e.g. Hyderabad)"),
    db: Session = Depends(get_db),
) -> dict:
    """24-hour congestion forecast for a location based on historical patterns."""
    now = datetime.now(timezone.utc)
    forecast = _build_hourly_forecast(location, 24, db, now)
    peak = _pick_peak(forecast)
    best_next_8h = _pick_best(forecast[:8])

    hourly = [{"hour": f["time_label"], "score": f["ui_score"]} for f in forecast]
    # Strip internal ui_score from detailed forecast
    hourly_forecast = [{k: v for k, v in f.items() if k != "ui_score"} for f in forecast]

    logger.info(
        "24h forecast for %s peak=%s@%s best=%s@%s",
        location,
        peak["predicted_congestion"], peak["time_label"],
        best_next_8h["predicted_congestion"], best_next_8h["time_label"],
    )
    return {
        "location": location,
        "forecast_generated_at": now.isoformat(),
        "peak_congestion_hour": {k: v for k, v in peak.items() if k != "ui_score"},
        "best_departure_next_8h": {k: v for k, v in best_next_8h.items() if k != "ui_score"},
        "hourly_forecast": hourly_forecast,
        "hourly": hourly,
    }


@router.get("/best-time", status_code=status.HTTP_200_OK)
def get_best_departure_time(
    location: str = Query(..., min_length=2, description="Origin/destination location"),
    distance_km: float = Query(..., gt=0, le=500, description="Distance to travel in km"),
    mode: str = Query("driving", description="Travel mode: driving / walking / transit"),
    window_hours: int = Query(6, ge=2, le=12, description="Hours ahead to evaluate"),
    db: Session = Depends(get_db),
) -> dict:
    """Recommend the top 3 best departure windows in the next N hours."""
    if mode not in {"driving", "walking", "transit"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be driving, walking, or transit",
        )

    now = datetime.now(timezone.utc)
    slots = []
    for h in range(window_hours):
        dep_time = (now + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
        pred = predict_traffic_congestion(location, dep_time.hour, db)
        pred_level = pred["predicted_congestion"]
        # ETA from congestion-class speed (not a flat live baseline × factor)
        speed = get_speed_for_congestion(pred_level, mode)
        eta_mins = round((distance_km / max(speed, 1.0)) * 60, 1)
        eta_buffer = round(eta_mins * 1.1, 1)

        slots.append({
            "departure_offset_hours": h,
            "departure_time": dep_time.strftime("%H:%M"),
            "predicted_congestion": pred_level,
            "confidence_score": pred["confidence_score"],
            "estimated_eta_minutes": eta_mins,
            "estimated_eta_with_buffer_minutes": eta_buffer,
        })

    top_3 = sorted(
        slots,
        key=lambda s: (
            s["estimated_eta_minutes"],
            -s["confidence_score"],
            s["departure_offset_hours"],
        ),
    )[:3]

    logger.info(
        "Best departure for %s %.1fkm %s: +%sh (%.1f min ETA)",
        location, distance_km, mode,
        top_3[0]["departure_offset_hours"], top_3[0]["estimated_eta_minutes"],
    )
    return {
        "location": location,
        "distance_km": distance_km,
        "mode": mode,
        "window_hours": window_hours,
        "top_3_recommended_departures": top_3,
        "all_slots": slots,
        "calculated_at": now.isoformat(),
    }


@router.get("/commute-score", status_code=status.HTTP_200_OK)
def get_commute_score(
    location: str = Query(..., min_length=2, description="City or area name"),
    db: Session = Depends(get_db),
) -> dict:
    """Personal commute friendliness score for a location (0–100)."""
    records = _fetch_recent_traffic(location, db, hours=6)
    active_incidents = _count_recent_incidents(location, db, hours=6)

    if not records:
        return {
            "location": location,
            "score": None,
            "grade": None,
            "verdict": "No recent data",
            "color": "gray",
            "active_incidents": active_incidents,
            "has_data": False,
            "message": "Insufficient traffic data — try a neighbourhood name or check back after collection",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }

    score, breakdown = _score_from_records(records, active_incidents)
    grade, verdict, color = _grade_verdict(score)

    logger.info("Commute score for %s: %.1f (%s)", location, score, grade)
    return {
        "location": location,
        "score": score,
        "grade": grade,
        "verdict": verdict,
        "color": color,
        "avg_speed_kmh": breakdown["avg_speed_kmh"],
        "active_incidents": active_incidents,
        "has_data": True,
        "congestion_breakdown": {
            "high_pct": breakdown["high_pct"],
            "medium_pct": breakdown["medium_pct"],
            "low_pct": breakdown["low_pct"],
        },
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/score", status_code=status.HTTP_200_OK)
def get_commute_score_alias(
    location: str = Query(..., min_length=2, description="City or area name"),
    db: Session = Depends(get_db),
) -> dict:
    """Commute score + best/worst windows for the frontend dashboard."""
    now = datetime.now(timezone.utc)
    records = _fetch_recent_traffic(location, db, hours=6)
    active_incidents = _count_recent_incidents(location, db, hours=6)

    if not records:
        return {
            "location": location,
            "score": None,
            "grade": None,
            "verdict": "No recent data",
            "best_window": None,
            "worst_window": None,
            "avg_commute_minutes": None,
            "active_incidents": active_incidents,
            "has_data": False,
            "message": "Insufficient traffic data — score unavailable",
            "evaluated_at": now.isoformat(),
        }

    score, _ = _score_from_records(records, active_incidents)
    grade, verdict, _ = _grade_verdict(score)

    forecast = _build_hourly_forecast(location, 24, db, now)
    # 2-hour windows ranked by congestion sum, then confidence
    def _window_key(i: int, prefer_worst: bool) -> tuple:
        a, b = forecast[i], forecast[i + 1]
        lvl = _CONGESTION_SCORE[a["predicted_congestion"]] + _CONGESTION_SCORE[b["predicted_congestion"]]
        conf = a["confidence_score"] + b["confidence_score"]
        if prefer_worst:
            return (lvl, conf)
        return (lvl, -conf, a["hour_offset"])

    best_i = min(range(22), key=lambda i: _window_key(i, prefer_worst=False))
    worst_i = max(range(22), key=lambda i: _window_key(i, prefer_worst=True))

    # If every window ties, force distinct rush vs off-peak labels
    if best_i == worst_i or (
        forecast[best_i]["predicted_congestion"] == forecast[worst_i]["predicted_congestion"]
        and all(f["predicted_congestion"] == forecast[0]["predicted_congestion"] for f in forecast)
    ):
        # Prefer late morning / early afternoon as "best", evening rush as "worst"
        best_i = next((i for i, f in enumerate(forecast[:22]) if f["hour_of_day"] in (11, 12, 13, 14)), 2)
        worst_i = next((i for i, f in enumerate(forecast[:22]) if f["hour_of_day"] in (18, 19, 17)), min(6, 21))

    def _window_label(i: int) -> str:
        start = now + timedelta(hours=i)
        end = now + timedelta(hours=i + 2)
        return f"{_ampm_label(start)} – {_ampm_label(end)}"

    try:
        eta = calculate_eta_for_location(location, 10.0, "driving", db)
        avg_minutes = round(eta.eta_minutes)
    except Exception:
        avg_minutes = 24

    logger.info("Commute score alias for %s: %.1f (%s)", location, score, grade)
    return {
        "location": location,
        "score": score,
        "grade": grade,
        "verdict": verdict.replace(" — plan extra time", "") if verdict.startswith("Moderate") else verdict,
        "best_window": _window_label(best_i),
        "worst_window": _window_label(worst_i),
        "avg_commute_minutes": avg_minutes,
        "active_incidents": active_incidents,
        "has_data": True,
        "evaluated_at": now.isoformat(),
    }


@router.get("/stress-score", status_code=status.HTTP_200_OK)
def get_stress_score(
    location: str = Query(..., min_length=2, description="Location / route area (e.g. 'Silk Board, Bangalore')"),
    distance_km: float = Query(10.0, gt=0, le=500, description="Trip distance in km — defaults to 10 km if not provided"),
    mode: str = Query("driving", description="driving / walking / transit"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Commute stress score (0 = calm, 100 = intense) for current road conditions."""
    from app.services.stress_scorer import calculate_stress_score

    if mode not in {"driving", "walking", "transit"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode must be driving, walking, or transit")

    user_id = str(current_user.id) if current_user else None
    return calculate_stress_score(location, distance_km, mode, db, user_id=user_id)


@router.get("/should-i-leave", status_code=status.HTTP_200_OK)
def should_i_leave(
    origin: str = Query(..., min_length=2, description="Origin location name"),
    destination: str = Query(..., min_length=2, description="Destination location name"),
    distance_km: float = Query(..., gt=0, le=500, description="Trip distance in km"),
    mode: str = Query("driving", description="Travel mode: driving / walking / transit"),
    target_arrival: Optional[str] = Query(None, description="Optional ISO-8601 target arrival time"),
    db: Session = Depends(get_db),
) -> dict:
    """Smart departure advisor — answers 'Should I leave NOW or wait?'"""
    if mode not in {"driving", "walking", "transit"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be driving, walking, or transit",
        )

    now = datetime.now(timezone.utc)
    is_intercity = distance_km > 100

    target_dt = None
    if target_arrival:
        try:
            target_dt = datetime.fromisoformat(target_arrival)
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_arrival must be a valid ISO-8601 datetime string",
            )

    # Prefer origin traffic; fall back to destination for corridor feel
    current_eta = calculate_eta_for_location(origin, distance_km, mode, db)
    current_eta_min = current_eta.eta_minutes
    current_congestion = current_eta.congestion_level

    if target_dt is not None:
        must_leave_by = target_dt - timedelta(minutes=current_eta_min)
        if now >= must_leave_by:
            return {
                "origin": origin,
                "destination": destination,
                "distance_km": distance_km,
                "mode": mode,
                "advice": "already_late",
                "current_eta_minutes": round(current_eta_min, 1),
                "optimal_eta_minutes": round(current_eta_min, 1),
                "optimal_departure_in_minutes": 0,
                "savings_minutes": 0.0,
                "reason": "You need to leave immediately to reach your destination on time.",
                "is_intercity": is_intercity,
                "congestion_forecast": [],
                "calculated_at": now.isoformat(),
            }

    forecast = []
    for h in range(6):
        slot = (now + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
        pred_o = predict_traffic_congestion(origin, slot.hour, db)
        pred_d = predict_traffic_congestion(destination, slot.hour, db)
        # Worse of origin/destination corridor
        o_lvl = pred_o["predicted_congestion"]
        d_lvl = pred_d["predicted_congestion"]
        level = o_lvl if _CONGESTION_SCORE[o_lvl] >= _CONGESTION_SCORE[d_lvl] else d_lvl
        conf = round((float(pred_o["confidence_score"]) + float(pred_d["confidence_score"])) / 2, 2)
        forecast.append({
            "hour_offset": h,
            "hour_label": _clock_label(slot),
            "predicted_congestion": level,
            "confidence_score": conf,
            "sample_size": (pred_o.get("sample_size", 0) or 0) + (pred_d.get("sample_size", 0) or 0),
        })

    low_confidence = all(f["confidence_score"] < 0.25 for f in forecast)

    best_offset = 0
    best_score = _CONGESTION_SCORE.get(current_congestion, 1)
    for entry in forecast[:4]:
        score = _CONGESTION_SCORE.get(entry["predicted_congestion"], 1)
        if score < best_score or (
            score == best_score and entry["confidence_score"] > forecast[best_offset]["confidence_score"]
            and entry["hour_offset"] != best_offset
        ):
            if score < best_score:
                best_score = score
                best_offset = entry["hour_offset"]

    best_congestion = forecast[best_offset]["predicted_congestion"]
    optimal_speed = get_speed_for_congestion(best_congestion, mode)
    optimal_eta_min = (distance_km / max(optimal_speed, 1)) * 60
    # Compare apples-to-apples using congestion-class speeds
    current_class_eta = (distance_km / max(get_speed_for_congestion(current_congestion, mode), 1)) * 60
    savings = max(0.0, current_class_eta - optimal_eta_min)

    if is_intercity:
        advice = "leave_now"
        reason = (
            f"This is a long-distance trip (~{int(distance_km)} km, ~{round(current_eta_min / 60, 1)} h). "
            "Local rush-hour waiting rarely helps — leave when ready and plan fuel/rest stops."
        )
        best_offset = 0
        savings = 0.0
        optimal_eta_min = current_eta_min
    elif low_confidence:
        advice = "leave_now"
        reason = (
            "Limited forecast confidence for this corridor — conditions are unlikely to change "
            "enough to justify waiting. Leave when ready."
        )
        best_offset = 0
        savings = 0.0
        optimal_eta_min = current_eta_min
    elif current_congestion == "low" or best_offset == 0 or savings < 3:
        advice = "leave_now"
        reason = (
            "Traffic is light right now — ideal time to head out."
            if current_congestion == "low"
            else "No meaningful improvement expected in the next few hours — leave now."
        )
        best_offset = 0
        savings = 0.0
        optimal_eta_min = current_eta_min
    else:
        wait_minutes = best_offset * 60
        advice = f"wait_{wait_minutes}_minutes"
        reason = (
            f"Traffic predicted to ease in ~{wait_minutes} min. "
            f"Waiting could save approximately {round(savings, 0):.0f} minutes on your journey."
        )

    logger.info(
        "should-i-leave: %s->%s %.1fkm %s -> advice=%s savings=%.1f min",
        origin, destination, distance_km, mode, advice, savings,
    )
    return {
        "origin": origin,
        "destination": destination,
        "distance_km": distance_km,
        "mode": mode,
        "advice": advice,
        "current_eta_minutes": round(current_eta_min, 1),
        "optimal_eta_minutes": round(optimal_eta_min, 1),
        "optimal_departure_in_minutes": best_offset * 60,
        "savings_minutes": round(savings, 1),
        "reason": reason,
        "is_intercity": is_intercity,
        "congestion_forecast": forecast,
        "calculated_at": now.isoformat(),
    }
