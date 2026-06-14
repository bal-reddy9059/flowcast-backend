"""
ML-based traffic prediction using scikit-learn RandomForestClassifier.

Trains on historical TrafficRecord data (last 60 days). Retrains every 6 hours.
Falls back to rule-based logic when not enough data is available.

Features used:
  - hour_norm        : hour-of-day / 23 (0.0 – 1.0)
  - dow_norm         : day-of-week / 6  (0.0 – 1.0)
  - is_weekend       : 1 if Sat/Sun, else 0
  - is_rush_hour     : 1 if 7-10 AM or 5-8 PM, else 0
  - is_night         : 1 if 10 PM – 5 AM, else 0
  - vehicle_count_n  : vehicle_count / 2000 clamped to [0, 1]
  - speed_n          : average_speed / 80 clamped to [0, 1]
"""

import logging
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import numpy as np
from sklearn.ensemble import RandomForestClassifier

logger = logging.getLogger(__name__)

_LABEL_MAP = {"low": 0, "medium": 1, "high": 2}
_REVERSE_LABEL = {0: "low", 1: "medium", 2: "high"}
_FEATURE_NAMES = [
    "hour_norm", "dow_norm", "is_weekend",
    "is_rush_hour", "is_night",
    "vehicle_count_n", "speed_n",
]


def _hour_label(h: int) -> str:
    if h == 0:  return "12:00 AM"
    if h < 12:  return f"{h}:00 AM"
    if h == 12: return "12:00 PM"
    return f"{h - 12}:00 PM"


def _hour_defaults(hour: int) -> tuple[float, float]:
    """Return realistic (vehicle_count, average_speed) for a given hour of day.

    Used when the caller does not supply explicit traffic values, so predictions
    for 1 AM don't use daytime defaults and come out wrong.
    """
    if hour >= 22 or hour <= 5:   return 70.0,  68.0   # night  — near empty, fast
    if 6  <= hour <= 7:            return 320.0, 48.0   # early morning — building up
    if 8  <= hour <= 10:           return 1700.0, 16.0  # morning rush
    if 11 <= hour <= 16:           return 680.0,  36.0  # mid-day
    if 17 <= hour <= 20:           return 1850.0, 14.0  # evening rush
    return 420.0, 40.0                                  # late evening


def _make_feature_row(hour: int, dow: int,
                      vehicle_count: Optional[float] = None,
                      average_speed: Optional[float] = None) -> list[float]:
    """Build the 7-feature vector. When vehicle_count/average_speed are None,
    hour-appropriate realistic defaults are used instead of flat midday values."""
    vc_def, spd_def = _hour_defaults(hour)
    vc  = vehicle_count if vehicle_count is not None else vc_def
    spd = average_speed if average_speed is not None else spd_def
    return [
        hour / 23.0,
        dow / 6.0,
        1.0 if dow >= 5 else 0.0,
        1.0 if (7 <= hour <= 10 or 17 <= hour <= 20) else 0.0,
        1.0 if (hour >= 22 or hour <= 5) else 0.0,
        min(vc  / 2000.0, 1.0),
        min(spd / 80.0,   1.0),
    ]


