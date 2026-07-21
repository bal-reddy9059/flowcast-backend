"""Route narrative service — turns route data into human-readable briefings."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def build_route_narrative(
    origin: str,
    destination: str,
    distance_km: float,
    eta_minutes: float,
    congestion_level: str,
    avg_speed_kmh: float,
    db: Session,
) -> dict:
    """Build a human-readable route briefing using live traffic data."""
    from app.models.predictor import Incident
    from app.services.ai_service import generate_narrative
    from app.services.city_aliases import location_filter

    # Gather incidents near origin and destination
    incidents = (
        db.query(Incident)
        .filter(
            Incident.is_active.is_(True),
            (location_filter(Incident.location, origin) | location_filter(Incident.location, destination)),
        )
        .limit(3)
        .all()
    )
    incident_notes = [f"{inc.incident_type} at {inc.location} ({inc.severity})" for inc in incidents]

    # Expected ETA at free-flow (low congestion speed)
    from app.services.eta_service import get_speed_for_congestion
    free_flow_speed = get_speed_for_congestion("low", "driving")
    expected_eta = (distance_km / max(free_flow_speed, 1)) * 60
    delay_min = max(0.0, eta_minutes - expected_eta)

    # Align congestion label with actual delay so narrative/UI stay consistent
    if delay_min >= 30 or (expected_eta > 0 and delay_min / expected_eta >= 1.5):
        congestion_level = "high"
    elif delay_min >= 12 or (expected_eta > 0 and delay_min / expected_eta >= 0.5):
        if congestion_level == "low":
            congestion_level = "medium"
    # else keep provided level

    context_lines = [
        f"Route: {origin} → {destination}",
        f"Distance: {distance_km} km",
        f"Current ETA: {round(eta_minutes, 0):.0f} minutes",
        f"Expected free-flow ETA: {round(expected_eta, 0):.0f} minutes",
        f"Delay: {round(delay_min, 0):.0f} minutes",
        f"Congestion: {congestion_level}",
        f"Average speed: {round(avg_speed_kmh, 1)} km/h",
    ]
    if incident_notes:
        context_lines.append("Active incidents: " + "; ".join(incident_notes))

    # Check if waiting might help (next 3h forecast)
    from app.services.prediction_service import predict_traffic_congestion
    now = datetime.now(timezone.utc)
    forecasts = []
    for h in range(1, 4):
        target_hour = (now.hour + h) % 24
        pred = predict_traffic_congestion(origin, target_hour, db)
        forecasts.append(f"+{h}h: {pred['predicted_congestion']}")
    context_lines.append("Forecast: " + ", ".join(forecasts))

    route_context = "\n".join(context_lines)
    narrative_text = generate_narrative(route_context)

    return {
        "narrative": narrative_text,
        "route": {"origin": origin, "destination": destination, "distance_km": distance_km},
        "traffic": {
            "eta_minutes": round(eta_minutes, 1),
            "expected_eta_minutes": round(expected_eta, 1),
            "delay_minutes": round(delay_min, 1),
            "congestion_level": congestion_level,
            "avg_speed_kmh": round(avg_speed_kmh, 1),
        },
        "active_incidents": len(incidents),
        "generated_at": now.isoformat(),
    }
