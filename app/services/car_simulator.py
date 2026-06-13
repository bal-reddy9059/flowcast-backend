"""Live car simulator — maintains simulated vehicle positions on India road segments."""

import logging
import math
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from math import cos, radians

logger = logging.getLogger(__name__)

CAR_LIMIT = 200
_HEADING_BY_ROAD: dict[str, list[float]] = {
    "highway":     [0.0, 90.0, 180.0, 270.0],
    "arterial":    [45.0, 135.0, 225.0, 315.0],
    "residential": list(range(0, 360, 30)),
}
_CLAMP_RADIUS = 0.015  # degrees (~1.6 km)


@dataclass
class SimulatedCar:
    id: str
    location: str
    lat: float
    lng: float
    speed_kmh: float
    heading: float
    congestion_level: str
    anchor_lat: float
    anchor_lng: float


class CarSimulator:
    def __init__(self) -> None:
        self._cars: dict[str, SimulatedCar] = {}
        self._initialized: bool = False

    # ── Initialization ────────────────────────────────────────────────────────

    def initialize_from_locations(self) -> None:
        from app.database import SessionLocal
        from app.models.predictor import TrafficRecord
        from app.services.india_locations import INDIA_LOCATIONS
        from app.services.realtime_collector import _simulate_flow

        db = SessionLocal()
        try:
            location_data: list[tuple[dict, float, float, str]] = []
            for loc in INDIA_LOCATIONS:
                record = (
                    db.query(TrafficRecord)
                    .filter(TrafficRecord.location == loc["name"])
                    .order_by(TrafficRecord.created_at.desc())
                    .first()
                )
                if record and record.vehicle_count:
                    vehicle_count = float(record.vehicle_count)
                    speed = float(record.average_speed or 35)
                    congestion = record.congestion_level or "medium"
                else:
                    flow = _simulate_flow(loc["lat"], loc["lng"])
                    cur_speed = float(flow.get("currentSpeed", 35))
                    free_speed = float(flow.get("freeFlowSpeed", 60))
                    ratio = cur_speed / max(free_speed, 1)
                    vehicle_count = float(max(50, int((1 - ratio) * 2000 + random.uniform(-100, 100))))
                    speed = cur_speed
                    congestion = "high" if ratio < 0.5 else ("medium" if ratio < 0.75 else "low")
                location_data.append((loc, vehicle_count, speed, congestion))
        finally:
            db.close()

        total_vehicles = sum(d[1] for d in location_data) or 1.0
        cars: list[SimulatedCar] = []
        for loc, vehicle_count, speed, congestion in location_data:
            n = max(1, round(CAR_LIMIT * vehicle_count / total_vehicles))
            for _ in range(n):
                if len(cars) >= CAR_LIMIT:
                    break
                cars.append(self._make_car(loc, speed, congestion))
            if len(cars) >= CAR_LIMIT:
                break

        self._cars = {c.id: c for c in cars}
        self._initialized = True
        logger.info("CarSimulator initialised with %d cars across %d locations", len(self._cars), len(location_data))

    def _make_car(self, loc: dict, speed_kmh: float, congestion: str) -> SimulatedCar:
        jitter_lat = random.uniform(-0.004, 0.004)
        jitter_lng = random.uniform(-0.004, 0.004)
        headings = _HEADING_BY_ROAD.get(loc.get("road_type", "arterial"), [0.0, 90.0, 180.0, 270.0])
        heading = random.choice(headings) + random.uniform(-5, 5)
        return SimulatedCar(
            id=f"car-{uuid.uuid4().hex[:8]}",
            location=loc["name"],
            lat=loc["lat"] + jitter_lat,
            lng=loc["lng"] + jitter_lng,
            speed_kmh=max(1.0, speed_kmh + random.uniform(-5, 5)),
            heading=heading % 360,
            congestion_level=congestion,
            anchor_lat=loc["lat"],
            anchor_lng=loc["lng"],
        )

    # ── Tick (called every 2 seconds) ─────────────────────────────────────────

    def tick(self) -> None:
        dt = 2.0
        for car in self._cars.values():
            speed = car.speed_kmh
            h_rad = radians(car.heading)
            delta_lat = (speed / 3600) * dt / 111.0
            delta_lng = (speed / 3600) * dt / (111.0 * max(cos(radians(car.lat)), 0.001))
            new_lat = car.lat + delta_lat * cos(h_rad)
            new_lng = car.lng + delta_lng * math.sin(h_rad)

            # Clamp and bounce heading if out of bounds
            if abs(new_lat - car.anchor_lat) > _CLAMP_RADIUS:
                car.heading = (car.heading + 180) % 360
                new_lat = car.lat
            if abs(new_lng - car.anchor_lng) > _CLAMP_RADIUS:
                car.heading = (360 - car.heading) % 360
                new_lng = car.lng

            car.lat = new_lat
            car.lng = new_lng
            car.heading = (car.heading + random.uniform(-0.3, 0.3)) % 360

    # ── DB refresh (every 30 min) ─────────────────────────────────────────────

    def refresh_from_db(self) -> None:
        from app.database import SessionLocal
        from app.models.predictor import TrafficRecord

        db = SessionLocal()
        try:
            # Build location → (speed, congestion) map from latest DB records
            location_map: dict[str, tuple[float, str]] = {}
            for car in self._cars.values():
                if car.location in location_map:
                    continue
                record = (
                    db.query(TrafficRecord)
                    .filter(TrafficRecord.location == car.location)
                    .order_by(TrafficRecord.created_at.desc())
                    .first()
                )
                if record and record.average_speed:
                    location_map[car.location] = (
                        float(record.average_speed),
                        record.congestion_level or "medium",
                    )
            for car in self._cars.values():
                if car.location in location_map:
                    new_speed, new_congestion = location_map[car.location]
                    car.speed_kmh = max(1.0, new_speed + random.uniform(-5, 5))
                    car.congestion_level = new_congestion
        except Exception as exc:
            logger.warning("CarSimulator DB refresh failed: %s", exc)
        finally:
            db.close()

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def get_snapshot(self) -> list[dict]:
        return [
            {
                "id": c.id,
                "location": c.location,
                "lat": round(c.lat, 6),
                "lng": round(c.lng, 6),
                "speed_kmh": round(c.speed_kmh, 1),
                "heading": round(c.heading, 1),
                "congestion_level": c.congestion_level,
            }
            for c in self._cars.values()
        ]


car_simulator = CarSimulator()
