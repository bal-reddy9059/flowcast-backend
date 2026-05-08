"""
Route optimization endpoints.

Provides API endpoints for route optimization, saving routes, and managing user routes.
"""

import logging
import os
from dotenv import load_dotenv
from typing import List

load_dotenv()

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.route import SavedRoute
from app.models.user import User
from app.schemas.route import (
    RouteResponse,
    SavedRouteCreate,
    SavedRouteResponse,
)
from app.services.auth_service import get_current_user
from app.services.route_service import (
    build_google_maps_url,
    calculate_eta,
    check_incidents_on_route,
    enrich_route_with_traffic,
    get_route_from_google,
)

router = APIRouter(prefix="/routes", tags=["Route Optimization"])

logger = logging.getLogger(__name__)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_DIRECTIONS_API_KEY")

HYDERABAD_LAT_RANGE = (17.0, 17.8)
HYDERABAD_LNG_RANGE = (78.0, 78.9)


def validate_coordinates(lat: float, lng: float) -> None:
    """Validate that coordinates are within Hyderabad region."""
    if not (HYDERABAD_LAT_RANGE[0] <= lat <= HYDERABAD_LAT_RANGE[1]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Coordinates outside Hyderabad. Only Hyderabad routes supported.",
        )
    if not (HYDERABAD_LNG_RANGE[0] <= lng <= HYDERABAD_LNG_RANGE[1]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Coordinates outside Hyderabad. Only Hyderabad routes supported.",
        )


@router.get("/optimize", response_model=RouteResponse)
async def optimize_route(
    origin_lat: float = Query(..., description="Origin latitude"),
    origin_lng: float = Query(..., description="Origin longitude"),
    destination_lat: float = Query(..., description="Destination latitude"),
    destination_lng: float = Query(..., description="Destination longitude"),
    mode: str = Query("driving", description="Travel mode"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RouteResponse:
    """Optimize route between two points with traffic enrichment and incident checking.

    Validates coordinates are within Hyderabad bounds, fetches Google Maps route,
    enriches with local traffic data, checks for incidents, and calculates ETA.
    """
    # Validate coordinates
    validate_coordinates(origin_lat, origin_lng)
    validate_coordinates(destination_lat, destination_lng)

    # Get route from Google Maps
    route_data = await get_route_from_google(
        origin_lat, origin_lng, destination_lat, destination_lng, mode, GOOGLE_MAPS_API_KEY
    )

    # Enrich with traffic data
    segments = await enrich_route_with_traffic(route_data, db)

    # Check for incidents
    incidents = await check_incidents_on_route(
        origin_lat, origin_lng, destination_lat, destination_lng, db
    )

    # Build Google Maps URL
    google_maps_url = build_google_maps_url(
        origin_lat, origin_lng, destination_lat, destination_lng, mode
    )

    # Calculate total ETA
    total_distance = route_data["total_distance_km"]
    congestion_levels = [seg.congestion_level for seg in segments]
    worst_congestion = max(congestion_levels, key=lambda x: ["low", "medium", "high"].index(x))
    eta_minutes, eta_with_buffer = calculate_eta(total_distance, worst_congestion)

    # Build response
    response = RouteResponse(
        segments=segments,
        total_distance_km=round(total_distance, 1),
        total_eta_minutes=round(eta_with_buffer, 1),
        congestion_summary=worst_congestion,
        google_maps_url=google_maps_url,
        incidents=incidents,
    )

    logger.info("Route optimized for user %s", current_user.id)
    return response


@router.post("/save", response_model=SavedRouteResponse, status_code=status.HTTP_201_CREATED)
async def save_route(
    route_data: SavedRouteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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
    result = await db.execute(stmt)
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
        destination_lat=route_data.destination_lat,
        destination_lng=route_data.destination_lng,
        waypoints_lat=route_data.waypoints_lat,
        waypoints_lng=route_data.waypoints_lng,
        waypoints_names=route_data.waypoints_names,
    )
    db.add(saved_route)
    await db.commit()
    await db.refresh(saved_route)

    logger.info("Route saved for user %s: %s", current_user.id, route_data.route_name)
    return SavedRouteResponse.from_orm(saved_route)


@router.get("/saved", response_model=List[SavedRouteResponse])
async def get_saved_routes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[SavedRouteResponse]:
    """Get all active saved routes for the current user.

    Returns routes ordered by creation date (newest first).
    """
    stmt = select(SavedRoute).where(
        SavedRoute.user_id == current_user.id,
        SavedRoute.is_active == True,
    ).order_by(SavedRoute.created_at.desc())
    result = await db.execute(stmt)
    routes = result.scalars().all()

    logger.info("Retrieved %d saved routes for user %s", len(routes), current_user.id)
    return [SavedRouteResponse.from_orm(route) for route in routes]


@router.delete("/saved/{route_id}")
async def delete_saved_route(
    route_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Soft delete a saved route.

    Only the route owner can delete their routes.
    """
    stmt = select(SavedRoute).where(SavedRoute.id == route_id)
    result = await db.execute(stmt)
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
    await db.commit()

    logger.info("Route %d soft deleted for user %s", route_id, current_user.id)
    return {"message": "Route deleted", "id": route_id}


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
