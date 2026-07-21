"""Traffic prediction service for FlowCast.

Uses historical traffic_records to predict congestion levels
based on hour-of-day patterns for a given location.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.predictor import PredictionResult, TrafficRecord
from app.services.city_aliases import location_filter

logger = logging.getLogger(__name__)

HISTORY_DAYS = 30
MIN_RECORDS_HIGH_CONFIDENCE = 20
MIN_RECORDS_MEDIUM_CONFIDENCE = 5

# Typical India urban rush-hour prior when hour-specific data is missing
_HOUR_PRIOR = {
    7: "medium", 8: "high", 9: "high", 10: "medium",
    11: "low", 12: "low", 13: "low", 14: "low", 15: "medium",
    16: "medium", 17: "high", 18: "high", 19: "high", 20: "medium",
    21: "medium", 22: "low", 23: "low",
    0: "low", 1: "low", 2: "low", 3: "low", 4: "low", 5: "low", 6: "medium",
}


def _record_hour(r: TrafficRecord) -> int | None:
    ts = r.timestamp or r.created_at
    if ts is None:
        return None
    return ts.hour


def _normalize_level(level: str | None) -> str | None:
    if not level:
        return None
    mapped = {"very_high": "high", "very_low": "low", "none": "low"}
    return mapped.get(level, level if level in ("low", "medium", "high") else None)


def _confidence_from_samples(total: int, agreement: float) -> float:
    if total >= MIN_RECORDS_HIGH_CONFIDENCE:
        return round(min(1.0, 0.7 + agreement * 0.3), 2)
    if total >= MIN_RECORDS_MEDIUM_CONFIDENCE:
        return round(min(1.0, 0.4 + agreement * 0.3), 2)
    if total > 0:
        return round(min(1.0, 0.1 + agreement * 0.3), 2)
    return 0.0


def _apply_rush_prior(result: dict, target_hour: int) -> dict:
    """If history is uniformly 'low' but this is a classic rush hour, raise the prior.

    Collectors often store TomTom ratio-based 'low' even during busy periods, which
    made every forecast hour look identical. Soft-override when sample evidence is thin
    or overwhelmingly one-sided during peak windows.
    """
    prior = _HOUR_PRIOR.get(target_hour, "medium")
    predicted = result["predicted_congestion"]
    n = int(result.get("sample_size") or 0)
    priority = {"low": 0, "medium": 1, "high": 2}
    if priority.get(prior, 0) <= priority.get(predicted, 0):
        return result
    # Only bump when data is sparse OR extremely skewed low during rush
    dist = result.get("congestion_distribution") or {}
    low_share = dist.get("low", 0) / max(n, 1)
    if n < 25 or low_share >= 0.85:
        result = dict(result)
        result["predicted_congestion"] = prior
        result["confidence_score"] = round(min(0.55, max(0.2, float(result["confidence_score"]) * 0.7)), 2)
        result["message"] = (
            result.get("message") or "Rush-hour prior applied (history skewed low)"
        )
    return result


def _predict_from_bucket(hour_records: list, target_hour: int, location: str) -> dict | None:
    levels = [_normalize_level(r.congestion_level) for r in hour_records]
    levels = [lvl for lvl in levels if lvl]
    if not levels:
        return None
    counts = Counter(levels)
    predicted = counts.most_common(1)[0][0]
    total = len(levels)
    agreement = counts[predicted] / total
    confidence = _confidence_from_samples(total, agreement)
    return {
        "location": location,
        "target_hour": target_hour,
        "predicted_congestion": predicted,
        "confidence_score": confidence,
        "sample_size": total,
        "congestion_distribution": dict(counts),
    }


def predict_traffic_congestion(
    location: str,
    target_hour: int,
    db: Session,
) -> dict:
    """Predict congestion for a location at a specific hour of day.

    Prefers same hour-of-day samples from the last 30 days. If none exist,
    widens to nearby hours, then falls back to a rush-hour prior — never
    dumps every historical record into every hour (that made forecasts flat).
    """
    since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    target_hour = int(target_hour) % 24

    records = (
        db.query(TrafficRecord)
        .filter(
            location_filter(TrafficRecord.location, location),
            TrafficRecord.timestamp >= since,
            TrafficRecord.congestion_level.isnot(None),
        )
        .all()
    )
    # Fallback to created_at window if timestamp filter returned nothing
    if not records:
        records = (
            db.query(TrafficRecord)
            .filter(
                location_filter(TrafficRecord.location, location),
                TrafficRecord.created_at >= since,
                TrafficRecord.congestion_level.isnot(None),
            )
            .all()
        )

    if not records:
        prior = _HOUR_PRIOR.get(target_hour, "medium")
        logger.info("No historical data for %s — prior=%s hour=%s", location, prior, target_hour)
        return {
            "location": location,
            "target_hour": target_hour,
            "predicted_congestion": prior,
            "confidence_score": 0.15,
            "sample_size": 0,
            "congestion_distribution": {},
            "message": "No historical data — using time-of-day prior",
        }

    by_hour: dict[int, list] = {}
    for r in records:
        h = _record_hour(r)
        if h is None:
            continue
        by_hour.setdefault(h, []).append(r)

    # 1) Exact hour
    if by_hour.get(target_hour):
        result = _predict_from_bucket(by_hour[target_hour], target_hour, location)
        if result:
            return _apply_rush_prior(result, target_hour)

    # 2) Nearby hours (±1, then ±2)
    nearby: list = []
    for delta in (1, 2):
        for h in ((target_hour - delta) % 24, (target_hour + delta) % 24):
            nearby.extend(by_hour.get(h, []))
        if nearby:
            result = _predict_from_bucket(nearby, target_hour, location)
            if result:
                result["confidence_score"] = round(result["confidence_score"] * 0.75, 2)
                result["message"] = f"No direct samples for hour {target_hour} — used nearby hours"
                return _apply_rush_prior(result, target_hour)

    # 3) Time-of-day prior blended with overall majority (not all-hours dump)
    overall = _predict_from_bucket(records, target_hour, location)
    prior = _HOUR_PRIOR.get(target_hour, "medium")
    if overall and overall["sample_size"] >= MIN_RECORDS_MEDIUM_CONFIDENCE:
        # Prefer prior during known rush windows when overall is uniformly low/high
        predicted = prior if prior != overall["predicted_congestion"] else overall["predicted_congestion"]
        # If overall is very skewed, keep overall but lower confidence
        if overall["confidence_score"] >= 0.85 and overall["sample_size"] >= 40:
            predicted = overall["predicted_congestion"]
        return {
            "location": location,
            "target_hour": target_hour,
            "predicted_congestion": predicted,
            "confidence_score": round(min(0.45, overall["confidence_score"] * 0.5), 2),
            "sample_size": overall["sample_size"],
            "congestion_distribution": overall["congestion_distribution"],
            "message": "Sparse hour coverage — blended with time-of-day prior",
        }

    return {
        "location": location,
        "target_hour": target_hour,
        "predicted_congestion": prior,
        "confidence_score": 0.2,
        "sample_size": len(records),
        "congestion_distribution": overall["congestion_distribution"] if overall else {},
        "message": "Using time-of-day prior",
    }


def save_prediction(
    location: str,
    predicted_congestion: str,
    confidence_score: float,
    hours_ahead: int,
    db: Session,
) -> PredictionResult:
    """Persist a prediction result to the prediction_results table."""
    prediction = PredictionResult(
        location=location,
        predicted_congestion=predicted_congestion,
        confidence_score=confidence_score,
        prediction_for=datetime.now(timezone.utc) + timedelta(hours=hours_ahead),
        model_version="v1.0-statistical",
        is_active=True,
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    logger.info("Saved prediction %s for %s: %s", prediction.id, location, predicted_congestion)
    return prediction
