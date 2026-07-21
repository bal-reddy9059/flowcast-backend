import asyncio
import uuid
from datetime import datetime
from typing import Optional

from app.services.crowd_station_service import resolve_station_id
from app.utils import crowd_live_cache
from app.utils.crowd_predictor import (
    baseline_crowd_score,
    fetch_live_crowd,
    merge_hourly_scores,
    now_ist,
)

HOUR_LABELS = [
    "12 AM", "1 AM", "2 AM", "3 AM", "4 AM", "5 AM",
    "6 AM", "7 AM", "8 AM", "9 AM", "10 AM", "11 AM",
    "12 PM", "1 PM", "2 PM", "3 PM", "4 PM", "5 PM",
    "6 PM", "7 PM", "8 PM", "9 PM", "10 PM", "11 PM",
]

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _as_uuid(station_id: str):
    resolved = resolve_station_id(station_id)
    try:
        return uuid.UUID(resolved)
    except (ValueError, AttributeError, TypeError):
        return resolved


def generate_recommendation(crowd_level: str, score: int | None, data_source: str | None = None) -> str:
    if crowd_level == "Unavailable" or score is None:
        return (
            "Live traffic data is unavailable. Showing time-of-day estimates — "
            "configure a valid TOMTOM_API_KEY for real-time crowd."
        )
    src = (data_source or "").lower()
    estimate_note = ""
    if "baseline" in src:
        estimate_note = " (time-of-day estimate)"

    if crowd_level == "Low":
        return f"Great time to visit! Station is comfortable right now{estimate_note}."
    if crowd_level == "Moderate":
        return f"Manageable crowd. Expect some wait times{estimate_note}."
    if crowd_level == "High":
        return f"Station is busy. Consider arriving 30 minutes early{estimate_note}."
    return f"Overcrowded! Avoid if possible. Try visiting during off-peak hours{estimate_note}."


async def _build_crowd_response(
    station: dict,
    now: datetime,
    prev_score: int | None = None,
) -> dict:
    lat = float(station["lat"]) if station.get("lat") is not None else None
    lng = float(station["lng"]) if station.get("lng") is not None else None
    city = station.get("city") or "Bangalore"
    stype = station.get("type") or "bus"

    if lat is not None and lng is not None:
        pred = await fetch_live_crowd(
            lat, lng, stype,
            prev_score=prev_score,
            city=city,
            hour=now.hour,
            day_of_week=now.weekday(),
            allow_baseline=True,
        )
    else:
        pred = baseline_crowd_score(stype, now.hour, now.weekday(), city)

    score = pred.get("score")
    level = pred.get("level") or ("Unavailable" if score is None else "Moderate")
    source = pred.get("source", "baseline")

    return {
        "station_id": station["id"],
        "station_name": station["name"],
        "type": station["type"],
        "city": station["city"],
        "state": station.get("state"),
        "crowd_score": score,
        "crowd_level": level,
        "capacity": station["capacity"],
        "peak_hours": station.get("peak_hours"),
        "amenities": list(station.get("amenities") or []),
        "estimated_people": round((score / 100) * station["capacity"]) if score is not None else None,
        "predicted_at": now.isoformat(),
        "data_source": source,
        "traffic_speed_kmh": pred.get("current_speed_kmh"),
        "free_flow_speed_kmh": pred.get("free_flow_speed_kmh"),
        "traffic_confidence": pred.get("confidence"),
        "recommendation": generate_recommendation(level, score, source),
    }


def _build_baseline_response(station: dict, now: datetime) -> dict:
    """Build an immediate deterministic response without outbound HTTP."""
    pred = baseline_crowd_score(
        station.get("type") or "bus",
        now.hour,
        now.weekday(),
        station.get("city") or "Bangalore",
    )
    score = pred["score"]
    level = pred["level"]
    return {
        "station_id": station["id"],
        "station_name": station["name"],
        "type": station["type"],
        "city": station["city"],
        "state": station.get("state"),
        "crowd_score": score,
        "crowd_level": level,
        "capacity": station["capacity"],
        "peak_hours": station.get("peak_hours"),
        "amenities": list(station.get("amenities") or []),
        "estimated_people": round((score / 100) * station["capacity"]),
        "predicted_at": now.isoformat(),
        "data_source": "baseline",
        "traffic_speed_kmh": None,
        "free_flow_speed_kmh": None,
        "traffic_confidence": None,
        "recommendation": generate_recommendation(level, score, "baseline"),
    }


