"""
Route optimization endpoints.

Provides API endpoints for route optimization, saving routes, and managing user routes.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.route import SavedRoute
from app.models.user import User
from app.schemas.route import (
    RouteRequest,
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


@router.get(
    "/optimize",
    response_model=RouteResponse,
    status_code=status.HTTP_200_OK,
)
async def optimize_route(
    origin_lat: float = Query(..., ge=-90, le=90, description="Origin latitude"),
    origin_lng: float = Query(..., ge=-180, le=180, description="Origin longitude"),
    destination_lat: float = Query(..., ge=-90, le=90, description="Destination latitude"),
    destination_lng: float = Query(..., ge=-180, le=180, description="Destination longitude"),
    mode: str = Query("driving", description="Travel mode: driving, walking, transit"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteResponse:
    """
    Get optimized route between two coordinates with real-time traffic insights.

    Validates coordinates are within Hyderabad, fetches route from Google Maps,
    enriches with local traffic data, and checks for active incidents.

    Args:
        origin_lat: Origin latitude (17.0-17.8)
        origin_lng: Origin longitude (78.0-78.9)
        destination_lat: Destination latitude
        destination_lng: Destination longitude
        mode: Travel mode (driving, walking, transit)
        current_user: Authenticated user
        db: Database session

    Returns:
        RouteResponse with segments, warnings, and ETA

    Raises:
        HTTPException 400: Coordinates outside Hyderabad region
        HTTPException 503: Google Maps API unavailable
    """
    validate_coordinates(origin_lat, origin_lng)
    validate_coordinates(destination_lat, destination_lng)

    try:
        route_data = await get_route_from_google(
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            dest_lat=destination_lat,
            dest_lng=destination_lng,
            mode=mode,
            api_key=None,
        )
    except HTTPException:
        raise

    segments = await enrich_route_with_traffic(route_data, db)

    warnings = await check_incidents_on_route(
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        dest_lat=destination_lat,
        dest_lng=destination_lng,
        db=db,
    )

    google_maps_url = await build_google_maps_url(
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        dest_lat=destination_lat,
        dest_lng=destination_lng,
        mode=mode,
    )

    congestion_levels = [segment.congestion_level for segment in segments]
    if "high" in congestion_levels:
        congestion_summary = "high"
    elif "medium" in congestion_levels:
        congestion_summary = "medium"
    else:
        congestion_summary = "low"

    total_eta = await calculate_eta(route_data["total_distance_km"], congestion_summary)

    logger.info(
        "Route optimization for user %s: %s to %s (mode: %s)",
        current_user.id,
        route_data["origin"],
        route_data["destination"],
        mode,
    )

    return RouteResponse(
        origin=route_data["origin"],
        destination=route_data["destination"],
        total_distance_km=route_data["total_distance_km"],
        total_eta_minutes=total_eta,
        congestion_summary=congestion_summary,
        segments=segments,
        warnings=warnings,
        google_maps_url=google_maps_url,
        fetched_at=None,
    )


@router.post(
    "/save",
    response_model=SavedRouteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_route(
    route_create: SavedRouteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedRouteResponse:
    """
    Save a route with a user-defined label for future quick access.

    Validates route name is unique per user and stores route with coordinates.

    Args:
        route_create: Route creation request with metadata
        current_user: Authenticated user
        db: Database session

    Returns:
        SavedRouteResponse with saved route details

    Raises:
        HTTPException 400: Route name already exists for this user
    """
    existing_route = (
        db.query(SavedRoute)
        .filter(
            SavedRoute.user_id == current_user.id,
            SavedRoute.route_name == route_create.route_name,
            SavedRoute.is_active.is_(True),
        )
        .first()
    )

    if existing_route:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Route with this name already exists",
        )

    new_route = SavedRoute(
        user_id=current_user.id,
        route_name=route_create.route_name,
        origin_lat=route_create.origin_lat,
        origin_lng=route_create.origin_lng,
        destination_lat=route_create.destination_lat,
        destination_lng=route_create.destination_lng,
        origin_name=route_create.origin_name,
        destination_name=route_create.destination_name,
    )

    db.add(new_route)
    db.commit()
    db.refresh(new_route)

    logger.info("User %s saved route: %s", current_user.id, route_create.route_name)

    return SavedRouteResponse.model_validate(new_route)


@router.get(
    "/saved",
    response_model=List[SavedRouteResponse],
    status_code=status.HTTP_200_OK,
)
async def list_saved_routes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[SavedRouteResponse]:
    """
    Retrieve all active saved routes for the current authenticated user.

    Returns saved routes ordered by most recent first.

    Args:
        current_user: Authenticated user
        db: Database session

    Returns:
        List of SavedRouteResponse (empty list if none found)
    """
    routes = (
        db.query(SavedRoute)
        .filter(
            SavedRoute.user_id == current_user.id,
            SavedRoute.is_active.is_(True),
        )
        .order_by(SavedRoute.created_at.desc())
        .all()
    )

    logger.info("User %s retrieved %d saved routes", current_user.id, len(routes))

    return [SavedRouteResponse.model_validate(route) for route in routes]


@router.delete(
    "/saved/{route_id}",
    status_code=status.HTTP_200_OK,
)
async def delete_saved_route(
    route_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Soft delete a saved route by ID (only route owner can delete).

    Sets is_active to False without removing the database record.

    Args:
        route_id: ID of the route to delete
        current_user: Authenticated user
        db: Database session

    Returns:
        JSON with deletion confirmation and route_id

    Raises:
        HTTPException 404: Route not found
        HTTPException 403: User does not own this route
    """
    route = db.query(SavedRoute).filter(SavedRoute.id == route_id).first()

    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Route not found",
        )

    if route.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to delete this route",
        )

    route.is_active = False
    db.commit()

    logger.info("User %s deleted route: %s (ID: %d)", current_user.id, route.route_name, route_id)

    return {
        "message": "Route deleted successfully",
        "route_id": route_id,
    }


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
