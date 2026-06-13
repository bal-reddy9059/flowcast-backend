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

MUMBAI_LOCATIONS = [
    {"name": "Bandra Kurla Complex", "lat": 19.0660, "lng": 72.8676, "road_type": "arterial"},
    {"name": "Andheri West",         "lat": 19.1197, "lng": 72.8468, "road_type": "arterial"},
    {"name": "Dadar",                "lat": 19.0178, "lng": 72.8478, "road_type": "arterial"},
    {"name": "Thane",                "lat": 19.2183, "lng": 72.9781, "road_type": "arterial"},
    {"name": "Powai",                "lat": 19.1176, "lng": 72.9060, "road_type": "arterial"},
    {"name": "Worli Sea Link",       "lat": 19.0176, "lng": 72.8146, "road_type": "highway"},
    {"name": "Marine Drive, Mumbai", "lat": 18.9437, "lng": 72.8233, "road_type": "arterial"},
]

BANGALORE_LOCATIONS = [
    {"name": "MG Road Bengaluru",   "lat": 12.9758, "lng": 77.6082, "road_type": "arterial"},
    {"name": "Koramangala",         "lat": 12.9352, "lng": 77.6245, "road_type": "residential"},
    {"name": "Indiranagar",         "lat": 12.9784, "lng": 77.6408, "road_type": "residential"},
    {"name": "Whitefield",          "lat": 12.9698, "lng": 77.7500, "road_type": "arterial"},
    {"name": "Electronic City",     "lat": 12.8399, "lng": 77.6770, "road_type": "arterial"},
    {"name": "Silk Board Junction", "lat": 12.9174, "lng": 77.6228, "road_type": "arterial"},
    {"name": "Hebbal Flyover",      "lat": 13.0450, "lng": 77.5966, "road_type": "highway"},
]

CHENNAI_LOCATIONS = [
    {"name": "Anna Nagar",          "lat": 13.0850, "lng": 80.2101, "road_type": "residential"},
    {"name": "T Nagar Chennai",     "lat": 13.0418, "lng": 80.2341, "road_type": "arterial"},
    {"name": "OMR Road Chennai",    "lat": 12.8996, "lng": 80.2209, "road_type": "arterial"},
    {"name": "Guindy",              "lat": 13.0067, "lng": 80.2206, "road_type": "arterial"},
    {"name": "Tambaram",            "lat": 12.9249, "lng": 80.1000, "road_type": "arterial"},
    {"name": "Anna Salai, Chennai", "lat": 13.0524, "lng": 80.2494, "road_type": "arterial"},
]

DELHI_LOCATIONS = [
    {"name": "Connaught Place",     "lat": 28.6315, "lng": 77.2167, "road_type": "arterial"},
    {"name": "Lajpat Nagar",        "lat": 28.5673, "lng": 77.2378, "road_type": "arterial"},
    {"name": "Dwarka",              "lat": 28.5921, "lng": 77.0460, "road_type": "residential"},
    {"name": "Rohini",              "lat": 28.7041, "lng": 77.1025, "road_type": "residential"},
    {"name": "Cyber City Gurgaon",  "lat": 28.4952, "lng": 77.0928, "road_type": "arterial"},
    {"name": "Noida Sector 18",     "lat": 28.5699, "lng": 77.3211, "road_type": "arterial"},
]

PUNE_LOCATIONS = [
    {"name": "Koregaon Park",       "lat": 18.5363, "lng": 73.8938, "road_type": "residential"},
    {"name": "Hinjewadi",           "lat": 18.5904, "lng": 73.7380, "road_type": "arterial"},
    {"name": "Kothrud",             "lat": 18.5074, "lng": 73.8077, "road_type": "arterial"},
]

