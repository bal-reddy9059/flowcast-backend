"""
seed.py  –  Run once to populate traffic-data with realistic sample data.
Usage:   python seed.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta
import random
from app.database import SessionLocal, Base, engine
from app.models.predictor import TrafficRecord, PredictionResult, Incident

# Make sure tables exist
Base.metadata.create_all(bind=engine)

LOCATIONS = [
    ("Anna Salai, Chennai",        13.0674,  80.2576),
    ("T. Nagar, Chennai",          13.0418,  80.2341),
    ("Velachery, Chennai",         12.9816,  80.2209),
    ("OMR, Chennai",               12.9010,  80.2279),
    ("Guindy, Chennai",            13.0067,  80.2206),
    ("Tambaram, Chennai",          12.9249,  80.1000),
    ("Adyar, Chennai",             13.0012,  80.2565),
    ("Porur, Chennai",             13.0382,  80.1567),
]

ROAD_TYPES   = ["arterial", "highway", "residential", "collector"]
INCIDENT_TYPES = ["accident", "roadwork", "closure", "waterlogging"]
SEVERITIES   = ["minor", "moderate", "severe"]
CONGESTION   = ["low", "medium", "high"]

def random_record(location_tuple, hours_ago: float) -> TrafficRecord:
    loc, lat, lon = location_tuple
    hour = (datetime.utcnow() - timedelta(hours=hours_ago)).hour
    # Rush-hour pattern: 8-10am and 5-8pm are busier
    rush = hour in range(8, 11) or hour in range(17, 21)
    vehicles = random.randint(60, 150) if rush else random.randint(10, 60)
    speed    = random.uniform(5, 25)  if rush else random.uniform(30, 70)
    level    = "high" if vehicles > 100 else ("medium" if vehicles > 40 else "low")
    ts       = datetime.utcnow() - timedelta(hours=hours_ago)

    return TrafficRecord(
        location        = loc,
        latitude        = lat + random.uniform(-0.002, 0.002),
        longitude       = lon + random.uniform(-0.002, 0.002),
        vehicle_count   = vehicles,
        average_speed   = round(speed, 1),
        congestion_level= level,
        road_type       = random.choice(ROAD_TYPES),
        timestamp       = ts,
        created_at      = ts,
    )


def main():
    db = SessionLocal()
    try:
        # ── Traffic records: 6 hours × 8 locations × 3 readings/hour = 144 rows
        records = []
        for loc in LOCATIONS:
            for h in [i * 0.33 for i in range(18)]:   # every ~20 min over 6h
                records.append(random_record(loc, hours_ago=h))
        db.bulk_save_objects(records)
        print(f"  + Inserted {len(records)} traffic records")

        # ── Predictions
        predictions = []
        for loc, lat, lon in LOCATIONS:
            for offset in range(1, 4):    # next 3 hours
                predictions.append(PredictionResult(
                    location           = loc,
                    predicted_congestion = random.choice(CONGESTION),
                    confidence_score   = round(random.uniform(0.65, 0.97), 2),
                    prediction_for     = datetime.utcnow() + timedelta(hours=offset),
                    model_version      = "v1.0",
                    is_active          = True,
                ))
        db.bulk_save_objects(predictions)
        print(f"  + Inserted {len(predictions)} predictions")

        # ── Incidents
        incidents = [
            Incident(
                location      = "Anna Salai, Chennai",
                latitude      = 13.0674,
                longitude     = 80.2576,
                incident_type = "accident",
                severity      = "moderate",
                description   = "Two-vehicle collision near LIC building. Right lane blocked.",
                is_active     = True,
            ),
            Incident(
                location      = "OMR, Chennai",
                latitude      = 12.9010,
                longitude     = 80.2279,
                incident_type = "roadwork",
                severity      = "minor",
                description   = "Metro rail construction: single-lane traffic near Perungudi.",
                is_active     = True,
            ),
            Incident(
                location      = "Tambaram, Chennai",
                latitude      = 12.9249,
                longitude     = 80.1000,
                incident_type = "waterlogging",
                severity      = "severe",
                description   = "Heavy rain flooding near Tambaram railway station underpass.",
                is_active     = True,
            ),
        ]
        db.bulk_save_objects(incidents)
        print(f"  + Inserted {len(incidents)} incidents")

        db.commit()
        print("\n[OK] Seed complete — traffic-data database populated.")

    except Exception as e:
        db.rollback()
        print(f"[ERROR] Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