async def _log_prediction(
    conn,
    station_id: str,
    score: int | None,
    level: str,
    hour: int,
    dow: int,
    data_source: str | None = None,
) -> None:
    """
    Persist a crowd sample for forecasting.

    Skips null/zero scores (bad live free-flow) and throttles to one sample
    per station per IST hour so the updater does not flood the table.
    """
    if score is None or score <= 0:
        return

    sid = _as_uuid(station_id)
    # One meaningful sample per station-hour
    existing = await conn.fetchval(
        """
        SELECT 1 FROM crowd_logs
        WHERE station_id = $1 AND hour_of_day = $2 AND day_of_week = $3
          AND predicted_at > NOW() - INTERVAL '55 minutes'
          AND crowd_score > 0
        LIMIT 1
        """,
        sid, hour, dow,
    )
    if existing:
        return

    # Prefer logging live / blended samples; allow baseline at most once/hour (handled above)
    await conn.execute(
        """
        INSERT INTO crowd_logs (station_id, crowd_score, crowd_level, hour_of_day, day_of_week)
        VALUES ($1, $2, $3, $4, $5)
        """,
        sid, score, level, hour, dow,
    )


async def _purge_bad_logs(conn) -> None:
    """Remove score≤5 pollution (free-flow → 0) that dragged historical averages down."""
    try:
        await conn.execute("DELETE FROM crowd_logs WHERE crowd_score <= 5")
    except Exception:
        pass


async def _fetch_log_hourly_averages(conn, station_id: str, dow: int) -> dict[int, dict]:
    rows = await conn.fetch(
        """
        SELECT hour_of_day, AVG(crowd_score)::float AS avg_score, COUNT(*)::int AS cnt
        FROM crowd_logs
        WHERE station_id = $1 AND day_of_week = $2 AND crowd_score > 5
        GROUP BY hour_of_day
        """,
        _as_uuid(station_id), dow,
    )
    return {
        r["hour_of_day"]: {"avg": r["avg_score"], "count": r["cnt"]}
        for r in rows
    }


async def _fetch_log_weekly_averages(conn, station_id: str) -> dict[int, dict]:
    rows = await conn.fetch(
        """
        SELECT day_of_week, AVG(crowd_score)::float AS avg_score, COUNT(*)::int AS cnt
        FROM crowd_logs
        WHERE station_id = $1 AND crowd_score > 5
        GROUP BY day_of_week
        """,
        _as_uuid(station_id),
    )
    return {
        r["day_of_week"]: {"avg": r["avg_score"], "count": r["cnt"]}
        for r in rows
    }


async def update_all_crowd(pool) -> list:
    async with pool.acquire() as conn:
        await _purge_bad_logs(conn)
        rows = await conn.fetch("SELECT * FROM stations ORDER BY city, name")
        now = now_ist()
        stations = []
        for row in rows:
            station = dict(row)
            station["id"] = str(station["id"])
            stations.append(station)

        async def _one(station: dict) -> dict:
            prev = crowd_live_cache.get_station_crowd(station["id"])
            prev_score = prev["crowd_score"] if prev and prev.get("crowd_score") is not None else None
            return await _build_crowd_response(station, now, prev_score=prev_score)

        datas = await asyncio.gather(*[_one(s) for s in stations])

        result = []
        for station, data in zip(stations, datas):
            await _log_prediction(
                conn,
                station["id"],
                data["crowd_score"],
                data["crowd_level"],
                now.hour,
                now.weekday(),
                data.get("data_source"),
            )
            crowd_live_cache.set_station_crowd(station["id"], data)
            result.append(data)
    return result


async def get_crowd_now(pool, station_id: str) -> Optional[dict]:
    resolved = resolve_station_id(station_id)
    cached = crowd_live_cache.get_station_crowd(resolved)
    if cached and cached.get("crowd_score") is not None:
        return cached
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM stations WHERE id = $1", _as_uuid(resolved))
        if not row:
            return None
        station = dict(row)
        station["id"] = str(station["id"])
        now = now_ist()
        data = _build_baseline_response(station, now)
    crowd_live_cache.set_station_crowd(station["id"], data)
    return data


