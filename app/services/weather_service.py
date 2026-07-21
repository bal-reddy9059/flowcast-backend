"""
Weather-traffic correlation service.

Fetches live weather from OpenWeatherMap every 30 minutes for 20 major Indian cities.
Each city gets a stable `city_id` (UUID5 based on name) and each snapshot gets a
unique `snapshot_id` (UUID4 per fetch).

Environment variable:
  OPENWEATHERMAP_API_KEY — free tier sufficient. Falls back to deterministic
                           simulation when absent.
"""

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.utils.http_client import get_http_client

logger = logging.getLogger(__name__)

_OWM_KEY  = os.getenv("OPENWEATHERMAP_API_KEY", "")
_OWM_BASE = "https://api.openweathermap.org/data/2.5/weather"

# Stable namespace for city UUIDs
_CITY_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_DNS

_MONITORED_CITIES: list[dict] = [
    {"city": "Hyderabad",     "lat": 17.3850, "lng": 78.4867},
    {"city": "Bangalore",     "lat": 12.9716, "lng": 77.5946},
    {"city": "Mumbai",        "lat": 19.0760, "lng": 72.8777},
    {"city": "Delhi",         "lat": 28.7041, "lng": 77.1025},
    {"city": "Chennai",       "lat": 13.0827, "lng": 80.2707},
    {"city": "Kolkata",       "lat": 22.5726, "lng": 88.3639},
    {"city": "Pune",          "lat": 18.5204, "lng": 73.8567},
    {"city": "Ahmedabad",     "lat": 23.0225, "lng": 72.5714},
    {"city": "Jaipur",        "lat": 26.9124, "lng": 75.7873},
    {"city": "Surat",         "lat": 21.1702, "lng": 72.8311},
    {"city": "Lucknow",       "lat": 26.8467, "lng": 80.9462},
    {"city": "Bhopal",        "lat": 23.2599, "lng": 77.4126},
    {"city": "Nagpur",        "lat": 21.1458, "lng": 79.0882},
    {"city": "Kochi",         "lat":  9.9312, "lng": 76.2673},
    {"city": "Chandigarh",    "lat": 30.7333, "lng": 76.7794},
    {"city": "Indore",        "lat": 22.7196, "lng": 75.8577},
    {"city": "Visakhapatnam", "lat": 17.6868, "lng": 83.2185},
    {"city": "Patna",         "lat": 25.5941, "lng": 85.1376},
    {"city": "Coimbatore",    "lat": 11.0168, "lng": 76.9558},
    {"city": "Vadodara",      "lat": 22.3072, "lng": 73.1812},
]

# Stable lat/lng lookup per city name
_CITY_COORDS: dict[str, tuple[float, float]] = {
    c["city"]: (c["lat"], c["lng"]) for c in _MONITORED_CITIES
}

# In-memory cache: city → snapshot dict
_weather_cache: dict[str, dict] = {}
_last_fetch: Optional[datetime] = None

# Area / neighbourhood → monitored city (for /weather/impact)
_AREA_TO_CITY: dict[str, str] = {
    # Hyderabad
    "gachibowli": "Hyderabad", "hitech city": "Hyderabad", "madhapur": "Hyderabad",
    "banjara hills": "Hyderabad", "jubilee hills": "Hyderabad", "kondapur": "Hyderabad",
    "kukatpally": "Hyderabad", "lb nagar": "Hyderabad", "secunderabad": "Hyderabad",
    "ameerpet": "Hyderabad", "kphb": "Hyderabad", "mehdipatnam": "Hyderabad",
    "begumpet": "Hyderabad", "dilsukhnagar": "Hyderabad", "miyapur": "Hyderabad",
    "cyber towers": "Hyderabad", "hitec city": "Hyderabad",
    # Bangalore
    "koramangala": "Bangalore", "indiranagar": "Bangalore", "whitefield": "Bangalore",
    "electronic city": "Bangalore", "silk board": "Bangalore", "hebbal": "Bangalore",
    "mg road": "Bangalore", "bengaluru": "Bangalore",
    # Mumbai
    "dadar": "Mumbai", "worli": "Mumbai", "powai": "Mumbai", "thane": "Mumbai",
    "bandra": "Mumbai", "andheri": "Mumbai", "marine drive": "Mumbai", "bkc": "Mumbai",
    # Delhi NCR
    "connaught place": "Delhi", "lajpat nagar": "Delhi", "rohini": "Delhi",
    "dwarka": "Delhi", "noida": "Delhi", "gurgaon": "Delhi", "gurugram": "Delhi",
    "faridabad": "Delhi", "cyber city": "Delhi",
    # Chennai
    "anna nagar": "Chennai", "anna salai": "Chennai", "t nagar": "Chennai",
    "tambaram": "Chennai", "guindy": "Chennai", "omr": "Chennai",
    # Kolkata
    "howrah": "Kolkata", "park street": "Kolkata", "salt lake": "Kolkata", "dum dum": "Kolkata",
}


