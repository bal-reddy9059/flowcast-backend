"""Shared incident seed data and idempotent seeding helper for FlowCast."""

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.predictor import Incident

_INCIDENT_SEEDS: dict[str, list[dict]] = {
    "bangalore": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Road widening work near Silk Board Junction — expect 15–20 min delays"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Minor fender-bender on MG Road near Trinity Circle, one lane blocked"},
        {"incident_type": "closure",   "severity": "moderate", "description": "Whitefield main road partially closed for metro pillar construction"},
        {"incident_type": "event",     "severity": "minor",    "description": "Cultural event at Koramangala 5th Block causing parking overflow onto main road"},
        {"incident_type": "accident",  "severity": "moderate", "description": "Multi-vehicle collision at Hebbal flyover — cleared by traffic police", "resolved_hours_ago": 3},
        {"incident_type": "roadwork",  "severity": "minor",    "description": "Pothole patching on Electronic City Phase 1 — completed and reopened", "resolved_hours_ago": 1},
    ],
    "hyderabad": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "GHMC pothole repair on Hitech City main road — right lane closed"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Two-wheeler collision near Gachibowli flyover, partially cleared"},
        {"incident_type": "closure",   "severity": "severe",   "description": "Ameerpet underpass flooded — full closure, use LB Nagar diversion"},
        {"incident_type": "event",     "severity": "minor",    "description": "IT corridor flash mob at Begumpet — dispersed, traffic restored", "resolved_hours_ago": 2},
        {"incident_type": "accident",  "severity": "moderate", "description": "Bus breakdown on LB Nagar flyover — towed, lanes reopened", "resolved_hours_ago": 1},
    ],
    "mumbai": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Metro line 3 utility shifting on LBS Road — one lane closed"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Vehicle breakdown on Worli Sea Link blocking slow lane"},
        {"incident_type": "closure",   "severity": "severe",   "description": "Bandra-Kurla flooding during heavy rain — access restored", "resolved_hours_ago": 4},
    ],
    "delhi": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Delhi PWD road repair at Connaught Place inner circle — lane restriction"},
        {"incident_type": "event",     "severity": "minor",    "description": "VIP movement on Rajpath causing signal holds on Janpath"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Auto-rickshaw collision at Lajpat Nagar crossroads — cleared", "resolved_hours_ago": 2},
    ],
    "chennai": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Storm drain work on Anna Salai — left lane closed near Gemini flyover"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Lorry breakdown on OMR Road — moved to shoulder, traffic flowing", "resolved_hours_ago": 1},
    ],
    "pune": [
        {"incident_type": "accident",  "severity": "moderate", "description": "Two-vehicle collision on Pune-Solapur highway, police on scene"},
        {"incident_type": "roadwork",  "severity": "minor",    "description": "Pothole repair on Baner road — completed", "resolved_hours_ago": 2},
    ],
    "kolkata": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Footpath repair on Park Street causing footpath-to-road overflow"},
        {"incident_type": "event",     "severity": "minor",    "description": "Cultural procession near Howrah Bridge causing intermittent lane closures"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Auto-rickshaw collision near Salt Lake Sector V — cleared", "resolved_hours_ago": 1},
    ],
    "ahmedabad": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "BRTS expansion work on SG Highway — two lanes closed"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Minor collision on CG Road near Navrangpura — vehicles moved to side"},
    ],
    "lucknow": [
        {"incident_type": "event",     "severity": "moderate", "description": "Government rally near Hazratganj causing road closures till evening"},
        {"incident_type": "roadwork",  "severity": "minor",    "description": "Gomti Nagar flyover ramp repair — expect 10 min delays"},
    ],
    "kochi": [
        {"incident_type": "closure",   "severity": "moderate", "description": "Edapally Junction underpass waterlogged — use alternate NH-66 route"},
        {"incident_type": "roadwork",  "severity": "minor",    "description": "Kakkanad IT park road resurfacing — one lane open"},
    ],
    "jaipur": [
        {"incident_type": "event",     "severity": "minor",    "description": "Wedding procession on MI Road causing temporary signal disruption"},
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Water line repair on Malviya Nagar main road — road partially dug"},
    ],
    "surat": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Diamond Necklace road expansion work near Varachha — lane narrowing"},
    ],
    "coimbatore": [
        {"incident_type": "accident",  "severity": "minor",    "description": "Bus breakdown on Avinashi Road — towed, traffic restored", "resolved_hours_ago": 1},
    ],
    "nagpur": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Ring Road flyover ramp maintenance — alternate route via Sitabuldi"},
    ],
    "indore": [
        {"incident_type": "event",     "severity": "minor",    "description": "Street food festival on Vijay Nagar main road causing parking congestion"},
    ],
}

