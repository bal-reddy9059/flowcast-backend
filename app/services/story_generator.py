"""Traffic story generator — converts live events into human-readable news cards."""

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_story_cache: list[dict] = []
_last_generated: Optional[datetime] = None
_CACHE_TTL_MINUTES = 5


# ── Speed-based context ────────────────────────────────────────────────────────

def _speed_descriptor(speed: float) -> tuple[str, str]:
    """Return (adjective, impact) based on km/h."""
    if speed < 8:
        return "near standstill", "adding 40+ minutes to most trips"
    if speed < 15:
        return "severely congested", "causing major delays across the area"
    if speed < 25:
        return "heavily congested", "adding 15–25 minutes to typical journeys"
    if speed < 35:
        return "moderately congested", "adding 8–12 minutes to most trips"
    if speed < 50:
        return "slightly slow", "causing minor 5-minute delays"
    return "moving well", "with minimal impact on travel times"


def _speed_vs_normal(speed: float, mode: str = "city") -> str:
    normal = 45 if mode == "city" else 60
    if speed <= 0:
        return ""
    pct = round((1 - speed / normal) * 100)
    if pct <= 5:
        return f"Speed is near normal at {round(speed)} km/h."
    if pct < 0:
        return f"Traffic is flowing freely at {round(speed)} km/h, above average."
    return f"Speed is {round(speed)} km/h — {pct}% below the normal {normal} km/h."


def _eta_impact(speed: float, distance_km: float = 10) -> str:
    if speed <= 0:
        return ""
    normal_speed = 45
    normal_min = (distance_km / normal_speed) * 60
    current_min = (distance_km / speed) * 60
    extra = round(current_min - normal_min)
    if extra <= 1:
        return "Minimal impact on travel times."
    return f"A typical {round(distance_km)} km trip takes ~{round(current_min)} min instead of the usual {round(normal_min)} min."


# ── Body generators ────────────────────────────────────────────────────────────

_HIGH_BODIES = [
    lambda loc, speed: (
        f"Traffic on roads around {loc} is {_speed_descriptor(speed)[0]}, with speeds averaging just {round(speed)} km/h. "
        f"{_eta_impact(speed)} Drivers are advised to seek alternate routes or delay travel."
    ),
    lambda loc, speed: (
        f"Severe congestion has built up near {loc}. {_speed_vs_normal(speed)} "
        f"The slowdown is {_speed_descriptor(speed)[1]}. Consider using parallel roads if available."
    ),
    lambda loc, speed: (
        f"Roads around {loc} are {_speed_descriptor(speed)[0]} with an average speed of {round(speed)} km/h. "
        f"This is significantly below normal flow. Check alternate routes before departing."
    ),
    lambda loc, speed: (
        f"Heavy gridlock reported at {loc} — vehicles moving at {round(speed)} km/h. "
        f"{_eta_impact(speed)} The congestion is {_speed_descriptor(speed)[1]}."
    ),
]

_MEDIUM_BODIES = [
    lambda loc, speed: (
        f"Traffic around {loc} is slower than usual at {round(speed)} km/h. "
        f"Build in an extra 10–15 minutes if you're heading through this area."
    ),
    lambda loc, speed: (
        f"Moderate congestion is developing near {loc}. {_speed_vs_normal(speed)} "
        f"Conditions may worsen during peak hours — monitor before departing."
    ),
    lambda loc, speed: (
        f"Expect some delays around {loc} where speeds have dropped to {round(speed)} km/h. "
        f"Not severe yet, but worth checking your alternate options."
    ),
]

_LOW_BODIES = [
    lambda loc, speed: (
        f"Roads near {loc} are flowing smoothly at {round(speed)} km/h — well above typical congestion thresholds. "
        f"Good window to travel through this corridor right now."
    ),
    lambda loc, speed: (
        f"Clear conditions reported around {loc} with speeds at {round(speed)} km/h. "
        f"If you need to pass through the area, now is a good time."
    ),
]