# ── UUID helpers ───────────────────────────────────────────────────────────────

def city_uuid(city: str) -> str:
    """Return a stable UUID5 for a city — same value every time for the same city name."""
    return str(uuid.uuid5(_CITY_NS, f"flowcast.city.{city.lower()}"))


def snapshot_uuid() -> str:
    """Return a fresh UUID4 for one weather fetch snapshot."""
    return str(uuid.uuid4())


# ── Impact classification ──────────────────────────────────────────────────────

def _compute_modifier(condition: str, rain_mm: float, wind_kmh: float,
                      visibility_km: float) -> str:
    cond = condition.lower() if condition else ""
    if "thunderstorm" in cond:                          return "severe"
    if rain_mm >= 10 or "heavy rain" in cond:           return "severe"
    if rain_mm >= 5 or "rain" in cond or "drizzle" in cond: return "moderate"
    if "fog" in cond or "haze" in cond or (visibility_km and visibility_km < 1.0):
        return "moderate"
    if "dust" in cond or "sand" in cond or "smoke" in cond: return "moderate"
    if wind_kmh and wind_kmh > 50:                      return "light"
    return "none"


def _alert_level(modifier: str) -> str:
    return {"none": "normal", "light": "minor", "moderate": "caution",
            "severe": "danger"}.get(modifier, "normal")


def modifier_to_congestion_bump(modifier: str) -> int:
    return {"none": 0, "light": 0, "moderate": 1, "severe": 2}.get(modifier, 0)


def _modifier_advice(modifier: str, condition: str) -> str:
    return {
        "none":     f"Weather is clear ({condition}). Normal traffic expected.",
        "light":    f"Light weather impact ({condition}). Minor slowdowns possible.",
        "moderate": f"Moderate weather impact ({condition}). Expect 20–40% longer travel times.",
        "severe":   f"Severe weather ({condition}). Significant delays — consider delaying travel.",
    }.get(modifier, "")


# ── OWM fetch ─────────────────────────────────────────────────────────────────

async def fetch_city_weather(city_meta: dict) -> Optional[dict]:
    city = city_meta["city"]
    if not _OWM_KEY:
        return _simulated_weather(city_meta)

    params = {"lat": city_meta["lat"], "lon": city_meta["lng"],
              "appid": _OWM_KEY, "units": "metric"}
    try:
        resp = await get_http_client().get(_OWM_BASE, params=params)
        resp.raise_for_status()
        data = resp.json()

        rain_mm  = data.get("rain", {}).get("1h", 0.0)
        wind_kmh = round(data.get("wind", {}).get("speed", 0.0) * 3.6, 1)
        vis_km   = round(data.get("visibility", 10000) / 1000, 2)
        cond     = data["weather"][0]["main"] if data.get("weather") else "Clear"
        desc     = data["weather"][0].get("description", cond.lower()) if data.get("weather") else ""
        modifier = _compute_modifier(cond, rain_mm, wind_kmh, vis_km)

        return _build_snapshot(
            city=city,
            country=data.get("sys", {}).get("country", "IN"),
            condition=cond,
            description=desc,
            temp_c=round(data["main"]["temp"], 1),
            feels_like_c=round(data["main"].get("feels_like", data["main"]["temp"]), 1),
            humidity=data["main"]["humidity"],
            wind_kmh=wind_kmh,
            rain_mm_1h=rain_mm,
            visibility_km=vis_km,
            modifier=modifier,
            lat=city_meta["lat"],
            lng=city_meta["lng"],
            source="openweathermap",
        )
    except Exception as exc:
        logger.warning("OWM fetch failed for %s: %s", city, exc)
        return _simulated_weather(city_meta)


