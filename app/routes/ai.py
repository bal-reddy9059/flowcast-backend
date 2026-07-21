"""AI Traffic Copilot — natural language traffic intelligence powered by Claude."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/ai", tags=["AI Traffic Copilot"])
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=3, max_length=500, description="Your traffic question in plain English")
    location: Optional[str] = Field(None, description="Primary location context (city or area)")
    destination: Optional[str] = Field(None, description="Destination if the question is route-related")


@router.post("/chat", status_code=status.HTTP_200_OK)
def ai_chat(
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Ask any traffic question in plain English and get an intelligent, data-backed answer.

    **Examples:**
    - "Should I leave for the airport now or wait 30 minutes?"
    - "What's the best route from Powai to BKC after 6pm on Mondays?"
    - "Why is traffic so bad near Silk Board today?"

    Powered by Claude AI with live FlowCast traffic data as context.
    """
    from app.services.ai_service import build_traffic_context, ask_claude

    location = payload.location or "India"
    context = build_traffic_context(location, db, user_id=str(current_user.id))

    if payload.destination:
        dest_ctx = build_traffic_context(payload.destination, db)
        context += f"\n--- Destination ---\n{dest_ctx}"

    answer = ask_claude(context, payload.message)

    logger.info("AI chat for user %s: '%s...'", current_user.id, payload.message[:40])
    return {
        "message": payload.message,
        "response": answer,
        "location_context": location,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/departure-coach", status_code=status.HTTP_200_OK)
def departure_coach(
    origin: str = Query(..., min_length=2, description="Starting location"),
    destination: str = Query(..., min_length=2, description="Destination"),
    distance_km: Optional[float] = Query(None, gt=0, le=500, description="Trip distance in km — auto-calculated if omitted"),
    mode: str = Query("driving", description="driving / walking / transit"),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """AI coach that learns from YOUR past trips and recommends a personalized departure window.

    Unlike the generic best-departure endpoint, this analyses your personal trip history
    to find patterns specific to your commute — day-of-week behaviour, typical delays, etc.
    """
    from app.models.trip import TripHistory
    from app.services.ai_service import generate_departure_coaching, build_traffic_context
    from app.services.prediction_service import predict_traffic_congestion

    if mode not in {"driving", "walking", "transit"}:
        raise HTTPException(status_code=400, detail="mode must be driving, walking, or transit")

    # Auto-calculate distance if not provided
    if distance_km is None:
        try:
            from app.routes.route import _geocode, _haversine_km
            orig_loc = _geocode(origin)
            dest_loc = _geocode(destination)
            if orig_loc and dest_loc:
                distance_km = round(_haversine_km(
                    orig_loc["lat"], orig_loc["lng"],
                    dest_loc["lat"], dest_loc["lng"],
                ) * 1.25, 1)
        except Exception:
            pass
        if distance_km is None:
            distance_km = 10.0

    # Gather personal trip history for this route
    trips = (
        db.query(TripHistory)
        .filter(
            TripHistory.user_id == current_user.id,
            TripHistory.origin_name.ilike(f"%{origin}%"),
            TripHistory.destination_name.ilike(f"%{destination}%"),
        )
        .order_by(TripHistory.created_at.desc())
        .limit(30)
        .all()
    )

    now = datetime.now(timezone.utc)

    # Build coaching context
    context_lines = [
        f"Route: {origin} → {destination}, {distance_km} km, mode={mode}",
        f"Current date/time (IST): {now.strftime('%A %d %b %Y %H:%M')}",
    ]

    if trips:
        etas = [t.predicted_eta_minutes for t in trips if t.predicted_eta_minutes]
        avg_eta = round(sum(etas) / len(etas), 1) if etas else 0
        by_hour: dict[int, list] = {}
        for t in trips:
            h = t.created_at.hour
            by_hour.setdefault(h, []).append(t.predicted_eta_minutes or avg_eta)
        hour_summary = {h: round(sum(v) / len(v), 1) for h, v in by_hour.items()}
        best_hour = min(hour_summary, key=lambda h: hour_summary[h]) if hour_summary else None
        worst_hour = max(hour_summary, key=lambda h: hour_summary[h]) if hour_summary else None

        context_lines += [
            f"Personal trip history: {len(trips)} recorded trips on this route",
            f"Average ETA: {avg_eta} min",
            f"Best hour historically: {best_hour}:00 ({hour_summary.get(best_hour, '-')} min avg ETA)" if best_hour else "",
            f"Worst hour historically: {worst_hour}:00 ({hour_summary.get(worst_hour, '-')} min avg ETA)" if worst_hour else "",
        ]

        by_day = {}
        for t in trips:
            d = t.created_at.strftime("%A")
            by_day.setdefault(d, []).append(t.predicted_eta_minutes or avg_eta)
        day_avg = {d: round(sum(v) / len(v), 1) for d, v in by_day.items()}
        today_name = now.strftime("%A")
        today_avg = day_avg.get(today_name)
        if today_avg:
            context_lines.append(f"Historical avg for {today_name}: {today_avg} min")
    else:
        context_lines.append("No personal trip history found for this route yet.")

    # Add live + forecast traffic
    live_ctx = build_traffic_context(origin, db)
    context_lines.append(f"Live traffic: {live_ctx}")

    for h in range(6):
        target_hour = (now.hour + h) % 24
        pred = predict_traffic_congestion(origin, target_hour, db)
        context_lines.append(f"Forecast +{h}h ({target_hour}:00): {pred['predicted_congestion']} congestion")

    coaching_context = "\n".join(l for l in context_lines if l)
    result = generate_departure_coaching(coaching_context)

    logger.info("Departure coach for user %s: %s→%s", current_user.id, origin, destination)
    return {
        "origin": origin,
        "destination": destination,
        "distance_km": distance_km,
        "mode": mode,
        "trip_history_count": len(trips),
        "coaching": result,
        "generated_at": now.isoformat(),
    }


@router.get("/commute-insight", status_code=status.HTTP_200_OK)
def weekly_commute_insight(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """AI-generated weekly commute insight report from your personal trip history.

    Surfaces patterns you might not notice: your worst day, your most efficient route,
    how your commute time has changed over weeks, and personalised tips.
    """
    from app.models.trip import TripHistory
    from app.services.ai_service import ask_claude

    since = datetime.now(timezone.utc) - timedelta(days=30)
    trips = (
        db.query(TripHistory)
        .filter(TripHistory.user_id == current_user.id, TripHistory.created_at >= since)
        .order_by(TripHistory.created_at.desc())
        .all()
    )

    if not trips:
        return {
            "insight": "No trip history found yet. Log some trips to unlock your personalized commute insights!",
            "trip_count": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # Build summary stats
    by_day: dict[str, list] = {}
    by_route: dict[str, list] = {}
    for t in trips:
        day = t.created_at.strftime("%A")
        by_day.setdefault(day, []).append(t.predicted_eta_minutes or 0)
        route_key = f"{t.origin_name}→{t.destination_name}"
        by_route.setdefault(route_key, []).append(t.predicted_eta_minutes or 0)

    context = f"Last 30 days: {len(trips)} trips logged\n"
    context += "By day of week: " + ", ".join(
        f"{d}: avg {round(sum(v)/len(v),1)} min" for d, v in by_day.items()
    ) + "\n"
    context += "Top routes: " + ", ".join(
        f"{r}: {len(v)} trips, avg {round(sum(v)/len(v),1)} min" for r, v in list(by_route.items())[:3]
    ) + "\n"

    total_etas = [t.predicted_eta_minutes for t in trips if t.predicted_eta_minutes]
    if total_etas:
        context += f"Overall avg ETA: {round(sum(total_etas)/len(total_etas),1)} min\n"

    prompt = "Give me a personalized weekly commute insight summary. Be specific, mention patterns, and give 2-3 actionable tips."
    insight_text = ask_claude(context, prompt)

    logger.info("Weekly commute insight for user %s (%d trips)", current_user.id, len(trips))
    return {
        "insight": insight_text,
        "trip_count": len(trips),
        "period_days": 30,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