class TrafficMLModel:
    """Thread-safe singleton scikit-learn model for congestion prediction."""

    def __init__(self):
        self._model: Optional[RandomForestClassifier] = None
        self._trained_at: Optional[datetime] = None
        self._retrain_interval = timedelta(hours=6)
        self._lock = threading.Lock()
        self._sample_count = 0
        self._min_samples = 50

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, db) -> bool:
        """Fit the model from the last 60 days of TrafficRecord data.

        Returns True if training succeeded, False if not enough data.
        """
        from app.models.predictor import TrafficRecord

        since = datetime.now(timezone.utc) - timedelta(days=60)
        records = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.created_at >= since,
                TrafficRecord.congestion_level.in_(["low", "medium", "high"]),
            )
            .all()
        )

        X: list[list[float]] = []
        y: list[int] = []

        for r in records:
            if r.created_at is None:
                continue
            ts = r.created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            label = _LABEL_MAP.get(r.congestion_level)
            if label is None:
                continue
            X.append(_make_feature_row(
                ts.hour,
                ts.weekday(),
                float(r.vehicle_count or 500),
                float(r.average_speed or 35),
            ))
            y.append(label)

        if len(X) < self._min_samples:
            logger.warning(
                "ML trainer: only %d samples (need %d) — keeping previous model",
                len(X), self._min_samples,
            )
            return False

        X_arr = np.array(X, dtype=np.float32)
        y_arr = np.array(y, dtype=np.int32)

        clf = RandomForestClassifier(
            n_estimators=150,
            max_depth=10,
            random_state=42,
            class_weight="balanced",
            n_jobs=-1,
        )
        clf.fit(X_arr, y_arr)

        with self._lock:
            self._model = clf
            self._trained_at = datetime.now(timezone.utc)
            self._sample_count = len(X)

        dist = Counter(y_arr.tolist())
        logger.info(
            "ML model trained — samples=%d  low=%d  medium=%d  high=%d",
            len(X), dist.get(0, 0), dist.get(1, 0), dist.get(2, 0),
        )
        return True

    # ── State helpers ─────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return self._model is not None

    def needs_retrain(self) -> bool:
        if self._trained_at is None:
            return True
        return (datetime.now(timezone.utc) - self._trained_at) > self._retrain_interval

    def model_info(self) -> dict:
        return {
            "ready": self.is_ready(),
            "trained_at": self._trained_at.isoformat() if self._trained_at else None,
            "model_type": "RandomForestClassifier(n=150,depth=10)" if self.is_ready() else "none",
            "training_samples": self._sample_count,
            "features": _FEATURE_NAMES,
            "retrain_interval_hours": 6,
        }

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(
        self,
        hour: int,
        dow: int,
        vehicle_count: Optional[float] = None,
        average_speed: Optional[float] = None,
    ) -> dict:
        """Return predicted congestion + probability breakdown."""
        row = _make_feature_row(hour, dow, vehicle_count, average_speed)

        if not self.is_ready():
            return self._rule_based(hour, dow)

        with self._lock:
            proba = self._model.predict_proba([row])[0]
            classes = self._model.classes_

        # Map class indices back to labels
        prob_dict = {_REVERSE_LABEL.get(int(c), "medium"): float(p)
                     for c, p in zip(classes, proba)}
        best_label = max(prob_dict, key=prob_dict.__getitem__)
        confidence = prob_dict[best_label]

        return {
            "predicted_congestion": best_label,
            "confidence": round(confidence, 3),
            "probabilities": {
                "low":    round(prob_dict.get("low",    0.0), 3),
                "medium": round(prob_dict.get("medium", 0.0), 3),
                "high":   round(prob_dict.get("high",   0.0), 3),
            },
            "model": "RandomForest-v2",
        }

    def predict_hours_ahead(
        self,
        base_hour: int,
        base_dow: int,
        vehicle_count: Optional[float] = None,
        average_speed: Optional[float] = None,
        hours_ahead: int = 3,
    ) -> list[dict]:
        """Return predictions for the next N hours.

        vehicle_count/average_speed are re-derived per target hour when None,
        so a forecast from 1 AM correctly uses night defaults at 2 AM, 3 AM, etc.
        """
        results = []
        for offset in range(1, hours_ahead + 1):
            target_hour = (base_hour + offset) % 24
            extra_days  = (base_hour + offset) // 24
            target_dow  = (base_dow + extra_days) % 7

            # When caller passes None, each hour in the forecast gets its own
            # realistic defaults rather than carrying forward a fixed value.
            pred = self.predict(target_hour, target_dow, vehicle_count, average_speed)
            pred["offset_hours"] = offset
            pred["target_hour"]  = target_hour
            pred["time_label"]   = _hour_label(target_hour)
            results.append(pred)
        return results

    # ── Rule-based fallback ───────────────────────────────────────────────────

    def _rule_based(self, hour: int, dow: int) -> dict:
        is_rush    = 7 <= hour <= 10 or 17 <= hour <= 20
        is_night   = hour >= 22 or hour <= 5
        is_weekend = dow >= 5

        if is_night:
            level, conf = "low", 0.85
        elif is_weekend:
            level, conf = ("medium", 0.65) if is_rush else ("low", 0.70)
        elif is_rush:
            level, conf = "high", 0.80
        else:
            level, conf = "medium", 0.60

        return {
            "predicted_congestion": level,
            "confidence": conf,
            "probabilities": {},
            "model": "rule-based-fallback",
        }


# ── Global singleton ──────────────────────────────────────────────────────────

ml_model = TrafficMLModel()