def _simulated_weather(city_meta: dict) -> dict:
    """Deterministic simulated weather keyed on city + current hour."""
    city = city_meta["city"]
    h = int(hashlib.md5(f"{city}{datetime.now().hour}".encode()).hexdigest(), 16)
    conditions = ["Clear", "Partly Cloudy", "Rain", "Haze", "Clear", "Clear"]
    cond = conditions[h % len(conditions)]

    # Fix: ensure rain_mm_1h is non-zero when condition is Rain
    if cond == "Rain":
        rain = round(1.0 + (h % 8) * 0.5, 1)   # 1.0–4.5 mm/h range
    else:
        rain = 0.0

    vis      = 2.0 if cond in ("Haze", "Fog") else 10.0
    wind_kmh = round(5.0 + (h % 20), 1)
    temp_c   = round(22.0 + (h % 14), 1)
    modifier = _compute_modifier(cond, rain, wind_kmh, vis)

    return _build_snapshot(
        city=city,
        country="IN",
        condition=cond,
        description=cond.lower(),
        temp_c=temp_c,
        feels_like_c=round(temp_c + (1 if cond in ("Haze", "Rain") else -1), 1),
        humidity=50 + (h % 40),
        wind_kmh=wind_kmh,
        rain_mm_1h=rain,
        visibility_km=vis,
        modifier=modifier,
        lat=city_meta["lat"],
        lng=city_meta["lng"],
        source="simulated",
    )


def _build_snapshot(*, city: str, country: str, condition: str, description: str,
                    temp_c: float, feels_like_c: float, humidity: int,
                    wind_kmh: float, rain_mm_1h: float, visibility_km: float,
                    modifier: str, lat: float, lng: float, source: str) -> dict:
    """Assemble a full weather snapshot dict with all IDs and fields."""
    return {
        # ── Identity ────────────────────────────────────────────────────────
        "city_id":     city_uuid(city),          # stable UUID5 — same every time
        "snapshot_id": snapshot_uuid(),           # unique UUID4 per fetch
        # ── Location ────────────────────────────────────────────────────────
        "city":        city,
        "country":     country,
        "lat":         lat,
        "lng":         lng,
        # ── Conditions ──────────────────────────────────────────────────────
        "condition":   condition,
        "description": description,
        "temp_c":      temp_c,
        "feels_like_c": feels_like_c,
        "humidity":    humidity,
        "wind_kmh":    wind_kmh,
        "rain_mm_1h":  rain_mm_1h,
        "visibility_km": visibility_km,
        # ── Traffic impact ──────────────────────────────────────────────────
        "congestion_modifier":   modifier,
        "alert_level":           _alert_level(modifier),
        "congestion_bump_levels": modifier_to_congestion_bump(modifier),
        "impact_advice":         _modifier_advice(modifier, condition),
        # ── Meta ────────────────────────────────────────────────────────────
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
        "source":      source,
    }


# ── Background refresh ─────────────────────────────────────────────────────────

def _persist_snapshots(results: list[dict]) -> None:
    """Persist a refresh batch without blocking the asyncio event loop."""
    from app.database import SessionLocal
    from app.models.weather import WeatherSnapshot

    db = SessionLocal()
    try:
        db.add_all([
            WeatherSnapshot(
                city=s["city"],
                condition=s["condition"],
                temp_c=s["temp_c"],
                humidity=s["humidity"],
                wind_kmh=s["wind_kmh"],
                rain_mm_1h=s["rain_mm_1h"],
                visibility_km=s["visibility_km"],
                congestion_modifier=s["congestion_modifier"],
            )
            for s in results
        ])
        db.commit()
    except Exception as exc:
        logger.error("Weather DB persist error: %s", exc)
        db.rollback()
    finally:
        db.close()


async def refresh_all_cities() -> list[dict]:
    fetched = await asyncio.gather(
        *(fetch_city_weather(city_meta) for city_meta in _MONITORED_CITIES),
        return_exceptions=True,
    )
    results = []
    for city_meta, result in zip(_MONITORED_CITIES, fetched):
        if isinstance(result, Exception):
            logger.warning("Weather refresh failed for %s: %s", city_meta["city"], result)
            continue
        if result:
            results.append(result)

    if results:
        _weather_cache.update({snapshot["city"]: snapshot for snapshot in results})
        await asyncio.to_thread(_persist_snapshots, results)

    global _last_fetch
    _last_fetch = datetime.now(timezone.utc)
    logger.info("Weather refresh: %d cities updated", len(results))
    return results