async def get_all_crowd_now(pool) -> list:
    cached = crowd_live_cache.get_all_crowd()
    if cached and any(c.get("crowd_score") is not None for c in cached):
        return cached
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM stations ORDER BY city, name")
    now = now_ist()
    result = []
    for row in rows:
        station = dict(row)
        station["id"] = str(station["id"])
        data = _build_baseline_response(station, now)
        crowd_live_cache.set_station_crowd(station["id"], data)
        result.append(data)
    return result


async def get_hourly_prediction(pool, station_id: str) -> Optional[list]:
    sid = _as_uuid(station_id)
    now = now_ist()
    async with pool.acquire() as conn:
        await _purge_bad_logs(conn)
        row = await conn.fetchrow(
            "SELECT type, city FROM stations WHERE id = $1", sid,
        )
        if not row:
            return None
        log_scores = await _fetch_log_hourly_averages(conn, station_id, now.weekday())

    station_type, city = row["type"], row["city"]
    hourly = merge_hourly_scores(log_scores, station_type, now.weekday(), city)

    return [
        {
            "hour": h,
            "label": HOUR_LABELS[h],
            "crowd_score": hourly[h]["score"],
            "crowd_level": hourly[h]["level"],
            "data_source": hourly[h]["source"],
            "sample_size": hourly[h].get("sample_size", 0),
        }
        for h in range(24)
    ]


async def get_weekly_pattern(pool, station_id: str) -> Optional[list]:
    sid = _as_uuid(station_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT type, city FROM stations WHERE id = $1", sid,
        )
        if not row:
            return None
        weekly_logs = await _fetch_log_weekly_averages(conn, station_id)
        peak_rows = await conn.fetch(
            """
            SELECT day_of_week, hour_of_day
            FROM (
                SELECT day_of_week, hour_of_day,
                       AVG(crowd_score) AS avg_score,
                       ROW_NUMBER() OVER (
                           PARTITION BY day_of_week ORDER BY AVG(crowd_score) DESC
                       ) AS rn
                FROM crowd_logs
                WHERE station_id = $1 AND crowd_score > 0
                GROUP BY day_of_week, hour_of_day
            ) t
            WHERE rn = 1
            """,
            sid,
        )
    peak_by_dow = {r["day_of_week"]: int(r["hour_of_day"]) for r in peak_rows}

    station_type, city = row["type"], row["city"]
    patterns = []
    for dow in range(7):
        hourly = merge_hourly_scores({}, station_type, dow, city)
        if dow in weekly_logs and weekly_logs[dow]["count"] >= 5:
            avg_score = round(weekly_logs[dow]["avg"], 1)
            source = "historical"
            peak_hour = peak_by_dow.get(dow, max(range(24), key=lambda h: hourly[h]["score"]))
        else:
            scores = [hourly[h]["score"] for h in range(24)]
            avg_score = round(sum(scores) / len(scores), 1)
            source = "baseline"
            peak_hour = max(range(24), key=lambda h: hourly[h]["score"])

        patterns.append({
            "day": DAY_LABELS[dow],
            "avg_score": avg_score,
            "peak_hour": peak_hour,
            "data_source": source,
        })
    return patterns


async def get_best_time(pool, station_id: str) -> Optional[dict]:
    hourly = await get_hourly_prediction(pool, station_id)
    if not hourly:
        return None

    scores = [h["crowd_score"] for h in hourly if h["crowd_score"] is not None]
    if not scores:
        return {
            "station_id": resolve_station_id(station_id),
            "best_window_start": None,
            "best_window_end": None,
            "best_window_label": "Unavailable",
            "avg_score_in_window": None,
            "reason": "Insufficient live or historical data to recommend a visit window.",
        }

    best_start, best_avg = 0, float("inf")
    for start in range(22):
        window = hourly[start:start + 3]
        window_scores = [h["crowd_score"] for h in window if h["crowd_score"] is not None]
        if not window_scores:
            continue
        avg = sum(window_scores) / len(window_scores)
        if avg < best_avg:
            best_avg, best_start = avg, start

    if best_avg == float("inf"):
        best_start, best_avg = 0, float(scores[0])

    best_end = best_start + 2
    return {
        "station_id": resolve_station_id(station_id),
        "best_window_start": best_start,
        "best_window_end": best_end,
        "best_window_label": f"{HOUR_LABELS[best_start]} – {HOUR_LABELS[best_end]}",
        "avg_score_in_window": round(best_avg, 1),
        "reason": f"Lowest predicted crowd ({round(best_avg)}% capacity) during this window today.",
    }
