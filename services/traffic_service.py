from __future__ import annotations
import asyncio
import json
import os
import random
from datetime import datetime
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from dotenv import load_dotenv

load_dotenv()

GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")

# Sample Indian city locations used for dummy / fallback data
_SAMPLE_LOCATIONS = [
    {"name": "MG Road, Bangalore", "lat": 12.9756, "lon": 77.6097},
    {"name": "Connaught Place, Delhi", "lat": 28.6315, "lon": 77.2167},
    {"name": "Marine Drive, Mumbai", "lat": 18.9437, "lon": 72.8237},
    {"name": "Park Street, Kolkata", "lat": 22.5514, "lon": 88.3512},
    {"name": "Anna Salai, Chennai", "lat": 13.0569, "lon": 80.2425},
]


def _congestion_from_ratio(ratio: float) -> str:
    if ratio < 1.2:
        return "low"
    elif ratio < 1.5:
        return "moderate"
    elif ratio < 2.0:
        return "high"
    return "very_high"


def get_dummy_traffic() -> list[dict]:
    records = []
    for loc in _SAMPLE_LOCATIONS:
        ratio = random.uniform(1.0, 2.6)
        records.append(
            {
                "location": loc["name"],
                "latitude": loc["lat"],
                "longitude": loc["lon"],
                "congestion_level": _congestion_from_ratio(ratio),
                "speed_kmh": round(random.uniform(8.0, 65.0), 1),
                "travel_time_mins": round(random.uniform(4.0, 50.0), 1),
                "timestamp": datetime.utcnow(),
            }
        )
    return records


async def _fetch_google_maps(origin: str, destination: str) -> dict | None:
    """
    Calls Google Maps Distance Matrix API with live traffic.
    Returns a single traffic dict or None on failure / missing key.
    """
    if not GOOGLE_MAPS_API_KEY:
        return None

    params = urlencode(
        {
            "origins": origin,
            "destinations": destination,
            "departure_time": "now",
            "traffic_model": "best_guess",
            "key": GOOGLE_MAPS_API_KEY,
        }
    )
    url = f"https://maps.googleapis.com/maps/api/distancematrix/json?{params}"

    def _blocking_fetch() -> dict:
        with urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode())

    try:
        data = await asyncio.to_thread(_blocking_fetch)
    except (URLError, OSError, json.JSONDecodeError):
        return None

    if data.get("status") != "OK":
        return None

    try:
        element = data["rows"][0]["elements"][0]
    except (KeyError, IndexError):
        return None

    if element.get("status") != "OK":
        return None

    duration_s: int = element["duration"]["value"]
    duration_traffic_s: int = element.get("duration_in_traffic", {}).get("value", duration_s)
    distance_m: int = element["distance"]["value"]

    ratio = duration_traffic_s / duration_s if duration_s > 0 else 1.0
    speed_kmh = round((distance_m / duration_traffic_s) * 3.6, 1) if duration_traffic_s > 0 else 0.0

    return {
        "location": f"{origin} → {destination}",
        "latitude": 0.0,
        "longitude": 0.0,
        "congestion_level": _congestion_from_ratio(ratio),
        "speed_kmh": speed_kmh,
        "travel_time_mins": round(duration_traffic_s / 60, 1),
        "timestamp": datetime.utcnow(),
    }


async def get_traffic_data(
    origin: str | None = None,
    destination: str | None = None,
) -> tuple[list[dict], str]:
    """
    Returns (traffic_list, source) where source is 'google_maps' or 'dummy'.
    Falls back to dummy data when no API key is set or the Maps call fails.
    """
    if origin and destination:
        result = await _fetch_google_maps(origin, destination)
        if result:
            return [result], "google_maps"

    return get_dummy_traffic(), "dummy"
