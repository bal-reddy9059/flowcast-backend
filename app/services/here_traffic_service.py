"""
HERE Maps Traffic API v7 — real-time flow and incidents for India.

Free tier: 250,000 API calls/month — no credit card required.
Sign up:   https://developer.here.com
           → Create project → Generate a Freemium API key
           → Paste the key as HERE_API_KEY in .env

Flow endpoint:      https://data.traffic.hereapi.com/v7/flow
Incidents endpoint: https://data.traffic.hereapi.com/v7/incidents

Rate budget (250K/month ≈ 8,333/day):
  80 locations × 48 cycles/day = 3,840 flow calls/day — well within the limit.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_RAW_KEY = os.getenv("HERE_API_KEY", "")
_PLACEHOLDERS = {"", "your_here_api_key_here", "your_key_here", "HERE_KEY"}
HERE_API_KEY = _RAW_KEY if _RAW_KEY not in _PLACEHOLDERS else ""

if not HERE_API_KEY:
    logger.info(
        "HERE API key not configured — HERE traffic disabled. "
        "Get a free key at https://developer.here.com and set HERE_API_KEY in .env"
    )

_FLOW_URL     = "https://data.traffic.hereapi.com/v7/flow"
_INCIDENT_URL = "https://data.traffic.hereapi.com/v7/incidents"
_TIMEOUT      = 8

_key_invalid = False


def _key_ok() -> bool:
    return bool(HERE_API_KEY) and not _key_invalid


def is_available() -> bool:
    """True when a valid HERE key is configured and has not been rejected."""
    return _key_ok()


# ── Flow data ─────────────────────────────────────────────────────────────────

async def fetch_flow(lat: float, lng: float) -> Optional[dict]:
    """
    Fetch real-time traffic flow for a lat/lng point (200 m search radius).

    Returns dict with keys: currentSpeed, freeFlowSpeed, confidence, roadClosure, source
    Returns None when the key is missing/invalid or the request fails.
    """
    global _key_invalid
    if not _key_ok():
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _FLOW_URL,
                params={
                    "in":                 f"circle:{lat},{lng};r=200",
                    "locationReferencing": "shape",
                    "apikey":             HERE_API_KEY,
                },
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if not results:
                    return None

                speeds, free_flows, confidences = [], [], []
                for r in results:
                    cf = r.get("currentFlow", {})
                    if "speed" in cf and "freeFlow" in cf:
                        speeds.append(cf["speed"])
                        free_flows.append(cf["freeFlow"])
                        confidences.append(cf.get("confidence", 0.8))

                if not speeds:
                    return None

                return {
                    "currentSpeed":  round(sum(speeds)      / len(speeds),      1),
                    "freeFlowSpeed": round(sum(free_flows)  / len(free_flows),  1),
                    "confidence":    round(sum(confidences) / len(confidences), 2),
                    "roadClosure":   False,
                    "source":        "here",
                }

            if resp.status_code in (401, 403):
                _key_invalid = True
                logger.warning(
                    "HERE API key rejected (%s). Disabling HERE calls for this session — "
                    "check your key at https://developer.here.com",
                    resp.status_code,
                )
            else:
                logger.debug("HERE flow %s,%s → HTTP %s", lat, lng, resp.status_code)

    except httpx.TimeoutException:
        logger.debug("HERE flow timeout for %s,%s", lat, lng)
    except Exception as exc:
        logger.error("HERE flow error: %s", exc)

    return None


# ── Incident data ─────────────────────────────────────────────────────────────

async def fetch_incidents(
    min_lat: float, min_lng: float,
    max_lat: float, max_lng: float,
) -> list[dict]:
    """
    Fetch active road incidents inside a bounding box.
    Returns list of raw HERE incident Feature dicts (may be empty).
    """
    global _key_invalid
    if not _key_ok():
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _INCIDENT_URL,
                params={
                    "in":     f"bbox:{min_lng},{min_lat},{max_lng},{max_lat}",
                    "apikey": HERE_API_KEY,
                },
            )
            if resp.status_code == 200:
                return resp.json().get("results", [])
            if resp.status_code in (401, 403):
                _key_invalid = True
                logger.warning("HERE incidents: key rejected (%s)", resp.status_code)
            else:
                logger.debug("HERE incidents HTTP %s", resp.status_code)
    except Exception as exc:
        logger.error("HERE incidents error: %s", exc)

    return []


# ── Helpers ───────────────────────────────────────────────────────────────────

_INCIDENT_TYPE_MAP: dict[str, tuple[str, str]] = {
    "accident":         ("accident",  "moderate"),
    "congestion":       ("accident",  "minor"),
    "disabled vehicle": ("accident",  "minor"),
    "mass transit":     ("event",     "minor"),
    "miscellaneous":    ("accident",  "minor"),
    "road hazard":      ("roadwork",  "minor"),
    "road closure":     ("closure",   "severe"),
    "planned event":    ("event",     "minor"),
    "construction":     ("roadwork",  "moderate"),
}


def parse_incident(raw: dict, location_name: str) -> Optional[dict]:
    """
    Convert a HERE incident Feature to internal incident format.
    Returns None when coordinates are missing.
    """
    props = raw.get("properties", {})
    geom  = raw.get("geometry", {})

    raw_type = props.get("incidentType", "miscellaneous").lower()
    inc_type, severity = _INCIDENT_TYPE_MAP.get(raw_type, ("accident", "minor"))

    criticality = props.get("criticality", "").lower()
    if criticality == "critical":
        severity = "severe"
    elif criticality == "major":
        severity = "moderate"
    elif criticality == "minor":
        severity = "minor"

    coords = None
    geo_type = geom.get("type", "")
    if geo_type == "Point":
        coords = geom.get("coordinates")
    elif geo_type == "LineString":
        c = geom.get("coordinates", [])
        if c:
            coords = c[0]

    if not coords or len(coords) < 2:
        return None

    desc = (
        props.get("description", {}).get("value")
        or props.get("summary")
        or "Traffic incident"
    )

    return {
        "location":      props.get("summary", location_name)[:255],
        "latitude":      float(coords[1]),
        "longitude":     float(coords[0]),
        "incident_type": inc_type,
        "severity":      severity,
        "description":   str(desc),
    }
