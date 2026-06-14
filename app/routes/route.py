"""
Route optimization endpoints.

Provides API endpoints for route optimization, saving routes, and managing user routes.
"""

import logging
import math
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from typing import List, Optional

load_dotenv()

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.route import SavedRoute
from app.models.share import RouteShareToken
from app.models.user import User
from app.schemas.route import (
    RouteResponse,
    SavedRouteCreate,
    SavedRouteResponse,
)
from app.services.auth_service import get_current_user
from app.services.prediction_service import predict_traffic_congestion
from app.services.route_service import (
    build_google_maps_url,
    check_incidents_on_route,
    enrich_route_with_traffic,
    get_route,
)

router = APIRouter(prefix="/routes", tags=["Route Optimization"])

logger = logging.getLogger(__name__)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_DIRECTIONS_API_KEY")

# Bounding box covering the entire Indian subcontinent
INDIA_LAT_RANGE = (6.0, 37.5)
INDIA_LNG_RANGE = (68.0, 97.5)


def validate_coordinates(lat: float, lng: float) -> None:
    """Validate that coordinates are within India."""
    if not (INDIA_LAT_RANGE[0] <= lat <= INDIA_LAT_RANGE[1]) or \
       not (INDIA_LNG_RANGE[0] <= lng <= INDIA_LNG_RANGE[1]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Coordinates ({lat}, {lng}) are outside India. "
                f"Valid range: lat {INDIA_LAT_RANGE[0]}–{INDIA_LAT_RANGE[1]}, "
                f"lng {INDIA_LNG_RANGE[0]}–{INDIA_LNG_RANGE[1]}."
            ),
        )


