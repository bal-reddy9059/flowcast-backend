"""
Seed script — populates the database with 30 days of realistic Hyderabad traffic data.
Run once:  python seed_data.py
"""

import random
import sys
from datetime import datetime, timedelta

from app.database import SessionLocal, engine
from app.models.predictor import Incident, PredictionResult, TrafficRecord

# ── Hyderabad locations with real coordinates ─────────────────────────────────
LOCATIONS = [
    {"name": "Hitech City",      "lat": 17.4486, "lng": 78.3908, "road_type": "arterial"},
    {"name": "Gachibowli",       "lat": 17.4401, "lng": 78.3489, "road_type": "arterial"},
    {"name": "Madhapur",         "lat": 17.4504, "lng": 78.3908, "road_type": "arterial"},
    {"name": "Banjara Hills",    "lat": 17.4156, "lng": 78.4485, "road_type": "residential"},
    {"name": "Jubilee Hills",    "lat": 17.4324, "lng": 78.4073, "road_type": "residential"},
    {"name": "Kondapur",         "lat": 17.4706, "lng": 78.3487, "road_type": "arterial"},
    {"name": "Kukatpally",       "lat": 17.4848, "lng": 78.4138, "road_type": "arterial"},
    {"name": "LB Nagar",         "lat": 17.3490, "lng": 78.5480, "road_type": "arterial"},
    {"name": "Secunderabad",     "lat": 17.4399, "lng": 78.4983, "road_type": "arterial"},
    {"name": "Ameerpet",         "lat": 17.4375, "lng": 78.4483, "road_type": "arterial"},
    {"name": "KPHB Colony",      "lat": 17.4914, "lng": 78.3942, "road_type": "residential"},
    {"name": "Mehdipatnam",      "lat": 17.3956, "lng": 78.4307, "road_type": "arterial"},
    {"name": "Begumpet",         "lat": 17.4402, "lng": 78.4687, "road_type": "arterial"},
    {"name": "Dilsukhnagar",     "lat": 17.3688, "lng": 78.5271, "road_type": "arterial"},
    {"name": "Miyapur",          "lat": 17.4964, "lng": 78.3376, "road_type": "residential"},
]

INCIDENTS_DATA = [
    {"location": "Hitech City",   "lat": 17.4490, "lng": 78.3910, "type": "accident",  "severity": "moderate", "desc": "Multi-vehicle collision near Cyber Towers junction"},
    {"location": "Ameerpet",      "lat": 17.4372, "lng": 78.4480, "type": "roadwork",  "severity": "minor",    "desc": "Metro construction causing lane closure"},
    {"location": "Kukatpally",    "lat": 17.4845, "lng": 78.4140, "type": "closure",   "severity": "severe",   "desc": "Flyover maintenance — one lane blocked"},
    {"location": "LB Nagar",      "lat": 17.3492, "lng": 78.5478, "type": "accident",  "severity": "minor",    "desc": "Two-wheeler accident near LB Nagar X roads"},
    {"location": "Secunderabad",  "lat": 17.4400, "lng": 78.4985, "type": "event",     "severity": "moderate", "desc": "Public event causing heavy pedestrian flow"},
    {"location": "Mehdipatnam",   "lat": 17.3954, "lng": 78.4305, "type": "roadwork",  "severity": "moderate", "desc": "Water pipeline work — right lane closed"},
    {"location": "Gachibowli",    "lat": 17.4405, "lng": 78.3491, "type": "accident",  "severity": "severe",   "desc": "Truck overturned near Gachibowli stadium"},
]


