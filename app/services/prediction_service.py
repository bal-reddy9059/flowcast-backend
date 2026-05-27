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


def predict_traffic_congestion(
    location: str,
    target_hour: int,
    db: Session,
) -> dict:
    """Predict congestion for a location at a specific hour of day.

    Queries the last 30 days of traffic records, filters to the target
    hour-of-day, and returns the most common congestion level with a
    confidence score based on sample size and agreement ratio.
    """
    since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)

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
        logger.info("No historical data for %s — returning default prediction", location)
        return {
            "location": location,
            "target_hour": target_hour,
            "predicted_congestion": "medium",
            "confidence_score": 0.0,
            "sample_size": 0,
            "congestion_distribution": {},
            "message": "No historical data available — defaulting to medium",
        }

    hour_records = [r for r in records if r.created_at.hour == target_hour]

    if not hour_records:
        hour_records = records
        logger.debug("No hour-%s data for %s — using all %s records", target_hour, location, len(records))

    _LEVEL_MAP = {"very_high": "high", "very_low": "low", "none": "low"}
    normalized = [
        _LEVEL_MAP.get(r.congestion_level, r.congestion_level)
        if r.congestion_level not in ("low", "medium", "high")
        else r.congestion_level
        for r in hour_records
    ]
    counts = Counter(normalized)
    predicted = counts.most_common(1)[0][0]
    total = len(hour_records)
    agreement = counts[predicted] / total

    if total >= MIN_RECORDS_HIGH_CONFIDENCE:
        confidence = round(0.7 + agreement * 0.3, 2)
    elif total >= MIN_RECORDS_MEDIUM_CONFIDENCE:
        confidence = round(0.4 + agreement * 0.3, 2)
    else:
        confidence = round(0.1 + agreement * 0.3, 2)

    logger.info(
        "Prediction for %s at hour %s: %s (confidence=%.2f, n=%s)",
        location, target_hour, predicted, confidence, total,
    )

    return {
        "location": location,
        "target_hour": target_hour,
        "predicted_congestion": predicted,
        "confidence_score": min(confidence, 1.0),
        "sample_size": total,
        "congestion_distribution": dict(counts),
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
