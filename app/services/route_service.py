"""
Route optimization service for FlowCast.

Handles Google Maps API integration, traffic enrichment, ETA calculations,
incident checking, and Google Maps URL generation for Hyderabad routes.
"""

import logging
import math
import os
from datetime import datetime
from typing import List, Tuple

from dotenv import load_dotenv

load_dotenv()

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.predictor import TrafficRecord
from app.schemas.route import RouteSegment

logger = logging.getLogger(__name__)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_DIRECTIONS_API_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

_ORS_PROFILE = {
    "driving": "driving-car",
    "walking": "foot-walking",
    "transit": "driving-car",
}


async def get_route_from_google(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    mode: str,
    api_key: str,
) -> dict:
    """Fetch route data from Google Maps Directions API.

    Args:
        origin_lat: Origin latitude
        origin_lng: Origin longitude
        dest_lat: Destination latitude
        dest_lng: Destination longitude
        mode: Travel mode (driving, walking, transit)
        api_key: Google Maps API key

    Returns:
        dict: Parsed route data with steps, total distance, and duration

    Raises:
        HTTPException: 400 if no route found, 503 if API unavailable
    """
    url = "https://maps.googleapis.com/maps/api/directions/json"
    params = {
        "origin": f"{origin_lat},{origin_lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "mode": mode,
        "departure_time": "now",
        "traffic_model": "best_guess",
        "key": api_key,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            logger.error("Google Maps API call failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Google Maps API unavailable",
            )

    if data.get("status") == "ZERO_RESULTS":
        logger.warning("No route found between %s,%s and %s,%s", origin_lat, origin_lng, dest_lat, dest_lng)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No route found between these locations",
        )

    if data.get("status") != "OK":
        logger.error("Google Maps API error: %s", data.get("status"))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Maps API unavailable",
        )

    route = data["routes"][0]
    leg = route["legs"][0]

    steps = []
    for step in leg["steps"]:
        steps.append({
            "start_location": {
                "lat": step["start_location"]["lat"],
                "lng": step["start_location"]["lng"],
            },
            "end_location": {
                "lat": step["end_location"]["lat"],
                "lng": step["end_location"]["lng"],
            },
            "distance_km": step["distance"]["value"] / 1000,
            "duration_minutes": step["duration"]["value"] / 60,
        })

    total_distance_km = leg["distance"]["value"] / 1000
    total_duration_minutes = leg["duration"]["value"] / 60

    logger.info("Fetched route from Google Maps: %s km, %s minutes", total_distance_km, total_duration_minutes)

    return {
        "steps": steps,
        "total_distance_km": total_distance_km,
        "total_duration_minutes": total_duration_minutes,
        "source": "google_maps",
    }


async def get_route_from_ors(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    mode: str,
) -> dict:
    """Fetch route from OpenRouteService (free fallback for Google Maps).

    ORS uses [longitude, latitude] coordinate order.
    """
    if not ORS_API_KEY or ORS_API_KEY == "your_ors_key_here":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No routing service available — configure GOOGLE_MAPS_DIRECTIONS_API_KEY or ORS_API_KEY",
        )

    profile = _ORS_PROFILE.get(mode, "driving-car")
    url = f"https://api.openrouteservice.org/v2/directions/{profile}"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [[origin_lng, origin_lat], [dest_lng, dest_lat]],
        "instructions": True,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            logger.error("ORS API call failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Routing service unavailable",
            )

    feature = data["features"][0]
    props = feature["properties"]
    coords = feature["geometry"]["coordinates"]  # [lng, lat] pairs
    ors_steps = props["segments"][0]["steps"]
    summary = props["summary"]

    steps = []
    for step in ors_steps:
        wp = step["way_points"]
        start = coords[wp[0]]
        end = coords[wp[-1]]
        steps.append({
            "start_location": {"lat": start[1], "lng": start[0]},
            "end_location": {"lat": end[1], "lng": end[0]},
            "distance_km": step["distance"] / 1000,
            "duration_minutes": step["duration"] / 60,
        })

    logger.info("Fetched route from ORS: %.2f km, %.1f minutes", summary["distance"] / 1000, summary["duration"] / 60)

    return {
        "steps": steps,
        "total_distance_km": summary["distance"] / 1000,
        "total_duration_minutes": summary["duration"] / 60,
        "source": "openrouteservice",
    }


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line great-circle distance between two GPS points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _local_route(
    origin_lat: float, origin_lng: float,
    dest_lat: float, dest_lng: float,
) -> dict:
    """Estimate route using straight-line distance when no external API is available.

    Applies a 1.3× road-distance factor and splits the trip into two segments
    via the midpoint so downstream traffic enrichment has something to work with.
    """
    straight_km = _haversine_km(origin_lat, origin_lng, dest_lat, dest_lng)
    road_km = round(straight_km * 1.3, 2)          # road is ~30 % longer than straight line
    duration_min = round((road_km / 35.0) * 60, 1) # 35 km/h average urban speed

    mid_lat = (origin_lat + dest_lat) / 2
    mid_lng = (origin_lng + dest_lng) / 2
    half_km = road_km / 2
    half_min = duration_min / 2

    return {
        "steps": [
            {
                "start_location": {"lat": origin_lat, "lng": origin_lng},
                "end_location":   {"lat": mid_lat,    "lng": mid_lng},
                "distance_km":    half_km,
                "duration_minutes": half_min,
            },
            {
                "start_location": {"lat": mid_lat,  "lng": mid_lng},
                "end_location":   {"lat": dest_lat, "lng": dest_lng},
                "distance_km":    half_km,
                "duration_minutes": half_min,
            },
        ],
        "total_distance_km": road_km,
        "total_duration_minutes": duration_min,
        "source": "local_estimate",
    }


