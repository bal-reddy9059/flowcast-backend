"""
TomTom Traffic API integration — real-time flow and incidents for India.

Free tier: 2,500 requests/day  →  collector batches 80 locations every 30 min
Sign up:   https://developer.tomtom.com  (free, no credit card needed)
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_RAW_KEY  = os.getenv("TOMTOM_API_KEY", "")
# Treat placeholder values as "no key"
_PLACEHOLDERS = {"", "your_tomtom_key_here", "your_key_here", "TOMTOM_KEY"}
TOMTOM_API_KEY = _RAW_KEY if _RAW_KEY not in _PLACEHOLDERS else ""

if not TOMTOM_API_KEY:
    logger.info("TomTom API key not configured — collector will use simulation fallback")

_FLOW_URL      = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
_INCIDENT_URL  = "https://api.tomtom.com/traffic/services/5/incidentDetails"
_TIMEOUT       = 8  # seconds per request

# Set to True after a 401 so we stop wasting calls for the rest of the process lifetime
_key_invalid = False


def _key_ok() -> bool:
    return bool(TOMTOM_API_KEY) and not _key_invalid


# ── Flow data ─────────────────────────────────────────────────────────────────

async def fetch_flow(lat: float, lng: float) -> Optional[dict]:
    """
    Fetch real-time traffic flow for a single lat/lng point.

    Returns dict with keys: currentSpeed, freeFlowSpeed, confidence, roadClosure
    Returns None when API key is missing/invalid or request fails.
    """
    global _key_invalid
    if not _key_ok():
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _FLOW_URL,
                params={
                    "point": f"{lat},{lng}",
                    "unit": "KMPH",
                    "key": TOMTOM_API_KEY,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("flowSegmentData")
            if resp.status_code == 401:
                _key_invalid = True
                logger.warning(
                    "TomTom API key is invalid (401). Disabling TomTom calls — "
                    "using simulation fallback. Add a valid key to .env to enable real data."
                )
            else:
                logger.debug("TomTom flow %s,%s → HTTP %s", lat, lng, resp.status_code)
    except httpx.TimeoutException:
        logger.debug("TomTom flow timeout for %s,%s", lat, lng)
    except Exception as exc:
        logger.error("TomTom flow error: %s", exc)
    return None


# ── Incident data ─────────────────────────────────────────────────────────────

async def fetch_incidents(min_lat: float, min_lng: float,
                          max_lat: float, max_lng: float) -> list[dict]:
    """
    Fetch active road incidents in a bounding box.
    Returns list of TomTom incident objects (may be empty).
    """
    global _key_invalid
    if not _key_ok():
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _INCIDENT_URL,
                params={
                    "bbox": f"{min_lng},{min_lat},{max_lng},{max_lat}",
                    "key": TOMTOM_API_KEY,
                    "language": "en-GB",
                    "categoryFilter": "0,1,2,3,4,5,6,7,8,9,10,11",
                    "timeValidityFilter": "present",
                    "fields": (
                        "{incidents{type,geometry{coordinates},"
                        "properties{iconCategory,magnitudeOfDelay,"
                        "events{description},from,to,roadNumbers}}}"
                    ),
                },
            )
            if resp.status_code == 200:
                return resp.json().get("incidents", [])
            if resp.status_code == 401:
                _key_invalid = True
                logger.warning("TomTom incidents: API key invalid (401), disabling TomTom calls")
            else:
                logger.debug("TomTom incidents HTTP %s", resp.status_code)
    except Exception as exc:
        logger.error("TomTom incidents error: %s", exc)
    return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def classify_congestion(current_speed: float, free_flow_speed: float) -> str:
    """Convert speed ratio to low / medium / high."""
    if free_flow_speed <= 0:
        return "medium"
    ratio = current_speed / free_flow_speed
    if ratio >= 0.75:
        return "low"
    if ratio >= 0.45:
        return "medium"
    return "high"


def estimate_vehicle_count(current_speed: float, free_flow_speed: float) -> int:
    """Estimate vehicle density from the speed ratio (50–1 200 range)."""
    if free_flow_speed <= 0:
        return 300
    ratio = current_speed / free_flow_speed
    return max(50, int(50 + (1.0 - ratio) * 1150))


def parse_incident_type(category: int) -> tuple[str, str]:
    """Map TomTom iconCategory to (incident_type, severity)."""
    mapping = {
        0:  ("accident",  "minor"),
        1:  ("accident",  "moderate"),
        2:  ("accident",  "severe"),
        3:  ("roadwork",  "minor"),
        4:  ("roadwork",  "moderate"),
        5:  ("closure",   "severe"),
        6:  ("event",     "minor"),
        7:  ("event",     "moderate"),
        8:  ("roadwork",  "severe"),
        9:  ("accident",  "moderate"),
        10: ("closure",   "moderate"),
        11: ("closure",   "severe"),
    }
    return mapping.get(category, ("accident", "minor"))
