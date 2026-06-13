"""
Driver behavior scoring service.

Scoring model (deductions from 100 per event per day):
  - speeding   (high)       : -8 pts
  - speeding   (medium)     : -4 pts
  - speeding   (low)        : -2 pts
  - harsh_braking (high)    : -6 pts
  - harsh_braking (medium)  : -3 pts
  - harsh_acceleration      : -3 pts
  - idle > 10 min           : -1 pt per 10 min
  - route_deviation          : -2 pts

Grades: A ≥ 90 | B ≥ 75 | C ≥ 60 | D ≥ 45 | F < 45
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_EVENT_DEDUCTIONS = {
    ("speeding", "high"):            8,
    ("speeding", "medium"):          4,
    ("speeding", "low"):             2,
    ("harsh_braking", "high"):       6,
    ("harsh_braking", "medium"):     3,
    ("harsh_braking", "low"):        2,
    ("harsh_acceleration", "high"):  4,
    ("harsh_acceleration", "medium"): 3,
    ("harsh_acceleration", "low"):   1,
    ("route_deviation", None):       2,
}
_IDLE_DEDUCTION_PER_10MIN = 1.0


def _grade(score: float) -> str:
    if score >= 90: return "A"
    if score >= 75: return "B"
    if score >= 60: return "C"
    if score >= 45: return "D"
    return "F"


def compute_daily_score(logs: list) -> dict:
    """
    Given a list of DriverBehaviorLog ORM objects for one vehicle/day,
    return a dict with score, grade, and breakdown counts.
    """
    deductions   = 0.0
    speeding     = 0
    harsh_brake  = 0
    harsh_accel  = 0
    idle_min     = 0.0
    deviation    = 0

    for log in logs:
        evt = log.event_type
        sev = log.severity

        if evt == "speeding":
            speeding += 1
            deductions += _EVENT_DEDUCTIONS.get(("speeding", sev), 2)
        elif evt == "harsh_braking":
            harsh_brake += 1
            deductions += _EVENT_DEDUCTIONS.get(("harsh_braking", sev), 3)
        elif evt == "harsh_acceleration":
            harsh_accel += 1
            deductions += _EVENT_DEDUCTIONS.get(("harsh_acceleration", sev), 3)
        elif evt == "idle":
            # details may contain "minutes:15"
            mins = _parse_idle_minutes(log.details)
            idle_min += mins
            deductions += (mins // 10) * _IDLE_DEDUCTION_PER_10MIN
        elif evt == "route_deviation":
            deviation += 1
            deductions += _EVENT_DEDUCTIONS.get(("route_deviation", None), 2)

    score = max(0.0, round(100.0 - deductions, 1))
    return {
        "score":               score,
        "grade":               _grade(score),
        "speeding_count":      speeding,
        "harsh_braking_count": harsh_brake,
        "harsh_accel_count":   harsh_accel,
        "idle_minutes":        idle_min,
        "deviation_count":     deviation,
        "total_events":        len(logs),
    }


def _parse_idle_minutes(details: Optional[str]) -> float:
    if not details:
        return 10.0
    try:
        for part in details.split():
            if part.startswith("minutes:"):
                return float(part.split(":")[1])
    except Exception:
        pass
    return 10.0


def score_summary_for_vehicle(vehicle_id: str, db, days: int = 7) -> dict:
    """Return score trend for a vehicle over the last N days."""
    from app.models.driver_behavior import DriverDailyScore
    since = datetime.now(timezone.utc) - timedelta(days=days)
    scores = (
        db.query(DriverDailyScore)
        .filter(
            DriverDailyScore.vehicle_id == uuid.UUID(vehicle_id),
            DriverDailyScore.score_date >= since,
        )
        .order_by(DriverDailyScore.score_date.asc())
        .all()
    )

    if not scores:
        return {"vehicle_id": vehicle_id, "days": days, "scores": [], "avg_score": None, "trend": "no_data"}

    rows = [
        {
            "date":   s.score_date.strftime("%Y-%m-%d"),
            "score":  s.score,
            "grade":  s.grade,
            "events": s.total_events,
        }
        for s in scores
    ]
    avg = round(sum(r["score"] for r in rows) / len(rows), 1)
    trend = "stable"
    if len(rows) >= 2:
        delta = rows[-1]["score"] - rows[0]["score"]
        if delta >= 5:   trend = "improving"
        elif delta <= -5: trend = "declining"

    return {
        "vehicle_id": vehicle_id,
        "days":       days,
        "avg_score":  avg,
        "avg_grade":  _grade(avg),
        "trend":      trend,
        "scores":     rows,
    }


async def run_daily_scoring(db) -> int:
    """
    Score all vehicles for yesterday. Idempotent — skips if score already exists.
    Called by background task in main.py. Returns count of vehicles scored.
    """
    from app.models.driver_behavior import DriverBehaviorLog, DriverDailyScore
    from app.models.fleet import FleetVehicle

    yesterday_start = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_end = yesterday_start + timedelta(days=1)

    vehicles = db.query(FleetVehicle).filter(FleetVehicle.is_active == True).all()
    scored = 0

    for v in vehicles:
        # Skip if already scored
        existing = db.query(DriverDailyScore).filter(
            DriverDailyScore.vehicle_id == v.id,
            DriverDailyScore.score_date == yesterday_start,
        ).first()
        if existing:
            continue

        logs = db.query(DriverBehaviorLog).filter(
            DriverBehaviorLog.vehicle_id == v.id,
            DriverBehaviorLog.recorded_at >= yesterday_start,
            DriverBehaviorLog.recorded_at < yesterday_end,
        ).all()

        result = compute_daily_score(logs)
        row = DriverDailyScore(
            vehicle_id=v.id,
            org_id=v.org_id,
            score_date=yesterday_start,
            **result,
        )
        db.add(row)
        scored += 1

    if scored:
        db.commit()
        logger.info("Daily scoring complete: %d vehicles scored for %s",
                    scored, yesterday_start.strftime("%Y-%m-%d"))
    return scored
