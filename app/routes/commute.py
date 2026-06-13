"""Commute planner endpoints — rush hour forecast and best departure time."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.eta_service import calculate_eta_for_location, get_speed_for_congestion
from app.services.prediction_service import predict_traffic_congestion

router = APIRouter(prefix="/commute", tags=["Commute Planner"])
logger = logging.getLogger(__name__)

_CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}
_SPEED_FACTOR = {"low": 1.2, "medium": 1.0, "high": 0.65}


@router.get("/forecast", status_code=status.HTTP_200_OK)
def get_rush_hour_forecast(
    location: str = Query(..., min_length=2, description="Hyderabad location name"),
    db: Session = Depends(get_db),
) -> dict:
    """24-hour congestion forecast for a location based on 30 days of historical data.

    Returns predicted congestion for each hour of the day starting from now,
    plus the worst peak hour and the best window in the next 8 hours.
    """
    now = datetime.now(timezone.utc)
    forecast = []

    for h in range(24):
        target_hour = (now.hour + h) % 24
        result = predict_traffic_congestion(location, target_hour, db)
        forecast.append({
            "hour_offset": h,
            "hour_of_day": target_hour,
            "time_label": (now + timedelta(hours=h)).strftime("%H:%M"),
            "predicted_congestion": result["predicted_congestion"],
            "confidence_score": result["confidence_score"],
            "sample_size": result.get("sample_size", 0),
        })

    peak = max(forecast, key=lambda x: _CONGESTION_SCORE.get(x["predicted_congestion"], 1))
    best_next_8h = min(
        forecast[:8],
        key=lambda x: _CONGESTION_SCORE.get(x["predicted_congestion"], 1),
    )

    logger.info("24h forecast generated for %s", location)
    return {
        "location": location,
        "forecast_generated_at": now.isoformat(),
        "peak_congestion_hour": peak,
        "best_departure_next_8h": best_next_8h,
        "hourly_forecast": forecast,
    }


@router.get("/best-time", status_code=status.HTTP_200_OK)
def get_best_departure_time(
    location: str = Query(..., min_length=2, description="Origin/destination location"),
    distance_km: float = Query(..., gt=0, le=500, description="Distance to travel in km"),
    mode: str = Query("driving", description="Travel mode: driving / walking / transit"),
    window_hours: int = Query(6, ge=2, le=12, description="Hours ahead to evaluate"),
    db: Session = Depends(get_db),
) -> dict:
    """Recommend the top 3 best departure windows in the next N hours.

    Evaluates predicted congestion for each future hour and adjusts the ETA
    estimate accordingly. Helps commuters choose the optimal time to leave.
    """
    if mode not in {"driving", "walking", "transit"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be driving, walking, or transit",
        )

    now = datetime.now(timezone.utc)
    # Fetch current traffic once — used as baseline
    baseline_eta = calculate_eta_for_location(location, distance_km, mode, db)
    base_speed = baseline_eta.average_speed_kmh or get_speed_for_congestion("medium", mode)

    slots = []
    for h in range(window_hours):
        dep_time = now + timedelta(hours=h)
        target_hour = dep_time.hour

        pred = predict_traffic_congestion(location, target_hour, db)
        pred_level = pred["predicted_congestion"]

        adjusted_speed = max(base_speed * _SPEED_FACTOR.get(pred_level, 1.0), 1.0)
        eta_mins = round((distance_km / adjusted_speed) * 60, 1)
        eta_buffer = round(eta_mins * 1.1, 1)

        slots.append({
            "departure_offset_hours": h,
            "departure_time": dep_time.strftime("%H:%M"),
            "predicted_congestion": pred_level,
            "confidence_score": pred["confidence_score"],
            "estimated_eta_minutes": eta_mins,
            "estimated_eta_with_buffer_minutes": eta_buffer,
        })

    top_3 = sorted(slots, key=lambda s: s["estimated_eta_minutes"])[:3]

    logger.info(
        "Best departure for %s %.1fkm %s: +%sh (%.1f min ETA)",
        location,
        distance_km,
        mode,
        top_3[0]["departure_offset_hours"],
        top_3[0]["estimated_eta_minutes"],
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
    location: str = Query(..., min_length=2, description="Hyderabad location name"),
    db: Session = Depends(get_db),
) -> dict:
    """Personal commute friendliness score for a location (0–100).

    Scores based on current congestion, speed, and active incidents.
    Higher is better — green means go, red means avoid now.
    """
    from collections import Counter
    from datetime import timedelta, timezone
    from app.models.predictor import TrafficRecord, Incident
    from app.services.city_aliases import location_filter

    since = datetime.now(timezone.utc) - timedelta(hours=1)

    records = (
        db.query(TrafficRecord)
        .filter(
            location_filter(TrafficRecord.location, location),
            TrafficRecord.timestamp >= since,
        )
        .all()
    )

    active_incidents = (
        db.query(Incident)
        .filter(
            location_filter(Incident.location, location),
            Incident.is_active.is_(True),
        )
        .count()
    )

    if not records:
        return {
            "location": location,
            "score": 50,
            "grade": "C",
            "verdict": "No recent data",
            "active_incidents": active_incidents,
            "message": "Insufficient traffic data — score is estimated",
        }

    counts = Counter(r.congestion_level for r in records if r.congestion_level)
    total = len(records)
    high_pct = counts.get("high", 0) / total * 100
    medium_pct = counts.get("medium", 0) / total * 100

    speeds = [r.average_speed for r in records if r.average_speed]
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    score = round(max(0.0, min(100.0, 100 - high_pct * 0.6 - medium_pct * 0.2 - active_incidents * 5)), 1)

    if score >= 80:
        grade, verdict, color = "A", "Great time to commute", "green"
    elif score >= 65:
        grade, verdict, color = "B", "Good — minor delays likely", "light-green"
    elif score >= 50:
        grade, verdict, color = "C", "Moderate traffic — plan extra time", "yellow"
    elif score >= 35:
        grade, verdict, color = "D", "Heavy traffic — delays expected", "orange"
    else:
        grade, verdict, color = "F", "Avoid if possible — severe congestion", "red"

    logger.info("Commute score for %s: %.1f (%s)", location, score, grade)
    return {
        "location": location,
        "score": score,
        "grade": grade,
        "verdict": verdict,
        "color": color,
        "avg_speed_kmh": round(avg_speed, 1) if avg_speed else None,
        "active_incidents": active_incidents,
        "congestion_breakdown": {
            "high_pct": round(high_pct, 1),
            "medium_pct": round(medium_pct, 1),
            "low_pct": round(counts.get("low", 0) / total * 100, 1),
        },
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/stress-score", status_code=status.HTTP_200_OK)
def get_stress_score(
    location: str = Query(..., min_length=2, description="Location / route area (e.g. 'Silk Board, Bangalore')"),
    distance_km: float = Query(10.0, gt=0, le=500, description="Trip distance in km — defaults to 10 km if not provided"),
    mode: str = Query("driving", description="driving / walking / transit"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Commute stress score (0 = calm, 100 = intense) for current road conditions.

    Only `location` is required — `distance_km` defaults to 10 km and `mode` defaults to driving.

    Measures stress from four factors: duration vs free-flow, active incidents,
    speed variability (stop-and-go), and overall congestion level.
    Includes a personal comparison if you have trip history on this route.
    """
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
    target_arrival: str = Query(None, description="Optional ISO-8601 target arrival time"),
    db: Session = Depends(get_db),
) -> dict:
    """Smart departure advisor — answers 'Should I leave NOW or wait?'

    Compares current traffic conditions against the next 6-hour congestion forecast
    and returns a plain-language recommendation with estimated time savings.

    **Advice values:**
    - `leave_now` — current conditions are optimal or won't improve
    - `wait_N_minutes` — waiting N minutes (60 / 120 / 180) saves meaningful time
    - `already_late` — you must leave immediately to reach `target_arrival`
    """
    if mode not in {"driving", "walking", "transit"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be driving, walking, or transit",
        )

    now = datetime.now(timezone.utc)

    # Parse optional target arrival
    target_dt = None
    if target_arrival:
        try:
            from datetime import datetime as _dt
            target_dt = _dt.fromisoformat(target_arrival)
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target_arrival must be a valid ISO-8601 datetime string",
            )

    # Current ETA
    current_eta = calculate_eta_for_location(origin, distance_km, mode, db)
    current_eta_min = current_eta.eta_minutes
    current_congestion = current_eta.congestion_level

    # Must-leave check
    if target_dt is not None:
        must_leave_by = target_dt - timedelta(minutes=current_eta_min)
        if now >= must_leave_by:
            logger.info("should-i-leave: already_late for %s→%s", origin, destination)
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
                "congestion_forecast": [],
                "calculated_at": now.isoformat(),
            }

    # 6-hour congestion forecast
    forecast = []
    for h in range(6):
        target_hour = (now.hour + h) % 24
        pred = predict_traffic_congestion(origin, target_hour, db)
        forecast.append({
            "hour_offset": h,
            "hour_label": (now + timedelta(hours=h)).strftime("%H:%M"),
            "predicted_congestion": pred["predicted_congestion"],
            "confidence_score": round(pred["confidence_score"], 2),
        })

    # Find optimal departure window (first 3 hours)
    best_offset = 0
    best_score = _CONGESTION_SCORE.get(current_congestion, 1)
    for entry in forecast[:3]:
        score = _CONGESTION_SCORE.get(entry["predicted_congestion"], 1)
        if score < best_score:
            best_score = score
            best_offset = entry["hour_offset"]

    best_congestion = forecast[best_offset]["predicted_congestion"]
    optimal_speed = get_speed_for_congestion(best_congestion, mode)
    optimal_eta_min = (distance_km / max(optimal_speed, 1)) * 60
    savings = max(0.0, current_eta_min - optimal_eta_min)

    # Determine advice
    if current_congestion == "low" or best_offset == 0:
        advice = "leave_now"
        reason = (
            "Traffic is light right now — ideal time to head out."
            if current_congestion == "low"
            else "No significant improvement expected in the next few hours — leave now."
        )
    else:
        wait_minutes = best_offset * 60
        advice = f"wait_{wait_minutes}_minutes"
        reason = (
            f"Traffic predicted to ease in ~{wait_minutes} min. "
            f"Waiting could save approximately {round(savings, 0):.0f} minutes on your journey."
        )
        if savings < 2:
            advice = "leave_now"
            reason = "Traffic conditions are similar throughout the next few hours — no benefit in waiting."

    logger.info(
        "should-i-leave: %s→%s %.1fkm %s → advice=%s savings=%.1f min",
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
        "congestion_forecast": forecast,
        "calculated_at": now.isoformat(),
    }