INCIDENTS_DATA = [
    {"location": "Hitech City",        "lat": 17.4490, "lng": 78.3910, "type": "accident",  "severity": "moderate", "desc": "Multi-vehicle collision near Cyber Towers junction"},
    {"location": "Ameerpet",           "lat": 17.4372, "lng": 78.4480, "type": "roadwork",  "severity": "minor",    "desc": "Metro construction causing lane closure"},
    {"location": "Kukatpally",         "lat": 17.4845, "lng": 78.4140, "type": "closure",   "severity": "severe",   "desc": "Flyover maintenance — one lane blocked"},
    {"location": "LB Nagar",           "lat": 17.3492, "lng": 78.5478, "type": "accident",  "severity": "minor",    "desc": "Two-wheeler accident near LB Nagar X roads"},
    {"location": "Secunderabad",       "lat": 17.4400, "lng": 78.4985, "type": "event",     "severity": "moderate", "desc": "Public event causing heavy pedestrian flow"},
    {"location": "Mehdipatnam",        "lat": 17.3954, "lng": 78.4305, "type": "roadwork",  "severity": "moderate", "desc": "Water pipeline work — right lane closed"},
    {"location": "Gachibowli",         "lat": 17.4405, "lng": 78.3491, "type": "accident",  "severity": "severe",   "desc": "Truck overturned near Gachibowli stadium"},
    {"location": "Bandra Kurla Complex","lat": 19.0660, "lng": 72.8676, "type": "accident",  "severity": "moderate", "desc": "Rear-end collision near BKC signal"},
    {"location": "Worli Sea Link",     "lat": 19.0176, "lng": 72.8146, "type": "event",     "severity": "minor",    "desc": "Marathon event — partial lane closure"},
    {"location": "Thane",              "lat": 19.2183, "lng": 72.9781, "type": "roadwork",  "severity": "moderate", "desc": "Flyover repair work near Thane station"},
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


def _seed_locations(db, locations: list[dict], now: datetime, label: str) -> int:
    """Seed 30 days of hourly traffic records for a list of locations.

    Skips any location that already has records so the function is safe to
    call multiple times without duplicating data.
    """
    # A location is considered fresh if it has records in ≥20 distinct hours
    # within the last 7 days. Older seed data (>7d) is stale and gets re-seeded.
    from sqlalchemy import func, extract
    week_ago = now - timedelta(days=7)
    already_seeded = set()
    for loc in locations:
        distinct_hours = (
            db.query(func.count(func.distinct(extract("hour", TrafficRecord.created_at))))
            .filter(
                TrafficRecord.location == loc["name"],
                TrafficRecord.created_at >= week_ago,
            )
            .scalar()
        )
        if (distinct_hours or 0) >= 20:
            already_seeded.add(loc["name"])
    to_seed = [loc for loc in locations if loc["name"] not in already_seeded]
    if not to_seed:
        print(f"[SKIP] All {label} locations already have records.")
        return 0

    print(f"Seeding {label}: {len(to_seed)} locations × 30 days × 24 hours …")
    start = now - timedelta(days=30)
    records = []
    day = start
    while day <= now:
        is_weekend = day.weekday() >= 5
        for loc in to_seed:
            for hour in range(24):
                ts = day.replace(hour=hour, minute=random.randint(0, 59), second=0, microsecond=0)
                if ts > now:
                    continue
                level, vehicles, speed = congestion_for_hour(hour, is_weekend)
                records.append(TrafficRecord(
                    location         = loc["name"],
                    latitude         = loc["lat"] + random.uniform(-0.001, 0.001),
                    longitude        = loc["lng"] + random.uniform(-0.001, 0.001),
                    vehicle_count    = vehicles,
                    average_speed    = speed,
                    congestion_level = level,
                    road_type        = loc["road_type"],
                    timestamp        = ts,
                    created_at       = ts,
                ))
        day += timedelta(days=1)

    chunk = 500
    for i in range(0, len(records), chunk):
        db.bulk_save_objects(records[i : i + chunk])
        db.commit()
        print(f"  {label}: inserted {min(i + chunk, len(records))} / {len(records)} …")

    print(f"[OK] {label}: inserted {len(records)} traffic records")
    return len(records)


def seed():
    db = SessionLocal()
    try:
        now = datetime.utcnow()

        # ── Traffic records (per-city, skip fresh locations, re-seed stale) ────
        total = 0
        total += _seed_locations(db, LOCATIONS,           now, "Hyderabad")
        total += _seed_locations(db, MUMBAI_LOCATIONS,    now, "Mumbai")
        total += _seed_locations(db, BANGALORE_LOCATIONS, now, "Bangalore")
        total += _seed_locations(db, CHENNAI_LOCATIONS,   now, "Chennai")
        total += _seed_locations(db, DELHI_LOCATIONS,     now, "Delhi")
        total += _seed_locations(db, PUNE_LOCATIONS,      now, "Pune")
        print(f"\n[OK] Total traffic records inserted: {total}")

        # ── Incidents (skip if already present for that location) ─────────────
        print("Seeding incidents …")
        added = 0
        for inc in INCIDENTS_DATA:
            exists = db.query(Incident).filter(Incident.location == inc["location"]).first()
            if exists:
                continue
            reported_at = now - timedelta(hours=random.randint(1, 6))
            db.add(Incident(
                location      = inc["location"],
                latitude      = inc["lat"],
                longitude     = inc["lng"],
                incident_type = inc["type"],
                severity      = inc["severity"],
                description   = inc["desc"],
                reported_by   = "system",
                upvotes       = 1,
                downvotes     = 0,
                is_active     = True,
                reported_at   = reported_at,
                expires_at    = reported_at + timedelta(hours=24),
            ))
            added += 1
        db.commit()
        print(f"[OK] Inserted {added} incidents (skipped {len(INCIDENTS_DATA) - added} duplicates)")

        # ── Prediction results (last 7 days, every 3 hours) ───────────────────
        print("Seeding prediction results …")
        preds = []
        all_locs = LOCATIONS + MUMBAI_LOCATIONS + BANGALORE_LOCATIONS + CHENNAI_LOCATIONS + DELHI_LOCATIONS + PUNE_LOCATIONS
        for loc in all_locs:
            for days_back in range(7):
                base_dt = now - timedelta(days=days_back)
                for hour in [0, 3, 6, 9, 12, 15, 18, 21]:
                    pred_for = base_dt.replace(hour=hour, minute=0, second=0, microsecond=0)
                    is_weekend = pred_for.weekday() >= 5
                    level, _, _ = congestion_for_hour(hour, is_weekend)
                    confidence = round(random.uniform(0.65, 0.95), 2)
                    preds.append(PredictionResult(
                        location             = loc["name"],
                        predicted_congestion = level,
                        confidence_score     = confidence,
                        prediction_for       = pred_for,
                        model_version        = "v1.0-statistical",
                        is_active            = True,
                    ))
        db.bulk_save_objects(preds)
        db.commit()
        print(f"[OK] Inserted {len(preds)} prediction results")

        print("\n[DONE] Seed complete!")

    except Exception as e:
        db.rollback()
        print(f"[ERROR] Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
