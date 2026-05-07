"""
Route optimization service.

Provides Google Directions integration and route enrichment using local traffic data.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException, status
from sqlalchemy import func

from app.models.predictor import Incident, TrafficRecord
from app.schemas.route import RouteSegment

load_dotenv()

GOOGLE_MAPS_ENDPOINT = "https://maps.googleapis.com/maps/api/directions/json"
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

logger = logging.getLogger(__name__)

CONGESTION_SPEEDS = {
    "low": 60.0,
    "medium": 35.0,
    "high": 15.0,
}

HYDERABAD_RADIUS_DEGREES = 0.01


async def get_route_from_google(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    mode: str,
    api_key: str,
) -> Dict[str, Any]:
    """Fetch optimized route data from Google Directions API."""
    effective_api_key = api_key or GOOGLE_MAPS_API_KEY
    if not effective_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Maps API key is not configured",
        )

    params = {
        "origin": f"{origin_lat},{origin_lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "mode": mode,
        "key": effective_api_key,
        "departure_time": "now",
        "traffic_model": "best_guess",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(GOOGLE_MAPS_ENDPOINT, params=params)
        except httpx.RequestError as error:
            logger.error("Google Maps request error: %s", error)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Google Maps unavailable",
            )

    if response.status_code != status.HTTP_200_OK:
        logger.error(
            "Google Maps returned non-200 status: %s %s",
            response.status_code,
            response.text,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Maps unavailable",
        )

    data = response.json()
    api_status = data.get("status")
    if api_status == "ZERO_RESULTS":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No route found for the provided coordinates",
        )
    if api_status != "OK":
        logger.error("Google Maps API error: %s - %s", api_status, data.get("error_message"))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Maps unavailable",
        )

    routes = data.get("routes") or []
    if not routes:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Maps did not return any routes",
        )

    leg = routes[0].get("legs", [])[0]
    if not leg:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Maps did not return route legs",
        )

    total_distance_km = leg["distance"]["value"] / 1000.0
    total_duration_minutes = leg["duration"]["value"] / 60.0
    steps = []

    for step in leg.get("steps", []):
        start_location = step["start_location"]
        end_location = step["end_location"]
        steps.append(
            {
                "start_location": {
                    "lat": float(start_location["lat"]),
                    "lng": float(start_location["lng"]),
                },
                "end_location": {
                    "lat": float(end_location["lat"]),
                    "lng": float(end_location["lng"]),
                },
                "distance_km": float(step["distance"]["value"]) / 1000.0,
                "duration_minutes": float(step["duration"]["value"]) / 60.0,
                "html_instructions": step.get("html_instructions"),
            }
        )

    return {
        "origin": leg.get("start_address", "Origin"),
        "destination": leg.get("end_address", "Destination"),
        "total_distance_km": total_distance_km,
        "total_duration_minutes": total_duration_minutes,
        "steps": steps,
    }


async def enrich_route_with_traffic(
    route_data: Dict[str, Any],
    db: Any,
) -> List[RouteSegment]:
    """Enrich Google route segments with local traffic data from PostgreSQL."""

    async def fetch_nearest_record(lat: float, lng: float) -> Any:
        def query() -> Any:
            return (
                db.query(TrafficRecord)
                .filter(
                    TrafficRecord.latitude.between(lat - HYDERABAD_RADIUS_DEGREES, lat + HYDERABAD_RADIUS_DEGREES),
                    TrafficRecord.longitude.between(lng - HYDERABAD_RADIUS_DEGREES, lng + HYDERABAD_RADIUS_DEGREES),
                )
                .order_by(
                    func.abs(TrafficRecord.latitude - lat) + func.abs(TrafficRecord.longitude - lng)
                )
                .first()
            )

        return await asyncio.to_thread(query)

    enriched_segments: List[RouteSegment] = []
    for step in route_data.get("steps", []):
        start = step["start_location"]
        record = await fetch_nearest_record(start["lat"], start["lng"])

        if record:
            congestion_level = record.congestion_level or "medium"
            congestion_warning = (
                f"Heavy traffic detected near {record.location} — expect delays"
                if congestion_level == "high"
                else None
            )
            duration_minutes = float(record.travel_time_mins) if record.travel_time_mins else step["duration_minutes"]
        else:
            congestion_level = "medium"
            congestion_warning = None
            duration_minutes = step["duration_minutes"]

        enriched_segments.append(
            RouteSegment(
                start_location=start,
                end_location=step["end_location"],
                distance_km=step["distance_km"],
                duration_minutes=duration_minutes,
                congestion_level=congestion_level,
                congestion_warning=congestion_warning,
            )
        )

    return enriched_segments


async def calculate_eta(distance_km: float, congestion_level: str) -> float:
    """Estimate travel time for a route based on congestion level."""
    speed_kmh = CONGESTION_SPEEDS.get(congestion_level)
    if speed_kmh is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid congestion level provided",
        )

    eta_minutes = (distance_km / speed_kmh) * 60.0
    eta_with_buffer = eta_minutes * 1.1
    return float(round(eta_with_buffer))


async def check_incidents_on_route(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    db: Any,
) -> List[str]:
    """Return active incident warnings for a route bounding box."""
    min_lat, max_lat = sorted([origin_lat, dest_lat])
    min_lng, max_lng = sorted([origin_lng, dest_lng])

    def query() -> List[Incident]:
        return (
            db.query(Incident)
            .filter(
                Incident.latitude.between(min_lat, max_lat),
                Incident.longitude.between(min_lng, max_lng),
                Incident.resolved_at.is_(None),
                Incident.is_active.is_(True),
            )
            .all()
        )

    incidents = await asyncio.to_thread(query)
    warnings: List[str] = []

    for incident in incidents:
        warnings.append(
            f"{incident.incident_type.title()} near {incident.location} — Severity: {incident.severity}"
        )

    return warnings


async def build_google_maps_url(
    origin_lat: float,
    origin_lng: float,
    dest_lat: float,
    dest_lng: float,
    mode: str,
) -> str:
    """Build a Google Maps directions deep link for the requested route."""
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={origin_lat},{origin_lng}"
        f"&destination={dest_lat},{dest_lng}"
        f"&travelmode={mode}"
    )
