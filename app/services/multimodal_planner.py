"""Multi-modal journey planner — AI + rule-based fallback for Indian cities."""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Metro city data
_METRO_CITIES = {
    "delhi":     {"lines": ["Yellow Line", "Blue Line", "Red Line", "Violet Line", "Pink Line"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 35},
    "mumbai":    {"lines": ["Line 1 (Versova–Ghatkopar)", "Line 2A (Dahisar–DN Nagar)", "Line 7"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 32},
    "bangalore": {"lines": ["Purple Line (Baiyappanahalli–Mysuru Rd)", "Green Line (Nagasandra–Silk Board)"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 35},
    "hyderabad": {"lines": ["Red Line (Miyapur–LB Nagar)", "Blue Line (Jubilee Hills–MGBS)"], "base_fare": 10, "per_km": 2.0, "speed_kmh": 32},
    "chennai":   {"lines": ["Blue Line (Wimco Nagar–Chennai Airport)", "Green Line"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 35},
    "kolkata":   {"lines": ["Blue Line (Dakshineswar–Kavi Subhas)", "Orange Line"], "base_fare": 5, "per_km": 2.0, "speed_kmh": 30},
    "pune":      {"lines": ["Line 1 (PCMC–Swargate)", "Line 2 (Vanaz–Ramwadi)"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 30},
    "ahmedabad": {"lines": ["East-West Corridor", "North-South Corridor"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 35},
    "kochi":     {"lines": ["Orange Line (Aluva–Tripunithura)"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 35},
    "gurgaon":   {"lines": ["Yellow Line (Delhi–Huda City Centre)"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 35},
    "noida":     {"lines": ["Blue Line Extension (Noida–Vaishali)"], "base_fare": 10, "per_km": 2.5, "speed_kmh": 35},
}

# City aliases for detection
_CITY_ALIASES = {
    "mumbai": ["mumbai", "bombay", "bkc", "andheri", "bandra", "dadar", "kurla", "thane",
               "ghatkopar", "powai", "lower parel", "worli", "borivali", "malad", "goregaon",
               "santacruz", "vile parle", "kandivali", "juhu", "versova", "navi mumbai"],
    "delhi":  ["delhi", "new delhi", "connaught", "dwarka", "rohini", "janakpuri",
               "lajpat", "saket", "vasant", "nehru place", "hauz khas"],
    "bangalore": ["bangalore", "bengaluru", "whitefield", "koramangala", "indiranagar",
                  "silk board", "mg road", "electronic city", "hsr", "btm", "jayanagar",
                  "marathahalli", "hebbal", "jp nagar"],
    "hyderabad": ["hyderabad", "hitech city", "gachibowli", "banjara hills", "jubilee hills",
                  "secunderabad", "begumpet", "ameerpet", "kukatpally", "lb nagar"],
    "chennai":   ["chennai", "madras", "t nagar", "adyar", "anna nagar", "velachery",
                  "omr", "guindy", "tambaram"],
    "kolkata":   ["kolkata", "calcutta", "salt lake", "park street", "howrah", "dumdum"],
    "pune":      ["pune", "hinjewadi", "kothrud", "shivajinagar", "baner", "hadapsar",
                  "viman nagar", "koregaon park", "pimpri"],
    "gurgaon":   ["gurgaon", "gurugram", "cyber city", "dlf", "sohna"],
    "noida":     ["noida", "greater noida", "sector 18", "sector 62"],
}


def _detect_city(origin: str, destination: str) -> Optional[str]:
    """Detect which metro city this journey is in."""
    text = (origin + " " + destination).lower()
    for city, aliases in _CITY_ALIASES.items():
        for alias in aliases:
            if alias in text:
                return city
    return None


def _driving_eta_minutes(distance_km: float, congestion: str = "medium") -> float:
    speeds = {"low": 45, "medium": 28, "high": 15}
    speed = speeds.get(congestion, 28)
    return (distance_km / speed) * 60


def _round_cost(value: float) -> int:
    if value <= 0:
        return 0
    return max(10, int(round(value / 5) * 5))


# ── Rule-based plan builder ────────────────────────────────────────────────────

def _build_rule_plan(
    origin: str,
    destination: str,
    distance_km: float,
    city: Optional[str],
    congestion: str,
    db: Session,
) -> dict:
    """Build a realistic multi-modal plan from rules — no API key needed."""
    from zoneinfo import ZoneInfo
    segments = []
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    hour = now_ist.hour
    is_peak = (7 <= hour <= 10) or (17 <= hour <= 21)

    metro_info = _METRO_CITIES.get(city) if city else None
    driving_min = _driving_eta_minutes(distance_km, congestion)

    # ── Same location ──────────────────────────────────────────────────────
    if distance_km < 0.05:
        return {
            "segments": [],
            "total_duration_min": 0,
            "total_cost_inr": 0,
            "vs_driving_only_min": 0,
            "vs_driving_only_cost_inr": 0,
            "carbon_saved_kg": 0.0,
            "drive_only": {"duration_min": 0, "cost_inr": 0},
            "peak_hour": is_peak,
            "city_detected": city,
            "metro_available": metro_info is not None,
            "summary": "Origin and destination appear to be the same location — no journey needed.",
            "source": "rule_based",
        }

    # ── Very short trip (< 2.5 km) ─────────────────────────────────────────
    if distance_km < 2.5:
        walk_min = round(distance_km / 5 * 60)
        auto_min = round(distance_km / 15 * 60) + 3
        auto_cost = _round_cost(20 + distance_km * 15)
        segments = [{
            "mode": "walking",
            "from": origin,
            "to": destination,
            "duration_min": walk_min,
            "cost_inr": 0,
            "notes": f"{distance_km:.1f} km on foot — fastest option for this distance",
        }]
        total_min = walk_min
        total_cost = 0
        vs_driving = round(driving_min - walk_min)
        carbon_saved = round(distance_km * 0.12, 2)
        summary = f"It's just {distance_km:.1f} km — walk or take an auto. Walking saves ₹{auto_cost} and {round(distance_km * 0.12, 2)} kg CO₂."

    # ── Short trip (2.5–6 km) ──────────────────────────────────────────────
    elif distance_km < 6:
        auto_min = round(distance_km / 18 * 60) + 3
        auto_cost = _round_cost(20 + distance_km * 15)
        surge = " (surge likely)" if is_peak else ""
        segments = [{
            "mode": "auto_rickshaw",
            "from": origin,
            "to": destination,
            "duration_min": auto_min,
            "cost_inr": auto_cost,
            "notes": f"Quick auto hop — {distance_km:.1f} km, ₹{auto_cost} estimated{surge}",
        }]
        total_min = auto_min
        total_cost = auto_cost
        vs_driving = round(driving_min - auto_min)
        carbon_saved = round(distance_km * 0.09, 2)
        summary = f"Best option for {distance_km:.1f} km is a direct auto (₹{auto_cost}, ~{auto_min} min)."

    # ── Medium trip with metro (6–30 km) ───────────────────────────────────
    elif distance_km < 30 and metro_info:
        line = metro_info["lines"][0]
        metro_speed = metro_info["speed_kmh"]
        base_fare = metro_info["base_fare"]
        per_km = metro_info["per_km"]

        # Drive to metro station: ~25% of trip distance
        drive_leg_km = round(distance_km * 0.25, 1)
        drive_leg_min = round(_driving_eta_minutes(drive_leg_km, congestion if is_peak else "medium"))
        cab_cost_to_metro = _round_cost(drive_leg_km * 16)

        # Metro leg: ~60% of distance
        metro_leg_km = round(distance_km * 0.60, 1)
        metro_min = round(metro_leg_km / metro_speed * 60) + 5  # +5 for wait
        metro_fare = _round_cost(base_fare + metro_leg_km * per_km)

        # Auto last mile: remaining ~15%
        last_mile_km = round(distance_km - drive_leg_km - metro_leg_km, 1)
        last_mile_km = max(last_mile_km, 1.0)
        last_min = round(last_mile_km / 15 * 60) + 2
        last_cost = _round_cost(20 + last_mile_km * 15)

        total_min = drive_leg_min + metro_min + last_min
        total_cost = cab_cost_to_metro + metro_fare + last_cost
        vs_driving = round(driving_min - total_min)
        carbon_saved = round((distance_km * 0.21) - (metro_leg_km * 0.03), 2)

        segments = [
            {
                "mode": "driving",
                "from": origin,
                "to": f"Nearest Metro Station",
                "duration_min": drive_leg_min,
                "cost_inr": cab_cost_to_metro,
                "notes": f"{drive_leg_km} km drive to metro — {'avoid peak traffic' if is_peak else 'clear roads right now'}",
            },
            {
                "mode": "metro",
                "from": "Metro Station",
                "to": "Metro Station (near destination)",
                "duration_min": metro_min,
                "cost_inr": metro_fare,
                "notes": f"{line} — {metro_leg_km} km, no traffic delays",
            },
            {
                "mode": "auto_rickshaw",
                "from": "Metro Station",
                "to": destination,
                "duration_min": last_min,
                "cost_inr": last_cost,
                "notes": f"{last_mile_km} km last mile — autos readily available near metro exits",
            },
        ]
        summary = (
            f"Drive {drive_leg_km} km to metro, take {line.split('(')[0].strip()} for {metro_leg_km} km, "
            f"then auto for the last {last_mile_km} km. "
            f"{'Saves ' + str(abs(vs_driving)) + ' min vs all-driving.' if vs_driving > 0 else 'Similar time to driving but far cheaper.'}"
        )

    # ── Medium trip, no metro ──────────────────────────────────────────────
    elif distance_km < 20 and not metro_info:
        cab_cost = _round_cost(distance_km * 16)
        cab_min = round(driving_min)
        bus_min = round(driving_min * 1.3)
        bus_cost = 20
        segments = [
            {
                "mode": "driving",
                "from": origin,
                "to": destination,
                "duration_min": cab_min,
                "cost_inr": cab_cost,
                "notes": f"App-cab (Ola/Uber) — ₹{cab_cost} estimated{', expect surge' if is_peak else ''}",
            },
        ]
        total_min = cab_min
        total_cost = cab_cost
        vs_driving = 0
        carbon_saved = 0.0
        summary = f"No metro available here. App-cab is the most convenient for {distance_km:.1f} km (₹{cab_cost}, ~{cab_min} min)."

    # ── Long trip (30+ km) ─────────────────────────────────────────────────
    else:
        if metro_info:
            line = metro_info["lines"][0]
            metro_speed = metro_info["speed_kmh"]
            base_fare = metro_info["base_fare"]
            per_km = metro_info["per_km"]

            drive_leg_km = round(distance_km * 0.15, 1)
            drive_leg_min = round(_driving_eta_minutes(drive_leg_km, congestion))
            cab_to_metro = _round_cost(drive_leg_km * 16)

            metro_leg_km = round(distance_km * 0.70, 1)
            metro_min = round(metro_leg_km / metro_speed * 60) + 8
            metro_fare = _round_cost(base_fare + metro_leg_km * per_km)

            last_km = round(distance_km - drive_leg_km - metro_leg_km, 1)
            last_km = max(last_km, 1.5)
            last_min = round(last_km / 15 * 60) + 3
            last_cost = _round_cost(20 + last_km * 15)

            total_min = drive_leg_min + metro_min + last_min
            total_cost = cab_to_metro + metro_fare + last_cost
            vs_driving = round(driving_min - total_min)
            carbon_saved = round((distance_km * 0.21) - (metro_leg_km * 0.03), 2)

            segments = [
                {
                    "mode": "driving",
                    "from": origin,
                    "to": "Metro Station",
                    "duration_min": drive_leg_min,
                    "cost_inr": cab_to_metro,
                    "notes": f"{drive_leg_km} km — quick drive to metro access point",
                },
                {
                    "mode": "metro",
                    "from": "Metro Station",
                    "to": "Metro Station (destination end)",
                    "duration_min": metro_min,
                    "cost_inr": metro_fare,
                    "notes": f"{line} — {metro_leg_km} km express, bypasses all road congestion",
                },
                {
                    "mode": "auto_rickshaw",
                    "from": "Metro Station",
                    "to": destination,
                    "duration_min": last_min,
                    "cost_inr": last_cost,
                    "notes": f"Last {last_km} km by auto — short hop from metro exit",
                },
            ]
            summary = (
                f"For {distance_km:.1f} km, metro is the smartest option. "
                f"Drive to the nearest station, take {line.split('(')[0].strip()} for {metro_leg_km} km, "
                f"then auto last mile. {'Saves ' + str(abs(vs_driving)) + ' min and ₹' + str(round(distance_km * 16 - total_cost)) + ' vs pure cab.' if vs_driving > 0 else 'Comparable time to driving but significantly cheaper.'}"
            )
        else:
            cab_cost = _round_cost(distance_km * 15)
            cab_min = round(driving_min)
            segments = [{
                "mode": "driving",
                "from": origin,
                "to": destination,
                "duration_min": cab_min,
                "cost_inr": cab_cost,
                "notes": f"Long distance — app-cab or self-drive recommended for {distance_km:.1f} km",
            }]
            total_min = cab_min
            total_cost = cab_cost
            vs_driving = 0
            carbon_saved = 0.0
            summary = f"{distance_km:.1f} km — driving is the most practical option here. Consider sharing a cab to reduce cost and emissions."

    # Driving-only comparison
    drive_only_cost = _round_cost(distance_km * 17)
    drive_only_min = round(driving_min)

    return {
        "segments": segments,
        "total_duration_min": total_min,
        "total_cost_inr": total_cost,
        "vs_driving_only_min": vs_driving,
        "vs_driving_only_cost_inr": drive_only_cost - total_cost,
        "carbon_saved_kg": max(0.0, carbon_saved),
        "drive_only": {"duration_min": drive_only_min, "cost_inr": drive_only_cost},
        "peak_hour": is_peak,
        "city_detected": city,
        "metro_available": metro_info is not None,
        "summary": summary,
        "source": "rule_based",
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def build_journey_context(
    origin: str, destination: str,
    origin_lat: float, origin_lng: float,
    dest_lat: float, dest_lng: float,
    distance_km: float, db: Session,
) -> str:
    from zoneinfo import ZoneInfo
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    lines = [
        f"Origin: {origin} (lat={origin_lat}, lng={origin_lng})",
        f"Destination: {destination} (lat={dest_lat}, lng={dest_lng})",
        f"Straight-line distance: {distance_km:.1f} km",
        f"Current time (IST): {now_ist.strftime('%A %H:%M')}",
        f"Driving ETA: ~{round(distance_km / 28 * 60)} min (estimated)",
    ]

    city = _detect_city(origin, destination)
    metro_info = _METRO_CITIES.get(city) if city else None
    if metro_info:
        lines.append(f"Metro available ({city.title()}): {', '.join(metro_info['lines'][:2])}")
        lines.append(f"Metro fare: base Rs {metro_info['base_fare']} + Rs {metro_info['per_km']}/km")
    else:
        lines.append("Metro: not available for this route")

    lines += [
        "Auto-rickshaw: Rs 20 base + Rs 15/km",
        "App-cab (Ola/Uber): Rs 14-18/km + possible peak surge",
        "Bus: Rs 5-25 flat",
        "Walking: suitable under 2km at 5 km/h",
    ]
    return "\n".join(lines)


def get_multimodal_plan(
    origin: str, destination: str,
    origin_lat: float, origin_lng: float,
    dest_lat: float, dest_lng: float,
    distance_km: float, db: Session,
) -> dict:
    """Generate a multi-modal plan. Uses Claude AI if key is set, falls back to rule engine.

    Always returns a frontend-friendly payload with both nested `plan` and top-level
    `segments` / `summary` aliases so clients that expect either shape work.
    """
    from zoneinfo import ZoneInfo
    from app.services.ai_service import generate_multimodal_plan, is_ai_available

    _IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(timezone.utc)
    now_ist = now.astimezone(_IST)

    # Detect city — prefer shared NCR metro when trip spans Delhi/Gurgaon/Noida
    city = _detect_city(origin, destination)
    text = f"{origin} {destination}".lower()
    if any(k in text for k in ("gurgaon", "gurugram", "noida", "faridabad")) and any(
        k in text for k in ("delhi", "connaught", "cp", "dwarka", "saket", "airport")
    ):
        city = "delhi"

    # Fast congestion hint — never block the planner on slow ETA/DB
    congestion = "medium"
    try:
        from app.services.weather_service import weather_impact_for_location
        impact = weather_impact_for_location(origin)
        mod = impact.get("congestion_modifier", "none")
        congestion = {"none": "low", "light": "low", "moderate": "medium", "severe": "high"}.get(mod, "medium")
    except Exception:
        hour = now_ist.hour
        if (7 <= hour <= 10) or (17 <= hour <= 21):
            congestion = "high"

    plan: dict
    ai_enhanced = False

    if is_ai_available():
        context = build_journey_context(
            origin, destination, origin_lat, origin_lng, dest_lat, dest_lng, distance_km, db
        )
        ai_plan = generate_multimodal_plan(context)
        if "error" not in ai_plan and ai_plan.get("segments"):
            plan = {**ai_plan, "source": "ai"}
            ai_enhanced = True
            logger.info("AI multimodal plan: %s to %s (%.1f km)", origin, destination, distance_km)
        else:
            plan = _build_rule_plan(origin, destination, distance_km, city, congestion, db)
            logger.info(
                "AI plan unavailable (%s) — rule fallback: %s to %s",
                ai_plan.get("error", "empty"), origin, destination,
            )
    else:
        plan = _build_rule_plan(origin, destination, distance_km, city, congestion, db)
        logger.info(
            "Rule-based multimodal plan: %s to %s (%.1f km, city=%s)",
            origin, destination, distance_km, city,
        )

    # Guarantee segments exist for distinct places (never leave frontend with empty plan)
    if distance_km >= 0.05 and not plan.get("segments"):
        plan = _build_rule_plan(origin, destination, max(distance_km, 1.0), city, congestion, db)

    return {
        "origin": origin,
        "destination": destination,
        "distance_km": round(distance_km, 2),
        "ai_available": is_ai_available(),
        "ai_enhanced": ai_enhanced,
        "source": plan.get("source", "rule_based"),
        "message": (
            "AI-enhanced multi-modal plan"
            if ai_enhanced
            else "Journey plan ready (rule-based multi-modal)."
        ),
        "ai_hint": (
            None if ai_enhanced
            else "Optional: set AI_ENABLED=true and ANTHROPIC_API_KEY for Claude-enhanced plans."
        ),
        # Nested object (existing contract)
        "plan": plan,
        # Flattened aliases — many UIs look for these at the top level
        "segments": plan.get("segments", []),
        "summary": plan.get("summary"),
        "total_duration_min": plan.get("total_duration_min"),
        "total_cost_inr": plan.get("total_cost_inr"),
        "vs_driving_only_min": plan.get("vs_driving_only_min"),
        "carbon_saved_kg": plan.get("carbon_saved_kg"),
        "drive_only": plan.get("drive_only"),
        "city_detected": plan.get("city_detected"),
        "metro_available": plan.get("metro_available"),
        "peak_hour": plan.get("peak_hour"),
        # Array form some clients expect
        "plans": [plan],
        "generated_at": now_ist.isoformat(),
    }