def congestion_for_hour(hour: int, is_weekend: bool) -> tuple[str, int, float]:
    """Return (congestion_level, vehicle_count, avg_speed) for a given hour."""
    if is_weekend:
        # Weekends: lighter morning rush, heavier evening
        if 10 <= hour <= 13:
            level, base_vehicles, base_speed = "medium", 520, 28.0
        elif 17 <= hour <= 21:
            level, base_vehicles, base_speed = "high", 820, 14.0
        elif 0 <= hour <= 5:
            level, base_vehicles, base_speed = "low", 80, 52.0
        else:
            level, base_vehicles, base_speed = "low", 220, 42.0
    else:
        # Weekdays: classic double-peak pattern
        if 8 <= hour <= 10:
            level, base_vehicles, base_speed = "high",   900, 12.0
        elif 17 <= hour <= 19:
            level, base_vehicles, base_speed = "high",   950, 10.0
        elif 11 <= hour <= 16:
            level, base_vehicles, base_speed = "medium", 540, 26.0
        elif 7 == hour or 20 == hour:
            level, base_vehicles, base_speed = "medium", 480, 30.0
        elif 0 <= hour <= 5:
            level, base_vehicles, base_speed = "low",    90,  55.0
        else:
            level, base_vehicles, base_speed = "low",   280,  44.0

    jitter_v = random.randint(-80, 80)
    jitter_s = random.uniform(-4.0, 4.0)
    vehicles = max(20, base_vehicles + jitter_v)
    speed    = round(max(5.0, base_speed + jitter_s), 1)
    return level, vehicles, speed


def seed():
    db = SessionLocal()
    try:
        existing = db.query(TrafficRecord).count()
        if existing > 0:
            print(f"[SKIP] Database already has {existing} traffic records. Delete them first to re-seed.")
            return

        print("Seeding traffic records for 30 days × 15 locations × 24 hours …")
        now   = datetime.utcnow()
        start = now - timedelta(days=30)

        records = []
        day = start
        while day <= now:
            is_weekend = day.weekday() >= 5
            for loc in LOCATIONS:
                for hour in range(24):
                    ts = day.replace(hour=hour, minute=random.randint(0, 59), second=0, microsecond=0)
                    if ts > now:
                        continue
                    level, vehicles, speed = congestion_for_hour(hour, is_weekend)
                    records.append(TrafficRecord(
                        location       = loc["name"],
                        latitude       = loc["lat"]  + random.uniform(-0.001, 0.001),
                        longitude      = loc["lng"]  + random.uniform(-0.001, 0.001),
                        vehicle_count  = vehicles,
                        average_speed  = speed,
                        congestion_level = level,
                        road_type      = loc["road_type"],
                        timestamp      = ts,
                        created_at     = ts,
                    ))

            day += timedelta(days=1)

        # Bulk insert in chunks of 500
        chunk = 500
        for i in range(0, len(records), chunk):
            db.bulk_save_objects(records[i : i + chunk])
            db.commit()
            print(f"  inserted {min(i + chunk, len(records))} / {len(records)} records …")

        print(f"\n✓ Inserted {len(records)} traffic records")

        # ── Incidents ─────────────────────────────────────────────────────────
        print("Seeding incidents …")
        for inc in INCIDENTS_DATA:
            db.add(Incident(
                location      = inc["location"],
                latitude      = inc["lat"],
                longitude     = inc["lng"],
                incident_type = inc["type"],
                severity      = inc["severity"],
                description   = inc["desc"],
                is_active     = True,
                reported_at   = now - timedelta(hours=random.randint(1, 6)),
            ))
        db.commit()
        print(f"✓ Inserted {len(INCIDENTS_DATA)} incidents")

        # ── Prediction results (last 7 days, every 3 hours) ───────────────────
        print("Seeding prediction results …")
        preds = []
        for loc in LOCATIONS:
            for days_back in range(7):
                base_dt = now - timedelta(days=days_back)
                for hour in [0, 3, 6, 9, 12, 15, 18, 21]:
                    pred_for = base_dt.replace(hour=hour, minute=0, second=0, microsecond=0)
                    is_weekend = pred_for.weekday() >= 5
                    level, _, _ = congestion_for_hour(hour, is_weekend)
                    confidence = round(random.uniform(0.65, 0.95), 2)
                    preds.append(PredictionResult(
                        location            = loc["name"],
                        predicted_congestion = level,
                        confidence_score    = confidence,
                        prediction_for      = pred_for,
                        model_version       = "v1.0-statistical",
                        is_active           = True,
                    ))
        db.bulk_save_objects(preds)
        db.commit()
        print(f"✓ Inserted {len(preds)} prediction results")

        print("\n✅ Seed complete! Try GET /api/v1/traffic/records?location=Hitech City&limit=10")

    except Exception as e:
        db.rollback()
        print(f"❌ Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
