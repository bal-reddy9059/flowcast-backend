"""
Weather-Traffic Correlation endpoints.

GET /weather/cities              — live weather for all 20 monitored cities (with city_id)
GET /weather/city/{city_id}      — single city by stable UUID (use city_id from /cities list)
GET /weather/city-ids            — directory: city_id → city_name for all monitored cities
GET /weather/impact?location=X   — congestion impact for any traffic location string
GET /weather/status              — cache freshness + OWM configuration status
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app.services.weather_service import (
    get_all_cached,
    get_cached_weather_by_id,
    get_city_id_map,
    get_monitored_cities,
    weather_impact_for_location,
    city_uuid,
)

router = APIRouter(prefix="/weather", tags=["Weather & Traffic Impact"])
logger = logging.getLogger(__name__)

_OWM_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")

_MODIFIER_LABEL = {
    "none":     "No weather impact — normal driving conditions",
    "light":    "Light impact — minor slowdowns possible",
    "moderate": "Moderate impact — expect 20–40% longer travel times",
    "severe":   "Severe impact — major delays, consider postponing travel",
}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/cities",
    status_code=status.HTTP_200_OK,
    summary="Live weather for all 20 monitored Indian cities",
)
def get_all_cities_weather() -> dict:
    """
    Live weather snapshot for every monitored city, sorted by congestion severity.

    Each city entry includes:
    - `city_id` — stable UUID (use this to call `GET /weather/city/{city_id}`)
    - `snapshot_id` — unique UUID for this refresh cycle
    - `alert_level` — `normal` / `minor` / `caution` / `danger`
    - `congestion_bump_levels` — how many congestion tiers to add to ML predictions

    Cache is refreshed every 30 minutes.
    """
    snapshots = get_all_cached()
    if not snapshots:
        return {
            "message": "Weather cache is warming up — try again in 30 seconds.",
            "cities": [],
            "total": 0,
        }

    _order = {"severe": 0, "moderate": 1, "light": 2, "none": 3}
    snapshots.sort(key=lambda s: _order.get(s.get("congestion_modifier", "none"), 3))

    none_count     = sum(1 for s in snapshots if s.get("congestion_modifier") == "none")
    light_count    = sum(1 for s in snapshots if s.get("congestion_modifier") == "light")
    moderate_count = sum(1 for s in snapshots if s.get("congestion_modifier") == "moderate")
    severe_count   = sum(1 for s in snapshots if s.get("congestion_modifier") == "severe")

    return {
        "total":           len(snapshots),
        "severe_impact":   severe_count,
        "moderate_impact": moderate_count,
        "light_impact":    light_count,
        "clear_cities":    none_count,
        "network_alert": (
            "danger"  if severe_count   >  0 else
            "caution" if moderate_count >= 5 else
            "minor"   if moderate_count >  0 else
            "normal"
        ),
        "cities":        snapshots,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "tip": "Use city_id from each entry to call GET /weather/city/{city_id}",
    }


@router.get(
    "/city-ids",
    status_code=status.HTTP_200_OK,
    summary="Directory of all city_id → city_name mappings",
)
def get_city_id_directory() -> dict:
    """
    Returns the stable `city_id` UUID for every monitored city.

    `city_id` values are deterministic (UUID5 based on the city name) and will
    never change — safe to store in your frontend or database as a stable key.

    Use `GET /weather/city/{city_id}` to fetch live weather for any entry.
    """
    id_map = get_city_id_map()
    entries = [
        {
            "city_id":   cid,
            "city_name": name,
            "endpoint":  f"/api/v1/weather/city/{cid}",
        }
        for cid, name in sorted(id_map.items(), key=lambda x: x[1])
    ]
    return {
        "total":   len(entries),
        "cities":  entries,
        "usage":   "Pass city_id as path parameter: GET /api/v1/weather/city/{city_id}",
    }


@router.get(
    "/city/{city_id}",
    status_code=status.HTTP_200_OK,
    summary="Weather + congestion impact for a single city (by city_id UUID)",
)
def get_city_weather(city_id: uuid.UUID) -> dict:
    """
    Fetch the latest weather snapshot for one city using its **stable `city_id` UUID**.

    Get the `city_id` from:
    - `GET /weather/cities` — each city object contains `city_id`
    - `GET /weather/city-ids` — full directory of city_id → city_name

    Returns the full snapshot plus `modifier_label` and `tips` for your UI.
    """
    snap = get_cached_weather_by_id(str(city_id))

    if snap is None:
        # Build a helpful error with the full city-id directory
        id_map = get_city_id_map()
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"No weather data found for city_id '{city_id}'.",
                "hint":    "Use GET /weather/city-ids to look up the correct city_id.",
                "available_cities": [
                    {"city_id": cid, "city_name": name}
                    for cid, name in sorted(id_map.items(), key=lambda x: x[1])
                ],
            },
        )

    modifier = snap.get("congestion_modifier", "none")
    return {
        **snap,
        "modifier_label": _MODIFIER_LABEL.get(modifier, ""),
        "tips":           _travel_tips(modifier, snap.get("condition", "")),
    }


@router.get(
    "/impact",
    status_code=status.HTTP_200_OK,
    summary="Weather impact for any traffic monitoring location",
)
def get_location_weather_impact(
    location: str = Query(
        ..., min_length=2,
        description="Traffic location name, e.g. 'Gachibowli' or 'Silk Board Junction'",
    ),
) -> dict:
    """
    Map any traffic location string to the nearest city's weather snapshot and
    return the congestion impact modifier.

    Used by the ML prediction service to adjust ETA and forecasts when weather
    degrades road conditions.
    """
    impact   = weather_impact_for_location(location)
    modifier = impact.get("congestion_modifier", "none")
    return {
        "location":              location,
        **impact,
        "modifier_label":        _MODIFIER_LABEL.get(modifier, ""),
        "congestion_bump_levels": {"none": 0, "light": 0, "moderate": 1, "severe": 2}.get(modifier, 0),
        "tips":                  _travel_tips(modifier, impact.get("condition", "")),
    }


@router.get(
    "/status",
    status_code=status.HTTP_200_OK,
    summary="Weather service cache status and OWM configuration",
)
def get_weather_status() -> dict:
    """Shows cache freshness, data source, and OpenWeatherMap configuration."""
    cached = get_all_cached()
    id_map = get_city_id_map()
    return {
        "owm_configured":          bool(_OWM_KEY),
        "data_source":             "openweathermap" if _OWM_KEY else "simulated",
        "cities_cached":           len(cached),
        "cities_monitored":        len(get_monitored_cities()),
        "refresh_interval_minutes": 30,
        "city_id_directory_url":   "/api/v1/weather/city-ids",
        "sample_city_id":          list(id_map.keys())[0] if id_map else None,
        "sample_city_url":         f"/api/v1/weather/city/{list(id_map.keys())[0]}" if id_map else None,
    }


# ── Travel tips helper ────────────────────────────────────────────────────────

def _travel_tips(modifier: str, condition: str) -> list[str]:
    tips: list[str] = []
    cond = condition.lower()

    if modifier == "severe":
        tips += [
            "Delay non-essential travel if possible.",
            "Keep emergency kit in vehicle.",
            "Follow traffic police instructions.",
        ]
    if modifier in ("severe", "moderate"):
        tips += [
            "Allow extra 30–60 minutes for your journey.",
            "Use headlights even during the day.",
            "Maintain safe following distance.",
        ]
    if "rain" in cond or "drizzle" in cond:
        tips.append("Roads may be slippery — reduce speed on curves and bridges.")
    if "fog" in cond or "haze" in cond:
        tips.append("Use fog lights and stay in lane — overtaking is dangerous.")
    if "thunderstorm" in cond:
        tips.append("Avoid underpasses and waterlogged roads.")

    return tips if tips else ["Conditions are good for travel."]
