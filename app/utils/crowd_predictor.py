"""
Crowd scoring from live road-traffic data and historical crowd logs.

Live scores: HERE/TomTom flow at station coordinates.
When live APIs fail: deterministic time-of-day baseline (same inputs → same score).
Forecasts: historical crowd_logs averages, with baseline cold-start.
"""

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from app.services.traffic_flow_service import (
    fetch_flow,
    is_live_available,
    score_to_level,
    speed_ratio_to_score,
    congestion_to_score,
)

_IST = ZoneInfo("Asia/Kolkata")

# Relative city load for cold-start forecast baseline (deterministic, no randomness)
CITY_MULTIPLIER = {
    "Mumbai":    1.35,
    "Delhi":     1.30,
    "Kolkata":   1.20,
    "Chennai":   1.15,
    "Bangalore": 1.10,
    "Hyderabad": 1.05,
    "Pune":      0.95,
    "Lucknow":   0.90,
    "Ahmedabad": 0.88,
    "Jaipur":    0.85,
    "Surat":     0.85,
    "Bhopal":    0.82,
}


def now_ist() -> datetime:
    return datetime.now(_IST)


def baseline_crowd_score(
    station_type: str,
    hour: int,
    day_of_week: int,
    city: str = "Bangalore",
) -> dict:
    """
    Deterministic time-of-day baseline used when no live API or log history.
    No random variance — same inputs always produce the same score.
    """
    # Soft peaks so baseline alone does not always read as "Overcrowded"
    if 7 <= hour <= 10:
        base = 68
    elif 17 <= hour <= 21:
        base = 72
    elif 11 <= hour <= 16:
        base = 42
    elif hour in (6, 22):
        base = 28
    else:
        base = 12

    if day_of_week >= 5:
        base = max(int(base * 0.55), 10)

    type_mod = 1.08 if station_type == "railway" else 1.0
    city_mod = CITY_MULTIPLIER.get(city, 1.0)
    # Cap baseline below Overcrowded — reserve 76–100 for live evidence
    score = int(min(74, max(0, round(base * type_mod * city_mod))))

    return {
        "score": score,
        "level": score_to_level(score),
        "source": "baseline",
        "current_speed_kmh": None,
        "free_flow_speed_kmh": None,
        "confidence": None,
    }


def _guard_live_with_baseline(
    live: dict,
    station_type: str,
    hour: int,
    day_of_week: int,
    city: str,
) -> dict:
    """
    Reject absurd live scores (e.g. free-flow road near station → crowd 0 at 5 PM).
    Prefer baseline when live is far below the time-of-day prior.
    """
    base = baseline_crowd_score(station_type, hour, day_of_week, city)
    live_score = live.get("score")
    if live_score is None:
        return base

    base_score = base["score"]
    # Free-flow / failed mapping often yields 0–5; that must not poison history
    if live_score <= 5 and base_score >= 20:
        return {
            **base,
            "current_speed_kmh": live.get("current_speed_kmh"),
            "free_flow_speed_kmh": live.get("free_flow_speed_kmh"),
            "confidence": live.get("confidence"),
            "source": "baseline_guard",
        }
    # Live much lower than prior during typical peak → blend upward
    if base_score >= 50 and live_score < base_score * 0.35:
        blended = int(round(live_score * 0.4 + base_score * 0.6))
        return {
            **live,
            "score": blended,
            "level": score_to_level(blended),
            "source": f"{live.get('source', 'live')}+baseline",
        }
    return live


async def fetch_live_crowd(
    lat: float,
    lng: float,
    station_type: str,
    prev_score: int | None = None,
    *,
    city: str = "Bangalore",
    hour: int | None = None,
    day_of_week: int | None = None,
    allow_baseline: bool = True,
) -> dict:
    """
    Fetch live traffic flow and derive crowd score from road congestion.

    Falls back to deterministic baseline when APIs are down / return nothing
    (unless allow_baseline=False). Guards against free-flow → score 0 pollution.
    """
    now = now_ist()
    hour = now.hour if hour is None else hour
    day_of_week = now.weekday() if day_of_week is None else day_of_week

    if is_live_available():
        flow = await fetch_flow(lat, lng)
        if flow is not None:
            cur = float(flow.get("currentSpeed", 0) or 0)
            free = float(flow.get("freeFlowSpeed", 0) or 0)
            score = speed_ratio_to_score(cur, free, station_type, prev_score)
            live = {
                "score": score,
                "level": score_to_level(score),
                "source": flow.get("source", "live"),
                "current_speed_kmh": round(cur, 1),
                "free_flow_speed_kmh": round(free, 1),
                "confidence": flow.get("confidence"),
            }
            if allow_baseline:
                return _guard_live_with_baseline(live, station_type, hour, day_of_week, city)
            return live

    if allow_baseline:
        return baseline_crowd_score(station_type, hour, day_of_week, city)

    return {
        "score": None,
        "level": "Unavailable",
        "source": "unavailable",
        "current_speed_kmh": None,
        "free_flow_speed_kmh": None,
        "confidence": None,
    }


def crowd_from_congestion(
    congestion: str,
    station_type: str,
    current_speed: float | None = None,
) -> dict:
    """Map a local traffic_records congestion label to a crowd estimate."""
    score = congestion_to_score(congestion or "medium")
    if station_type == "railway":
        score = int(min(100, round(score * 1.12)))
    return {
        "score": score,
        "level": score_to_level(score),
        "source": "local_traffic",
        "current_speed_kmh": round(current_speed, 1) if current_speed is not None else None,
        "free_flow_speed_kmh": None,
        "confidence": None,
    }


def crowd_from_log_avg(avg_score: float) -> dict:
    score = int(min(100, max(0, round(avg_score))))
    return {"score": score, "level": score_to_level(score), "source": "historical"}


def merge_hourly_scores(
    log_scores: dict[int, float],
    station_type: str,
    dow: int,
    city: str,
    min_samples: int = 3,
) -> dict[int, dict]:
    """Build 24-hour profile: prefer log averages, fall back to deterministic baseline."""
    result: dict[int, dict] = {}
    for h in range(24):
        if h in log_scores and log_scores[h]["count"] >= min_samples:
            pred = crowd_from_log_avg(log_scores[h]["avg"])
            pred["sample_size"] = log_scores[h]["count"]
        else:
            pred = baseline_crowd_score(station_type, h, dow, city)
            pred["sample_size"] = log_scores.get(h, {}).get("count", 0)
        result[h] = pred
    return result
