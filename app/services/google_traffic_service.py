"""
Google Directions API helper for real-time district-level traffic.

Uses departure_time=now to get duration_in_traffic vs duration,
which gives the actual congestion ratio on any road segment.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_API_KEY = os.getenv("GOOGLE_MAPS_DIRECTIONS_API_KEY", "")
_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
_TIMEOUT = 10.0


async def fetch_district_traffic(
    lat: float, lng: float, dest_lat: float, dest_lng: float
) -> Optional[dict]:
    """
    Call Google Directions API with departure_time=now.
    Returns a dict with speed_kmh, congestion_level, duration_s,
    duration_in_traffic_s, distance_m — or None on failure.
    """
    if not _API_KEY:
        return None

    params = {
        "origin": f"{lat},{lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "departure_time": "now",
        "traffic_model": "best_guess",
        "key": _API_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_DIRECTIONS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.debug("Google Directions API error: %s", exc)
        return None

    if data.get("status") != "OK":
        logger.debug("Directions API status=%s", data.get("status"))
        return None

    try:
        leg = data["routes"][0]["legs"][0]
        duration_s = leg["duration"]["value"]
        duration_traffic_s = leg.get("duration_in_traffic", {}).get("value", duration_s)
        distance_m = leg["distance"]["value"]

        if duration_traffic_s <= 0 or distance_m <= 0:
            return None

        # speed = distance / travel_time
        speed_kmh = round((distance_m / duration_traffic_s) * 3.6, 1)

        # congestion ratio: how much slower than free-flow
        ratio = duration_traffic_s / max(duration_s, 1)
        if ratio < 1.15:
            congestion = "low"
        elif ratio < 1.50:
            congestion = "medium"
        else:
            congestion = "high"

        return {
            "speed_kmh": speed_kmh,
            "congestion_level": congestion,
            "duration_s": duration_s,
            "duration_in_traffic_s": duration_traffic_s,
            "distance_m": distance_m,
            "congestion_ratio": round(ratio, 3),
        }
    except (KeyError, IndexError, ZeroDivisionError) as exc:
        logger.debug("Parse error: %s", exc)
        return None