# (location_name, latitude, longitude)
_INCIDENT_SEED_AREAS: dict[str, list[tuple]] = {
    "bangalore": [
        ("MG Road, Bangalore",  12.9756, 77.6099),
        ("Silk Board Junction", 12.9172, 77.6235),
        ("Whitefield",          12.9698, 77.7500),
        ("Koramangala",         12.9352, 77.6245),
        ("Electronic City",     12.8399, 77.6770),
        ("Hebbal Flyover",      13.0450, 77.5950),
    ],
    "hyderabad": [
        ("Hitech City",  17.4486, 78.3908),
        ("Gachibowli",   17.4401, 78.3489),
        ("Ameerpet",     17.4374, 78.4487),
        ("Begumpet",     17.4432, 78.4682),
        ("LB Nagar",     17.3481, 78.5494),
    ],
    "mumbai": [
        ("LBS Road, Mumbai",      19.0748, 72.8856),
        ("Worli Sea Link",        19.0195, 72.8144),
        ("Bandra Kurla Complex",  19.0660, 72.8680),
    ],
    "delhi": [
        ("Connaught Place", 28.6315, 77.2167),
        ("Rajpath",         28.6129, 77.2295),
        ("Lajpat Nagar",    28.5700, 77.2430),
    ],
    "chennai": [
        ("Anna Salai, Chennai",  13.0524, 80.2494),
        ("OMR Road Chennai",     12.9010, 80.2279),
    ],
    "pune": [
        ("Pune-Solapur Highway", 18.5204, 73.8567),
        ("Baner Road, Pune",     18.5590, 73.7868),
    ],
    "kolkata": [
        ("Park Street, Kolkata", 22.5524, 88.3510),
        ("Howrah Bridge",        22.5851, 88.3468),
        ("Salt Lake Sector V",   22.5764, 88.4322),
    ],
    "ahmedabad": [
        ("SG Highway",        23.0469, 72.5070),
        ("CG Road Ahmedabad", 23.0227, 72.5714),
    ],
    "lucknow": [
        ("Hazratganj Lucknow", 26.8467, 80.9462),
        ("Gomti Nagar",        26.8566, 81.0020),
    ],
    "kochi": [
        ("Edapally Junction", 10.0269, 76.3082),
        ("Kakkanad",          10.0158, 76.3419),
    ],
    "jaipur": [
        ("MI Road Jaipur",       26.9124, 75.7873),
        ("Malviya Nagar Jaipur", 26.8634, 75.8009),
    ],
    "surat": [
        ("Varachha Road Surat", 21.2154, 72.8700),
    ],
    "coimbatore": [
        ("Avinashi Road", 11.0168, 76.9558),
    ],
    "nagpur": [
        ("Ring Road Nagpur", 21.1458, 79.0882),
        ("Sitabuldi",        21.1497, 79.0806),
    ],
    "indore": [
        ("Vijay Nagar Indore", 22.7533, 75.8937),
    ],
}


def auto_seed_incidents(location: str, db: Session) -> None:
    """Idempotent: insert missing seed incidents for the city that matches location."""
    key = next((k for k in _INCIDENT_SEEDS if k in location.lower()), None)
    if key is None:
        return

    templates = _INCIDENT_SEEDS[key]
    areas = _INCIDENT_SEED_AREAS.get(key, [(location, None, None)])
    now = datetime.now(timezone.utc)
    added = False
    for i, tmpl in enumerate(templates):
        area_name, lat, lon = areas[i % len(areas)]
        exists = (
            db.query(Incident)
            .filter(Incident.location == area_name, Incident.incident_type == tmpl["incident_type"])
            .first()
        )
        if exists:
            continue
        resolved_hours = tmpl.get("resolved_hours_ago")
        resolved_at = now - timedelta(hours=resolved_hours) if resolved_hours else None
        inc = Incident(
            location=area_name,
            latitude=lat,
            longitude=lon,
            incident_type=tmpl["incident_type"],
            severity=tmpl["severity"],
            description=tmpl["description"],
            is_active=resolved_at is None,
            resolved_at=resolved_at,
            reported_at=now - timedelta(minutes=random.randint(10, 180)),
        )
        db.add(inc)
        added = True
    if added:
        db.commit()