# ── Public accessors ──────────────────────────────────────────────────────────

def ensure_weather_cache() -> list[dict]:
    """Guarantee the in-memory cache is populated (sync simulation seed).

    The background collector may start ~45s after boot and clears on reload.
    Endpoints must never return empty/404 for a known city while waiting.
    """
    global _last_fetch
    if _weather_cache:
        return list(_weather_cache.values())

    for city_meta in _MONITORED_CITIES:
        snap = _simulated_weather(city_meta)
        _weather_cache[snap["city"]] = snap
    _last_fetch = datetime.now(timezone.utc)
    logger.info("Weather cache seeded with %d simulated snapshots", len(_weather_cache))
    return list(_weather_cache.values())


def resolve_location_city(location: str) -> Optional[str]:
    """Map a free-text traffic location to a monitored city name."""
    loc = location.lower().strip()
    if not loc:
        return None

    # Direct city name match
    for c in _MONITORED_CITIES:
        name = c["city"].lower()
        if name in loc or loc in name:
            return c["city"]
    if "bengaluru" in loc:
        return "Bangalore"

    # Longest area alias match
    best_city = None
    best_len = 0
    for alias, city in _AREA_TO_CITY.items():
        if alias in loc and len(alias) > best_len:
            best_len = len(alias)
            best_city = city
    return best_city


def get_city_id_map() -> dict[str, str]:
    """Return {city_id: city_name} for all monitored cities."""
    return {city_uuid(c["city"]): c["city"] for c in _MONITORED_CITIES}


def get_cached_weather(city: str) -> Optional[dict]:
    """Lookup by city name (case-insensitive). Seeds cache if empty."""
    ensure_weather_cache()
    for k, v in _weather_cache.items():
        if k.lower() == city.lower():
            return v
    return None


def get_cached_weather_by_id(city_id: str) -> Optional[dict]:
    """Lookup by stable city_id UUID. Seeds cache if empty."""
    ensure_weather_cache()
    for snap in _weather_cache.values():
        if snap.get("city_id") == city_id:
            return snap
    id_map = get_city_id_map()
    city_name = id_map.get(city_id)
    if city_name:
        return get_cached_weather(city_name)
    return None


def get_all_cached() -> list[dict]:
    return ensure_weather_cache()


def get_monitored_cities() -> list[str]:
    return [c["city"] for c in _MONITORED_CITIES]


def get_cache_meta() -> dict:
    ensure_weather_cache()
    return {
        "cities_cached": len(_weather_cache),
        "last_fetch_at": _last_fetch.isoformat() if _last_fetch else None,
        "owm_configured": bool(_OWM_KEY),
        "data_source": "openweathermap" if _OWM_KEY else "simulated",
    }


def weather_impact_for_location(location: str) -> dict:
    ensure_weather_cache()
    city_name = resolve_location_city(location)

    snap = get_cached_weather(city_name) if city_name else None
    if snap is None:
        # Last resort: scan cache for substring (already tried resolve)
        loc = location.lower()
        for name, cached in _weather_cache.items():
            if name.lower() in loc or loc in name.lower():
                snap = cached
                city_name = name
                break

    if snap is None:
        return {
            "city_id":             None,
            "city":                None,
            "condition":           "Unknown",
            "congestion_modifier": "none",
            "alert_level":         "normal",
            "rain_mm_1h":          0,
            "visibility_km":       10,
            "impact_advice":       "No weather data available for this location.",
        }

    return {
        "city_id":             snap.get("city_id"),
        "city":                city_name or snap.get("city"),
        "condition":           snap["condition"],
        "congestion_modifier": snap["congestion_modifier"],
        "alert_level":         snap.get("alert_level", "normal"),
        "rain_mm_1h":          snap.get("rain_mm_1h", 0),
        "visibility_km":       snap.get("visibility_km", 10),
        "impact_advice":       snap.get("impact_advice", ""),
    }