async def get_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    mode: str,
    api_key: str,
) -> dict:
    """Try Google Maps → ORS → local haversine estimate (never returns 503)."""
    if api_key and api_key != "your_google_maps_key_here":
        try:
            return await get_route_from_google(origin_lat, origin_lng, dest_lat, dest_lng, mode, api_key)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_400_BAD_REQUEST:
                raise
            logger.warning("Google Maps failed (%s) — trying ORS fallback", exc.detail)

    try:
        return await get_route_from_ors(origin_lat, origin_lng, dest_lat, dest_lng, mode)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            raise
        logger.warning("ORS failed (%s) — using local haversine estimate", exc.detail)

    logger.info("Using local route estimate for (%.4f,%.4f)→(%.4f,%.4f)",
                origin_lat, origin_lng, dest_lat, dest_lng)
    return _local_route(origin_lat, origin_lng, dest_lat, dest_lng)


async def enrich_route_with_traffic(route_data: dict, db: Session) -> List[RouteSegment]:
    """Enrich route steps with real-time traffic data.

    Args:
        route_data: Route data from Google Maps API
        db: Database session

    Returns:
        List[RouteSegment]: Route segments with traffic information
    """
    segments = []

    for step in route_data["steps"]:
        # Calculate midpoint of the step
        mid_lat = (step["start_location"]["lat"] + step["end_location"]["lat"]) / 2
        mid_lng = (step["start_location"]["lng"] + step["end_location"]["lng"]) / 2

        # Query nearest traffic record within 0.01 degrees (~1km)
        stmt = select(TrafficRecord).where(
            TrafficRecord.latitude.between(mid_lat - 0.01, mid_lat + 0.01),
            TrafficRecord.longitude.between(mid_lng - 0.01, mid_lng + 0.01),
        ).order_by(TrafficRecord.timestamp.desc()).limit(1)

        result = db.execute(stmt)
        record = result.scalars().first()

        if record:
            congestion_level = record.congestion_level
            warning = None
            if congestion_level == "high":
                warning = f"Heavy traffic near {record.location} — expect delays"
        else:
            congestion_level = "medium"  # Fallback
            warning = None

        segment = RouteSegment(
            start_location={
                "lat": step["start_location"]["lat"],
                "lng": step["start_location"]["lng"],
            },
            end_location={
                "lat": step["end_location"]["lat"],
                "lng": step["end_location"]["lng"],
            },
            distance_km=round(step["distance_km"], 1),
            duration_minutes=round(step["duration_minutes"], 1),
            congestion_level=congestion_level,
            congestion_warning=warning,
        )
        segments.append(segment)

    return segments


def calculate_eta(distance_km: float, congestion_level: str) -> Tuple[float, float]:
    """Calculate estimated travel time with congestion and buffer.

    Args:
        distance_km: Distance in kilometers
        congestion_level: Traffic congestion level

    Returns:
        Tuple[float, float]: (eta_minutes, eta_with_buffer_minutes)
    """
    speed_kmh = {
        "low": 60.0,
        "medium": 35.0,
        "high": 15.0,
    }.get(congestion_level, 40.0)  # Default fallback

    eta_minutes = (distance_km / speed_kmh) * 60
    eta_with_buffer = eta_minutes * 1.1  # 10% Hyderabad buffer

    return round(eta_minutes, 1), round(eta_with_buffer, 1)


async def check_incidents_on_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    db: Session,
) -> List[str]:
    """Check for active incidents along the route.

    Args:
        origin_lat: Origin latitude
        origin_lng: Origin longitude
        dest_lat: Destination latitude
        dest_lng: Destination longitude
        db: Database session

    Returns:
        List[str]: List of incident warnings
    """
    # Build bounding box
    min_lat = min(origin_lat, dest_lat)
    max_lat = max(origin_lat, dest_lat)
    min_lng = min(origin_lng, dest_lng)
    max_lng = max(origin_lng, dest_lng)

    # Note: Assuming Incident model exists, but gracefully handle if not
    try:
        from app.models.predictor import Incident  # noqa: PLC0415

        stmt = select(Incident).where(
            Incident.latitude.between(min_lat, max_lat),
            Incident.longitude.between(min_lng, max_lng),
            Incident.resolved_at.is_(None),
        )
        result = db.execute(stmt)
        incidents = result.scalars().all()

        warnings = []
        for incident in incidents:
            warning = f"{incident.incident_type.title()} near {incident.location} — {incident.severity}"
            warnings.append(warning)

        return warnings
    except ImportError:
        # Incident model not found, return empty list
        return []


def build_google_maps_url(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    mode: str,
) -> str:
    """Build Google Maps directions URL.

    Args:
        origin_lat: Origin latitude
        origin_lng: Origin longitude
        dest_lat: Destination latitude
        dest_lng: Destination longitude
        mode: Travel mode

    Returns:
        str: Google Maps URL
    """
    return (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={origin_lat},{origin_lng}"
        f"&destination={dest_lat},{dest_lng}"
        f"&travelmode={mode}"
    )