@router.get("/optimize", response_model=RouteResponse)
async def optimize_route(
    origin_lat: float = Query(..., ge=6.0, le=37.5, description="Origin latitude (India: 6.0–37.5)"),
    origin_lng: float = Query(..., ge=68.0, le=97.5, description="Origin longitude (India: 68.0–97.5)"),
    destination_lat: float = Query(..., ge=6.0, le=37.5, description="Destination latitude (India: 6.0–37.5)"),
    destination_lng: float = Query(..., ge=68.0, le=97.5, description="Destination longitude (India: 68.0–97.5)"),
    mode: str = Query("driving", description="Travel mode"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteResponse:
    """Optimize route between two points with traffic enrichment and incident checking.

    Validates coordinates are within India bounds, fetches Google Maps route,
    enriches with local traffic data, checks for incidents, and calculates ETA.
    """
    # Validate coordinates
    validate_coordinates(origin_lat, origin_lng)
    validate_coordinates(destination_lat, destination_lng)

    # Get route from Google Maps
    route_data = await get_route(
        origin_lat, origin_lng, destination_lat, destination_lng, mode, GOOGLE_MAPS_API_KEY
    )

    # Enrich with traffic data
    segments = await enrich_route_with_traffic(route_data, db)

    # Check for incidents
    warnings = await check_incidents_on_route(
        origin_lat, origin_lng, destination_lat, destination_lng, db
    )

    # Build Google Maps URL
    google_maps_url = build_google_maps_url(
        origin_lat, origin_lng, destination_lat, destination_lng, mode
    )

    # Calculate total ETA
    # Use the routing provider's own duration (traffic-aware for Google Maps).
    # total_distance is summed from segments so it matches what the response shows.
    total_distance = round(sum(s.distance_km for s in segments), 1)
    base_eta = route_data["total_duration_minutes"]
    congestion_levels = [seg.congestion_level for seg in segments]
    worst_congestion = max(congestion_levels, key=lambda x: ["low", "medium", "high"].index(x))
    eta_minutes = round(base_eta, 1)
    eta_with_buffer = round(base_eta * 1.1, 1)

    # Build response
    response = RouteResponse(
        origin=f"{origin_lat},{origin_lng}",
        destination=f"{destination_lat},{destination_lng}",
        segments=segments,
        total_distance_km=total_distance,
        total_eta_minutes=eta_minutes,
        total_eta_with_buffer_minutes=eta_with_buffer,
        congestion_summary=worst_congestion,
        warnings=warnings,
        google_maps_url=google_maps_url,
        route_source=route_data.get("source", "google_maps"),
        fetched_at=datetime.now(timezone.utc),
    )

    logger.info("Route optimized for user %s", current_user.id)
    return response


def _geocode(name: str) -> Optional[dict]:
    """Fuzzy-match a free-text location name against INDIA_LOCATIONS."""
    from app.services.india_locations import INDIA_LOCATIONS
    name_lower = name.lower().strip()
    # Exact match
    for loc in INDIA_LOCATIONS:
        if loc["name"].lower() == name_lower:
            return loc
    # Substring match
    best: Optional[dict] = None
    best_len = 0
    for loc in INDIA_LOCATIONS:
        loc_name = loc["name"].lower()
        if loc_name in name_lower or name_lower in loc_name:
            match_len = len(loc_name)
            if match_len > best_len:
                best_len = match_len
                best = loc
    if best:
        return best
    # Word match (≥4 chars)
    for loc in INDIA_LOCATIONS:
        for word in name_lower.split():
            if len(word) >= 4 and word in loc["name"].lower():
                return loc
    return None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((phi2 - phi1) / 2) ** 2
         + math.cos(phi1) * math.cos(phi2)
         * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class OptimizeByNameRequest(BaseModel):
    origin: str = Field(..., min_length=2)
    destination: str = Field(..., min_length=2)
    mode: str = Field("driving", pattern="^(driving|transit|walking)$")
    alternatives: bool = False


@router.post("/optimize")
async def optimize_route_by_name(
    payload: OptimizeByNameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Optimize a route given free-text origin/destination names.

    Geocodes the names internally via INDIA_LOCATIONS, calculates distance
    using haversine + 25% routing overhead, and enriches with live traffic data.
    Returns the same schema as ORIGIN_STUB on the frontend.
    """
    from app.models.predictor import TrafficRecord, Incident

    HYDERABAD_DEFAULT = {"lat": 17.4486, "lng": 78.3908, "name": "Hitech City"}

    origin_loc = _geocode(payload.origin) or {**HYDERABAD_DEFAULT, "name": payload.origin}
    dest_loc   = _geocode(payload.destination) or {**HYDERABAD_DEFAULT, "name": payload.destination}

    straight_km = _haversine_km(
        origin_loc["lat"], origin_loc["lng"],
        dest_loc["lat"], dest_loc["lng"],
    )
    # 25% overhead converts straight-line to road distance
    road_km = round(straight_km * 1.25, 1)

    # Latest traffic near origin
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=1)
    origin_word = origin_loc["name"].split()[0]
    origin_traffic = (
        db.query(TrafficRecord)
        .filter(
            TrafficRecord.location.ilike(f"%{origin_word}%"),
            TrafficRecord.timestamp >= since,
        )
        .order_by(TrafficRecord.timestamp.desc())
        .first()
    )

    if origin_traffic:
        avg_speed = float(origin_traffic.average_speed or 35.0)
        congestion = origin_traffic.congestion_level or "medium"
        confidence = "high"
    else:
        avg_speed = {"driving": 38.0, "transit": 22.0, "walking": 5.0}.get(payload.mode, 38.0)
        congestion = "medium"
        confidence = "medium"

    if payload.mode == "transit":
        avg_speed *= 0.6
    elif payload.mode == "walking":
        avg_speed = 5.0

    avg_speed = max(avg_speed, 3.0)
    duration_min = round(road_km / avg_speed * 60)

    opt_score  = {"low": 95, "medium": 78, "high": 62}.get(congestion, 78)
    co2_factor = {"driving": 0.12, "transit": 0.04, "walking": 0.0}.get(payload.mode, 0.12)
    co2_kg     = round(road_km * co2_factor, 2)
    trees_offset = round(co2_kg / 10, 2)

    depart_offset = {"high": 20, "medium": 5, "low": 0}.get(congestion, 5)
    dep = now + timedelta(minutes=depart_offset)
    hour12 = dep.hour % 12 or 12
    best_dep = f"{hour12}:{dep.minute:02d} {'AM' if dep.hour < 12 else 'PM'}"

    incidents = (
        db.query(Incident)
        .filter(
            Incident.is_active.is_(True),
            Incident.latitude.between(origin_loc["lat"] - 0.1, origin_loc["lat"] + 0.1),
            Incident.longitude.between(origin_loc["lng"] - 0.1, origin_loc["lng"] + 0.1),
        )
        .limit(3)
        .all()
    )
    alerts = [
        {
            "location": i.location or origin_loc["name"],
            "status": (i.incident_type or "incident").replace("_", " ").title(),
            "speed": max(int(avg_speed * 0.7), 5),
        }
        for i in incidents
    ] or [{"location": origin_loc["name"], "status": "Fluid", "speed": int(avg_speed)}]

    logger.info(
        "Named route optimized: %s → %s (%.1fkm, %dmin) user=%s",
        payload.origin, payload.destination, road_km, duration_min, current_user.id,
    )
    return {
        "origin": origin_loc["name"],
        "destination": dest_loc["name"],
        "distance_km": road_km,
        "duration_minutes": duration_min,
        "avg_speed_kmh": int(avg_speed),
        "optimization_score": opt_score,
        "co2_kg": co2_kg,
        "trees_offset": trees_offset,
        "best_departure": best_dep,
        "confidence": confidence,
        "alerts": alerts,
        "congestion_summary": congestion,
        "mode": payload.mode,
    }


@router.post("/save", response_model=SavedRouteResponse, status_code=status.HTTP_201_CREATED)
async def save_route(
    route_data: SavedRouteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedRouteResponse:
    """Save a route for the current user.

    Checks for duplicate route names and creates a new saved route.
    """
    # Check for duplicate name
    stmt = select(SavedRoute).where(
        SavedRoute.user_id == current_user.id,
        SavedRoute.route_name == route_data.route_name,
        SavedRoute.is_active == True,
    )
    result = db.execute(stmt)
    existing = result.scalars().first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Route name already exists",
        )

    # Create new saved route
    saved_route = SavedRoute(
        user_id=current_user.id,
        route_name=route_data.route_name,
        origin_lat=route_data.origin_lat,
        origin_lng=route_data.origin_lng,
        origin_name=route_data.origin_name,
        destination_lat=route_data.destination_lat,
        destination_lng=route_data.destination_lng,
        destination_name=route_data.destination_name,
    )
    db.add(saved_route)
    db.commit()
    db.refresh(saved_route)

    logger.info("Route saved for user %s: %s", current_user.id, route_data.route_name)
    return SavedRouteResponse.model_validate(saved_route)


@router.get("/saved", response_model=List[SavedRouteResponse])
async def get_saved_routes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[SavedRouteResponse]:
    """Get all active saved routes for the current user.

    Returns routes ordered by creation date (newest first).
    """
    stmt = select(SavedRoute).where(
        SavedRoute.user_id == current_user.id,
        SavedRoute.is_active == True,
    ).order_by(SavedRoute.created_at.desc())
    result = db.execute(stmt)
    routes = result.scalars().all()

    logger.info("Retrieved %d saved routes for user %s", len(routes), current_user.id)
    return [SavedRouteResponse.model_validate(route) for route in routes]


@router.get("/saved/{route_id}/report")
def get_route_report(
    route_id: uuid.UUID = Path(
        ...,
        description="Saved route ID — get this from `GET /api/v1/routes/saved` (copy any `id`)",
        openapi_examples={"default": {"value": "550e8400-e29b-41d4-a716-446655440000"}},
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Traffic analysis report for a saved route.

    Returns current congestion near origin and destination, hourly prediction
    for the next 6 hours, active incident count, and a recommended departure time.
    """
    from app.models.predictor import TrafficRecord, Incident
    from datetime import timedelta, timezone
    from collections import Counter

    stmt = select(SavedRoute).where(
        SavedRoute.id == route_id,
        SavedRoute.user_id == current_user.id,
        SavedRoute.is_active == True,
    )
    route = db.execute(stmt).scalars().first()
    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved route not found",
        )

    now = datetime.now(timezone.utc)
    since_1h = now - timedelta(hours=1)

    def _latest_traffic(name: str):
        return (
            db.query(TrafficRecord)
            .filter(TrafficRecord.location.ilike(f"%{name}%"))
            .order_by(TrafficRecord.created_at.desc())
            .first()
        )

    origin_record = _latest_traffic(route.origin_name)
    dest_record = _latest_traffic(route.destination_name)

    def _record_summary(rec):
        if not rec:
            return {"congestion_level": "unknown", "avg_speed_kmh": None, "vehicle_count": None}
        return {
            "congestion_level": rec.congestion_level or "unknown",
            "avg_speed_kmh": rec.average_speed,
            "vehicle_count": rec.vehicle_count,
        }

    # 6-hour congestion forecast for origin
    forecast = []
    best_slot = None
    best_score = 3
    for h in range(6):
        target_hour = (now.hour + h) % 24
        pred = predict_traffic_congestion(route.origin_name, target_hour, db)
        score = {"low": 0, "medium": 1, "high": 2}.get(pred["predicted_congestion"], 1)
        slot = {
            "hour_offset": h,
            "departure_time": (now + timedelta(hours=h)).strftime("%H:%M"),
            "predicted_congestion": pred["predicted_congestion"],
            "confidence_score": pred["confidence_score"],
        }
        forecast.append(slot)
        if score < best_score:
            best_score = score
            best_slot = slot

    # Active incidents near origin / destination
    incident_count = (
        db.query(Incident)
        .filter(
            Incident.is_active.is_(True),
            Incident.latitude.between(route.origin_lat - 0.05, route.origin_lat + 0.05),
            Incident.longitude.between(route.origin_lng - 0.05, route.origin_lng + 0.05),
        )
        .count()
    )

    logger.info("Route report generated for route %s (user %s)", route_id, current_user.id)
    return {
        "route_id": route.id,
        "route_name": route.route_name,
        "origin": route.origin_name,
        "destination": route.destination_name,
        "origin_traffic": _record_summary(origin_record),
        "destination_traffic": _record_summary(dest_record),
        "active_incidents_near_origin": incident_count,
        "recommended_departure": best_slot,
        "6h_origin_forecast": forecast,
        "report_generated_at": now.isoformat(),
    }


@router.post("/saved/{route_id}/share", status_code=status.HTTP_201_CREATED)
def create_share_link(
    route_id: uuid.UUID = Path(
        ...,
        description="Saved route ID — get this from `GET /api/v1/routes/saved` (copy any `id`)",
        openapi_examples={"default": {"value": "550e8400-e29b-41d4-a716-446655440000"}},
    ),
    expires_days: Optional[int] = Query(7, ge=1, le=90, description="Link expiry in days. Omit for no expiry."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Generate a public share link for a saved route.

    The link is readable by anyone — no login required.
    Expires after `expires_days` days (default 7). Pass expires_days=0 for a permanent link.
    """
    route = db.execute(
        select(SavedRoute).where(
            SavedRoute.id == route_id,
            SavedRoute.user_id == current_user.id,
            SavedRoute.is_active == True,
        )
    ).scalars().first()
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved route not found")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=expires_days) if expires_days else None

    share = RouteShareToken(
        route_id=route_id,
        user_id=current_user.id,
        token=token,
        expires_at=expires_at,
    )
    db.add(share)
    db.commit()

    logger.info("Share token created for route %s by user %s", route_id, current_user.id)
    return {
        "token": token,
        "share_url": f"/api/v1/routes/shared/{token}",
        "route_name": route.route_name,
        "expires_at": expires_at.isoformat() if expires_at else "never",
    }


@router.get("/shared/{token}", status_code=status.HTTP_200_OK)
def view_shared_route(
    token: str = Path(
        ...,
        description="Share token — returned by `POST /api/v1/routes/saved/{route_id}/share` (copy the `token` field)",
        openapi_examples={"default": {"value": "abc123xyz"}},
    ),
    db: Session = Depends(get_db),
) -> dict:
    """View a shared route by its public token — no authentication required."""
    share = db.query(RouteShareToken).filter(RouteShareToken.token == token).first()
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found or expired")

    if share.expires_at and share.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Share link has expired")

    route = db.execute(
        select(SavedRoute).where(SavedRoute.id == share.route_id, SavedRoute.is_active == True)
    ).scalars().first()
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route no longer exists")

    share.view_count += 1
    db.commit()

    return {
        "route_name": route.route_name,
        "origin": {"name": route.origin_name, "lat": route.origin_lat, "lng": route.origin_lng},
        "destination": {"name": route.destination_name, "lat": route.destination_lat, "lng": route.destination_lng},
        "shared_by": "FlowCast user",
        "view_count": share.view_count,
        "expires_at": share.expires_at.isoformat() if share.expires_at else "never",
        "created_at": share.created_at.isoformat(),
    }


@router.delete("/saved/{route_id}")
async def delete_saved_route(
    route_id: uuid.UUID = Path(
        ...,
        description="Saved route ID — get this from `GET /api/v1/routes/saved` (copy any `id`)",
        openapi_examples={"default": {"value": "550e8400-e29b-41d4-a716-446655440000"}},
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Soft delete a saved route.

    Only the route owner can delete their routes.
    """
    stmt = select(SavedRoute).where(SavedRoute.id == route_id)
    result = db.execute(stmt)
    route = result.scalars().first()
    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Route not found",
        )
    if route.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this route",
        )

    # Soft delete
    route.is_active = False
    db.commit()

    logger.info("Route %s soft deleted for user %s", route_id, current_user.id)
    return {"message": "Route deleted", "id": route_id}


@router.get("/narrative", status_code=status.HTTP_200_OK)
def route_narrative(
    origin: str = Query(..., min_length=2, description="Origin location name"),
    destination: str = Query(..., min_length=2, description="Destination name"),
    distance_km: float = Query(..., gt=0, le=500, description="Trip distance in km"),
    mode: str = Query("driving", description="driving / walking / transit"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """AI-generated route briefing in plain English — like a traffic reporter.

    Takes your origin, destination, and distance and returns a 2-3 sentence
    human-readable summary of current conditions, any delays or incidents,
    and a departure tip. Much more useful than raw congestion levels.

    **Example output:**
    "Your 22km drive from Whitefield to MG Road should take about 48 minutes today.
    Watch out: Silk Board Flyover is showing heavy congestion right now, adding roughly
    11 minutes. Consider leaving before 5:30pm to beat the peak."
    """
    from app.services.eta_service import calculate_eta_for_location
    from app.services.narrative_service import build_route_narrative

    if mode not in {"driving", "walking", "transit"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode must be driving, walking, or transit")

    eta = calculate_eta_for_location(origin, distance_km, mode, db)
    result = build_route_narrative(
        origin=origin,
        destination=destination,
        distance_km=distance_km,
        eta_minutes=eta.eta_minutes,
        congestion_level=eta.congestion_level,
        avg_speed_kmh=eta.average_speed_kmh,
        db=db,
    )
    logger.info("Route narrative for %s→%s (%.1fkm %s)", origin, destination, distance_km, mode)
    return result


# Test commands (replace <token> with actual JWT token)
# curl -X GET "http://localhost:8000/routes/optimize?origin_lat=17.4401&origin_lng=78.3489&destination_lat=17.4486&destination_lng=78.3908&mode=driving" -H "Authorization: Bearer <token>"


# ────────────────────────────────────────────────────────────────────────────────
# TESTING ENDPOINTS WITH CURL COMMANDS
# ────────────────────────────────────────────────────────────────────────────────

# 1. GET /routes/optimize
# Hyderabad Test Coordinates:
#   Origin (Gachibowli):   lat=17.4401, lng=78.3489
#   Destination (Hitech):  lat=17.4486, lng=78.3908
#
# Example curl command (replace TOKEN with your actual JWT):
# ```bash
# curl -X GET "http://localhost:8000/routes/optimize?origin_lat=17.4401&origin_lng=78.3489&destination_lat=17.4486&destination_lng=78.3908&mode=driving" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN_HERE" \
#   -H "Content-Type: application/json"
# ```
#
# Response (200):
# {
#   "origin": "Gachibowli, Hyderabad",
#   "destination": "Hitech City, Hyderabad",
#   "total_distance_km": 5.2,
#   "total_eta_minutes": 18,
#   "congestion_summary": "medium",
#   "segments": [
#     {
#       "start_location": {"lat": 17.4401, "lng": 78.3489},
#       "end_location": {"lat": 17.4450, "lng": 78.3600},
#       "distance_km": 2.1,
#       "duration_minutes": 8,
#       "congestion_level": "medium",
#       "congestion_warning": null
#     }
#   ],
#   "warnings": ["Accident near Hitech City — Severity: moderate"],
#   "google_maps_url": "https://www.google.com/maps/dir/?api=1&origin=17.4401,78.3489&destination=17.4486,78.3908&travelmode=driving",
#   "fetched_at": "2026-05-07T10:30:00Z"
# }
#
# Error cases:
# - 400: Coordinates outside Hyderabad
#   "Coordinates outside Hyderabad. Only Hyderabad routes supported."
# - 503: Google Maps API unavailable
#   "Google Maps unavailable"


# 2. POST /routes/save
# Example curl command:
# ```bash
# curl -X POST "http://localhost:8000/routes/save" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN_HERE" \
#   -H "Content-Type: application/json" \
#   -d '{
#     "route_name": "Home to Office",
#     "origin_lat": 17.4401,
#     "origin_lng": 78.3489,
#     "destination_lat": 17.4486,
#     "destination_lng": 78.3908,
#     "origin_name": "Gachibowli",
#     "destination_name": "Hitech City"
#   }'
# ```
#
# Response (201):
# {
#   "id": 1,
#   "user_id": 42,
#   "route_name": "Home to Office",
#   "origin_name": "Gachibowli",
#   "destination_name": "Hitech City",
#   "is_active": true,
#   "created_at": "2026-05-07T10:30:00Z"
# }
#
# Error cases:
# - 400: Route with this name already exists
#   "Route with this name already exists"


# 3. GET /routes/saved
# Example curl command:
# ```bash
# curl -X GET "http://localhost:8000/routes/saved" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN_HERE"
# ```
#
# Response (200):
# [
#   {
#     "id": 1,
#     "user_id": 42,
#     "route_name": "Home to Office",
#     "origin_name": "Gachibowli",
#     "destination_name": "Hitech City",
#     "is_active": true,
#     "created_at": "2026-05-07T10:30:00Z"
#   },
#   {
#     "id": 2,
#     "user_id": 42,
#     "route_name": "Work to Gym",
#     "origin_name": "Hitech City",
#     "destination_name": "Banjara Hills",
#     "is_active": true,
#     "created_at": "2026-05-07T10:25:00Z"
#   }
# ]
# Returns empty array [] if user has no saved routes (200 OK, not 404)


# 4. DELETE /routes/saved/{route_id}
# Example curl command:
# ```bash
# curl -X DELETE "http://localhost:8000/routes/saved/1" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN_HERE"
# ```
#
# Response (200):
# {
#   "message": "Route deleted successfully",
#   "route_id": 1
# }
#
# Error cases:
# - 404: Route not found
#   "Route not found"
# - 403: User does not own this route
#   "You do not have permission to delete this route"


# ────────────────────────────────────────────────────────────────────────────────
# HOW TO GET JWT TOKEN FOR TESTING
# ────────────────────────────────────────────────────────────────────────────────
#
# 1. Register a new user:
# ```bash
# curl -X POST "http://localhost:8000/auth/register" \
#   -H "Content-Type: application/json" \
#   -d '{
#     "email": "user@example.com",
#     "full_name": "John Doe",
#     "password": "SecurePassword123!"
#   }'
# ```
#
# 2. Login to get JWT:
# ```bash
# curl -X POST "http://localhost:8000/auth/login" \
#   -H "Content-Type: application/json" \
#   -d '{
#     "email": "user@example.com",
#     "password": "SecurePassword123!"
#   }'
# ```
#
# Response:
# {
#   "access_token": "eyJhbGc...",
#   "token_type": "bearer",
#   "expires_in": 3600
# }
#
# 3. Use the access_token in all subsequent requests with Bearer prefix:
#    -H "Authorization: Bearer <access_token>"