_INCIDENT_BODIES = {
    "accident": lambda loc, sev: (
        f"A {'serious' if sev == 'high' else 'minor'} accident has been reported at {loc}, causing lane restrictions. "
        f"Emergency services are on scene. Expect significant delays and use alternate routes where possible."
    ),
    "construction": lambda loc, sev: (
        f"Active road work at {loc} is reducing lane capacity. "
        f"{'Major' if sev == 'high' else 'Minor'} slowdowns expected — follow diversion signs and allow extra travel time."
    ),
    "roadblock": lambda loc, sev: (
        f"A roadblock is currently in effect at {loc}. "
        f"Traffic is being diverted — follow police guidance and expect {'major' if sev == 'high' else 'some'} delays."
    ),
    "flooding": lambda loc, sev: (
        f"Waterlogging has been reported at {loc}, making roads difficult to navigate. "
        f"Drive slowly, avoid underpasses, and consider alternate routes to stay safe."
    ),
    "event": lambda loc, sev: (
        f"A large event near {loc} is drawing unusually heavy traffic to the area. "
        f"Expect {'severe' if sev == 'high' else 'moderate'} congestion for the next few hours. Plan around it."
    ),
}

_INCIDENT_HEADLINES = {
    "accident":     ["Accident blocking lanes", "Crash causing major delays", "Road accident slowing traffic"],
    "construction": ["Road works causing slowdown", "Construction reducing lanes", "Diversion in effect"],
    "roadblock":    ["Roadblock affecting route", "Police diversion in place", "Road closure reported"],
    "flooding":     ["Waterlogging on roads", "Flooding causing hazards", "Wet roads — drive carefully"],
    "event":        ["Event traffic building up", "Large crowd causing gridlock", "Event congestion expected"],
}

_HIGH_HEADLINES = [
    "Severe gridlock reported",
    "Major slowdown underway",
    "Heavy congestion — expect delays",
    "Traffic at a standstill",
    "Roads grinding to a halt",
    "Significant bottleneck forming",
]

_MEDIUM_HEADLINES = [
    "Traffic slowing — minor delays",
    "Congestion building up",
    "Moderate delays developing",
    "Roads busier than usual",
    "Slowdown on key corridors",
]

_LOW_HEADLINES = [
    "Roads clear — good travel window",
    "Light traffic across the area",
    "Smooth flow reported",
    "Clear roads — ideal time to travel",
]

_TIPS = {
    "high":   [
        "Consider delaying your trip by 30–45 min or take an alternate route.",
        "Use parallel roads to avoid the worst of the congestion.",
        "Check live traffic before departing — conditions may shift soon.",
        "If possible, wait for peak congestion to ease before heading out.",
    ],
    "medium": [
        "Add 10–15 min buffer to your travel time.",
        "Monitor conditions before leaving — may worsen in peak hours.",
        "Minor delays expected — plan accordingly.",
        "Slight congestion building — you may want to leave a bit earlier.",
    ],
    "low": [
        "Good window to travel — conditions are favourable.",
        "Roads are clear — ideal time to make your trip.",
        "Enjoy the open roads while they last!",
    ],
}


def _pick(lst: list, index: int = 0) -> str:
    return lst[index % len(lst)]


def _make_story(headline: str, body: str, severity: str, location: str, tip: str = None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": str(uuid.uuid4()),
        "headline": headline,
        "body": body,
        "severity": severity,
        "location": location,
        "tip": tip,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=30)).isoformat(),
    }


def get_cached_stories() -> list[dict]:
    return list(_story_cache)


def is_cache_stale() -> bool:
    if _last_generated is None:
        return True
    return (datetime.now(timezone.utc) - _last_generated).total_seconds() > _CACHE_TTL_MINUTES * 60


def _build_fallback_stories(high_records, medium_records, low_records, incidents) -> list[dict]:
    """Build varied, context-aware story cards from DB data — no AI required."""
    stories = []
    seen = set()

    # Incidents first (most actionable)
    for i, inc in enumerate(incidents[:3]):
        loc = inc.location or "Unknown location"
        if loc in seen:
            continue
        seen.add(loc)
        inc_type = (inc.incident_type or "incident").lower()
        sev = inc.severity or "medium"
        headlines = _INCIDENT_HEADLINES.get(inc_type, ["Traffic incident reported"])
        headline = f"{_pick(headlines, i)} — {loc}"
        body_fn = _INCIDENT_BODIES.get(inc_type, lambda l, s: f"A {inc_type} at {l} is affecting traffic flow.")
        body = body_fn(loc, sev)
        tip = _pick(_TIPS.get(sev, _TIPS["medium"]), i)
        stories.append(_make_story(headline, body, sev, loc, tip))

    # High congestion stories
    for i, r in enumerate(high_records[:4]):
        loc = r.location or "Unknown"
        if loc in seen:
            continue
        seen.add(loc)
        speed = float(r.average_speed or 18)
        headline = f"{_pick(_HIGH_HEADLINES, len(stories))} — {loc}"
        body = _pick(_HIGH_BODIES, i)(loc, speed)
        tip = _pick(_TIPS["high"], i)
        stories.append(_make_story(headline, body, "high", loc, tip))

    # Medium congestion — add variety
    for i, r in enumerate(medium_records[:2]):
        loc = r.location or "Unknown"
        if loc in seen:
            continue
        seen.add(loc)
        speed = float(r.average_speed or 30)
        headline = f"{_pick(_MEDIUM_HEADLINES, i)} — {loc}"
        body = _pick(_MEDIUM_BODIES, i)(loc, speed)
        tip = _pick(_TIPS["medium"], i)
        stories.append(_make_story(headline, body, "medium", loc, tip))

    # One "good news" low story for balance
    for r in low_records[:1]:
        loc = r.location or "Unknown"
        if loc in seen:
            continue
        seen.add(loc)
        speed = float(r.average_speed or 55)
        headline = f"{_pick(_LOW_HEADLINES, 0)} — {loc}"
        body = _pick(_LOW_BODIES, 0)(loc, speed)
        tip = _pick(_TIPS["low"], 0)
        stories.append(_make_story(headline, body, "low", loc, tip))

    return stories


async def refresh_stories(db) -> list[dict]:
    """Pull recent events from DB, generate story cards, update cache."""
    global _story_cache, _last_generated

    from app.models.predictor import TrafficRecord, Incident
    from app.services.ai_service import generate_stories

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=30)

    high_records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.created_at >= since, TrafficRecord.congestion_level == "high")
        .order_by(TrafficRecord.created_at.desc())
        .limit(8)
        .all()
    )
    medium_records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.created_at >= since, TrafficRecord.congestion_level == "medium")
        .order_by(TrafficRecord.created_at.desc())
        .limit(4)
        .all()
    )
    low_records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.created_at >= since, TrafficRecord.congestion_level == "low")
        .order_by(TrafficRecord.created_at.desc())
        .limit(2)
        .all()
    )
    incidents = (
        db.query(Incident)
        .filter(Incident.is_active.is_(True))
        .order_by(Incident.created_at.desc())
        .limit(5)
        .all()
    )

    lines = []
    for r in high_records[:6]:
        lines.append(f"{r.congestion_level} congestion at {r.location}, speed={r.average_speed} km/h")
    for r in medium_records[:3]:
        lines.append(f"{r.congestion_level} congestion at {r.location}, speed={r.average_speed} km/h")
    for inc in incidents:
        lines.append(f"Incident: {inc.incident_type} at {inc.location}, severity={inc.severity}")

    # AI skipped unless AI_ENABLED=true and a real sk- key is set
    from app.services.ai_service import is_ai_available, generate_stories
    import asyncio

    if lines and is_ai_available():
        ai_stories = await asyncio.to_thread(generate_stories, "\n".join(lines))
        if ai_stories:
            stamped = []
            for s in ai_stories:
                stamped.append({
                    "id": str(uuid.uuid4()),
                    "headline": s.get("headline", "Traffic Update"),
                    "body": s.get("body", ""),
                    "severity": s.get("severity", "medium"),
                    "location": s.get("location", ""),
                    "tip": s.get("tip"),
                    "generated_at": now.isoformat(),
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                })
            _story_cache = stamped
            _last_generated = now
            logger.info("Generated %d AI traffic stories", len(stamped))
            return _story_cache

    # Rule-based stories (default when AI is skipped)
    fallback = _build_fallback_stories(high_records, medium_records, low_records, incidents)
    if fallback:
        _story_cache = fallback
        _last_generated = now
        logger.info("Generated %d rule-based traffic stories (AI skipped)", len(fallback))
        return _story_cache

    # Nothing in DB — serve last cache or placeholder
    if _story_cache:
        return _story_cache

    _story_cache = [_make_story(
        headline="FlowCast is monitoring India roads",
        body="Real-time traffic data is being collected across 766 Indian districts. Stories will appear as traffic events are detected.",
        severity="low",
        location="India",
        tip="Check back in a few minutes for live road updates.",
    )]
    _last_generated = now
    return _story_cache
