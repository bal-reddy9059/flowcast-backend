"""Live Traffic WebSocket endpoints — car stream, trip ETA tracker, pulse feed."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database import SessionLocal
from app.services.car_simulator import car_simulator
from app.services.eta_service import calculate_eta_for_location

router = APIRouter(tags=["Live Traffic"])
logger = logging.getLogger(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────

_car_sockets: list[WebSocket] = []

# session_id → {origin, destination, distance_km, mode, started_at,
#               websocket, last_eta, last_congestion, last_speed}
_live_sessions: dict[str, dict] = {}

_pulse_sockets: list[WebSocket] = []
# location_name → {congestion_level, average_speed}
_pulse_prev_state: dict[str, dict] = {}


# ── Broadcast helpers (called from main.py background tasks) ──────────────────

async def _broadcast_cars(message: dict) -> None:
    dead = []
    for ws in list(_car_sockets):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _car_sockets:
            _car_sockets.remove(ws)


async def _broadcast_pulse(message: dict) -> None:
    dead = []
    for ws in list(_pulse_sockets):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in _pulse_sockets:
            _pulse_sockets.remove(ws)


# ── User Notification WebSocket (correct URL: /api/v1/ws/{user_id}) ──────────

@router.websocket("/ws/{user_id}")
async def user_notifications_ws(websocket: WebSocket, user_id: str) -> None:
    """User notification WebSocket — real-time push alerts.

    Connect: `ws://<host>/api/v1/ws/{user_id}`

    Pass your user UUID (from login response `user.id`) as `user_id`.
    Receives congestion alerts and departure reminders as they fire.

    Keepalive ping is sent every 30 seconds — respond with `"pong"` to acknowledge.
    """
    from app.services.connection_manager import manager
    await manager.connect(user_id, websocket)
    try:
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to FlowCast alerts",
            "user_id": user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "pong":
                    pass
            except asyncio.TimeoutError:
                await manager.send_ping(user_id)
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id)


# ── Feature 1: Live Car Stream ────────────────────────────────────────────────

@router.websocket("/traffic/ws/live")
async def live_cars_ws(websocket: WebSocket) -> None:
    """Stream all simulated car positions, updated every 2 seconds.

    Connect: `ws://<host>/api/v1/traffic/ws/live`

    First message on connect is a full snapshot. Subsequent messages are full
    updates broadcast every 2 seconds by the background ticker.

    Message format:
    ```json
    {
      "type": "cars_update",
      "timestamp": "2026-05-28T10:00:00+00:00",
      "total": 200,
      "cars": [{"id": "car-abc123", "location": "Gachibowli",
                "lat": 17.4401, "lng": 78.3489,
                "speed_kmh": 32.5, "heading": 90.2,
                "congestion_level": "medium"}]
    }
    ```
    """
    await websocket.accept()
    _car_sockets.append(websocket)
    try:
        if not car_simulator._initialized:
            car_simulator.initialize_from_locations()
        snapshot = car_simulator.get_snapshot()
        await websocket.send_json({
            "type": "cars_update",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": len(snapshot),
            "cars": snapshot,
        })
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _car_sockets:
            _car_sockets.remove(websocket)


# ── Feature 3: Live Trip ETA Tracker ──────────────────────────────────────────

_PLACEHOLDER_VALUES = {"string", "text", "foo", "bar", "example", "test", "origin", "destination", "location", "place"}


class LiveTripStart(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "origin": "Koramangala",
            "destination": "Silk Board",
            "distance_km": 4.8,
            "mode": "driving",
        }
    })

    origin: str = Field(
        ..., min_length=2,
        description="Starting location name (Indian city/neighbourhood)",
    )
    destination: str = Field(
        ..., min_length=2,
        description="Destination location name",
    )
    distance_km: float = Field(
        ..., gt=0,
        description="Trip distance in kilometres (must be > 0)",
    )
    mode: str = Field(
        "driving",
        description="Travel mode — driving | walking | transit",
    )

    @field_validator("origin", "destination")
    @classmethod
    def reject_placeholder_names(cls, v: str) -> str:
        if v.strip().lower() in _PLACEHOLDER_VALUES:
            raise ValueError(
                f"'{v}' is not a valid location. "
                "Use a recognised Indian city/neighbourhood (e.g. 'Koramangala', 'Andheri East')."
            )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        allowed = {"driving", "walking", "transit"}
        if v not in allowed:
            raise ValueError(f"mode must be one of: {', '.join(sorted(allowed))}")
        return v


@router.post("/trips/live/start", status_code=status.HTTP_201_CREATED)
def start_live_trip(payload: LiveTripStart) -> dict:
    """Start a live trip tracking session.

    Returns a `session_id` and the WebSocket URL to connect to for live ETA updates.
    The session auto-expires after 4 hours.

    **Connect to:** `ws://<host>/api/v1/trips/ws/{session_id}`

    ETA updates are pushed every 15 seconds with a `trend` field
    (`improving` / `worsening` / `stable`) so your UI can show direction of change.
    """
    session_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        eta = calculate_eta_for_location(payload.origin, payload.distance_km, payload.mode, db)
    finally:
        db.close()

    _live_sessions[session_id] = {
        "origin": payload.origin,
        "destination": payload.destination,
        "distance_km": payload.distance_km,
        "mode": payload.mode,
        "started_at": datetime.now(timezone.utc),
        "websocket": None,
        "last_eta": eta.eta_minutes,
        "last_congestion": eta.congestion_level,
        "last_speed": eta.average_speed_kmh,
    }
    return {
        "session_id": session_id,
        "origin": payload.origin,
        "destination": payload.destination,
        "initial_eta_minutes": eta.eta_minutes,
        "congestion_level": eta.congestion_level,
        "speed_kmh": eta.average_speed_kmh,
        "ws_url": f"/api/v1/trips/ws/{session_id}",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


@router.websocket("/trips/ws/{session_id}")
async def live_trip_ws(websocket: WebSocket, session_id: str) -> None:
    """Live trip ETA tracker WebSocket.

    Connect after calling `POST /trips/live/start`. Receives ETA update messages
    every 15 seconds pushed by the background updater.

    Message format:
    ```json
    {
      "type": "eta_update",
      "eta_minutes": 18.5,
      "congestion_level": "medium",
      "speed_kmh": 34.2,
      "trend": "improving",
      "updated_at": "2026-05-28T10:00:15+00:00"
    }
    ```
    """
    if session_id not in _live_sessions:
        await websocket.close(code=4004)
        return

    session = _live_sessions[session_id]
    await websocket.accept()
    session["websocket"] = websocket

    try:
        await websocket.send_json({
            "type": "trip_connected",
            "session_id": session_id,
            "origin": session["origin"],
            "destination": session["destination"],
            "message": "Live ETA updates will arrive every 15 seconds.",
        })
        _MAX_DURATION = 4 * 3600
        while True:
            elapsed = (datetime.now(timezone.utc) - session["started_at"]).total_seconds()
            if elapsed > _MAX_DURATION:
                await websocket.send_json({"type": "session_expired", "message": "4-hour session limit reached."})
                break
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if session_id in _live_sessions:
            _live_sessions[session_id]["websocket"] = None
            del _live_sessions[session_id]


@router.delete("/trips/live/{session_id}", status_code=status.HTTP_200_OK)
def end_live_trip(session_id: str) -> dict:
    """End a live trip tracking session."""
    if session_id not in _live_sessions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    del _live_sessions[session_id]
    return {"message": "Trip session ended", "session_id": session_id}


# ── Feature 4: Traffic Pulse Feed ─────────────────────────────────────────────

@router.websocket("/traffic/ws/pulse")
async def pulse_ws(websocket: WebSocket) -> None:
    """Real-time traffic change event feed.

    Connect: `ws://<host>/api/v1/traffic/ws/pulse`

    Events are pushed when congestion or speed changes significantly at any monitored
    location. The background monitor checks every 60 seconds.

    Event types: `congestion_spike`, `congestion_clearing`, `speed_drop`, `speed_recovery`

    Message format:
    ```json
    {
      "type": "pulse_event",
      "event": "congestion_spike",
      "location": "Silk Board Junction",
      "from_level": "medium",
      "to_level": "high",
      "speed_kmh": 12.4,
      "timestamp": "2026-05-28T10:01:00+00:00"
    }
    ```
    """
    await websocket.accept()
    _pulse_sockets.append(websocket)
    try:
        await websocket.send_json({
            "type": "pulse_connected",
            "message": "Subscribed to live traffic pulse events.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()})
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _pulse_sockets:
            _pulse_sockets.remove(websocket)


# ── Feature 5: ML Real-World Live Feed ───────────────────────────────────────

@router.websocket("/traffic/ws/ml-live")
async def ml_live_ws(websocket: WebSocket) -> None:
    """ML-enhanced real-time traffic feed.

    Connect: `ws://<host>/api/v1/traffic/ws/ml-live`

    Pushes a consolidated live snapshot every 5 seconds containing:
    - Live traffic readings from the DB for top monitored locations
    - ML predictions (RandomForest) for the next 1 h, 2 h, 3 h
    - Active community-reported incidents
    - Network-wide trend (improving / worsening / stable)

    Message format:
    ```json
    {
      "type": "ml_live_update",
      "timestamp": "...",
      "model_ready": true,
      "locations": [
        {
          "name": "Gachibowli",
          "city": "Hyderabad",
          "congestion_level": "medium",
          "avg_speed_kmh": 32.5,
          "vehicle_count": 540,
          "data_age_minutes": 2.1,
          "ml_forecast": [
            {"offset_hours": 1, "time_label": "5:00 PM",
             "predicted_congestion": "high", "confidence": 0.82,
             "probabilities": {"low":0.05,"medium":0.13,"high":0.82}},
            ...
          ]
        }
      ],
      "incidents": [...],
      "network_trend": "worsening",
      "high_congestion_pct": 42.3
    }
    ```
    """
    from app.database import SessionLocal
    from app.models.predictor import TrafficRecord, Incident as IncidentModel
    from app.services.india_locations import INDIA_LOCATIONS
    from app.services.ml_prediction_service import ml_model

    await websocket.accept()

    _TOP_N = 30          # send top N locations per tick
    _TICK_SECONDS = 5    # push interval

    try:
        await websocket.send_json({
            "type": "ml_live_connected",
            "message": (
                "Connected to ML live feed. "
                "Snapshot every 5 s — live DB traffic + RandomForest predictions."
            ),
            "model_ready": ml_model.is_ready(),
            "model_info": ml_model.model_info(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        while True:
            tick_start = datetime.now(timezone.utc)
            db = SessionLocal()
            try:
                # ── 1. Fetch latest record per location (last 10 min) ───────────
                since = tick_start - __import__("datetime").timedelta(minutes=10)
                records = (
                    db.query(TrafficRecord)
                    .filter(TrafficRecord.created_at >= since)
                    .order_by(TrafficRecord.created_at.desc())
                    .limit(_TOP_N * 3)   # over-fetch to deduplicate per location
                    .all()
                )

                # Deduplicate: keep only the latest record per location
                seen: dict[str, TrafficRecord] = {}
                for r in records:
                    if r.location not in seen:
                        seen[r.location] = r

                # If DB is sparse, fall back to INDIA_LOCATIONS simulation
                if len(seen) < 5:
                    from app.services.realtime_collector import _simulate_flow
                    from app.services.tomtom_service import classify_congestion, estimate_vehicle_count
                    for loc in INDIA_LOCATIONS[:_TOP_N]:
                        if loc["name"] not in seen:
                            flow = _simulate_flow(loc["lat"], loc["lng"])
                            cur  = float(flow["currentSpeed"])
                            free = float(flow["freeFlowSpeed"])
                            seen[loc["name"]] = type("FakeRec", (), {
                                "location":        loc["name"],
                                "congestion_level": classify_congestion(cur, free),
                                "average_speed":    cur,
                                "vehicle_count":    estimate_vehicle_count(cur, free),
                                "created_at":       tick_start,
                                "_city":            loc.get("city", ""),
                            })()

                # ── 2. Build per-location payload with ML forecast ────────────
                now_hour = tick_start.hour
                now_dow  = tick_start.weekday()
                location_rows = []
                congestion_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}

                for loc_name, rec in list(seen.items())[:_TOP_N]:
                    age_min = None
                    if hasattr(rec, "created_at") and rec.created_at:
                        ts = rec.created_at
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=__import__("datetime").timezone.utc)
                        age_min = round((tick_start - ts).total_seconds() / 60, 1)

                    vc  = float(rec.vehicle_count or 500)
                    spd = float(rec.average_speed or 35)
                    cong = rec.congestion_level or "medium"
                    congestion_counts[cong] = congestion_counts.get(cong, 0) + 1

                    forecast = ml_model.predict_hours_ahead(
                        base_hour=now_hour,
                        base_dow=now_dow,
                        vehicle_count=vc,
                        average_speed=spd,
                        hours_ahead=3,
                    )

                    location_rows.append({
                        "name":              loc_name,
                        "city":              getattr(rec, "_city", ""),
                        "congestion_level":  cong,
                        "avg_speed_kmh":     round(spd, 1),
                        "vehicle_count":     int(vc),
                        "data_age_minutes":  age_min,
                        "ml_forecast":       forecast,
                    })

                # ── 3. Active incidents ────────────────────────────────────────
                active_incidents = (
                    db.query(IncidentModel)
                    .filter(IncidentModel.is_active == True)
                    .order_by(IncidentModel.reported_at.desc())
                    .limit(20)
                    .all()
                )
                incident_list = [
                    {
                        "id":            i.id,
                        "location":      i.location,
                        "incident_type": i.incident_type,
                        "severity":      i.severity,
                        "upvotes":       i.upvotes or 0,
                        "downvotes":     i.downvotes or 0,
                        "reported_at":   i.reported_at.isoformat() if i.reported_at else None,
                        "expires_at":    i.expires_at.isoformat() if i.expires_at else None,
                    }
                    for i in active_incidents
                ]

                # ── 4. Network-wide trend ─────────────────────────────────────
                total_locs = len(location_rows)
                high_pct = round(congestion_counts.get("high", 0) / max(total_locs, 1) * 100, 1)

                # Compare current high% to what ML predicts in 1h
                future_high = sum(
                    1 for row in location_rows
                    if row["ml_forecast"] and row["ml_forecast"][0]["predicted_congestion"] == "high"
                )
                future_high_pct = round(future_high / max(total_locs, 1) * 100, 1)

                if future_high_pct > high_pct + 5:
                    network_trend = "worsening"
                elif future_high_pct < high_pct - 5:
                    network_trend = "improving"
                else:
                    network_trend = "stable"

                # ── 5. Send payload ───────────────────────────────────────────
                await websocket.send_json({
                    "type":              "ml_live_update",
                    "timestamp":         tick_start.isoformat(),
                    "model_ready":       ml_model.is_ready(),
                    "locations":         location_rows,
                    "incidents":         incident_list,
                    "network_trend":     network_trend,
                    "high_congestion_pct":  high_pct,
                    "future_high_pct_1h": future_high_pct,
                    "total_locations":   total_locs,
                })

            finally:
                db.close()

            # Sleep for the remainder of the tick window
            elapsed = (datetime.now(timezone.utc) - tick_start).total_seconds()
            sleep_for = max(0.1, _TICK_SECONDS - elapsed)
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("ML live WS error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass


@router.get("/traffic/ml/model-info", tags=["Live Traffic"])
def get_ml_model_info() -> dict:
    """Return current status and metadata of the ML traffic prediction model."""
    from app.services.ml_prediction_service import ml_model
    return {
        "ml_model": ml_model.model_info(),
        "websocket_endpoint": "ws://<host>/api/v1/traffic/ws/ml-live",
        "tick_interval_seconds": 5,
        "forecast_hours": 3,
    }


@router.get("/traffic/ml/predict", tags=["Live Traffic"])
def ml_predict_now(
    hour: int = 0,
    dow: int = 0,
    vehicle_count: float = 500.0,
    average_speed: float = 35.0,
    hours_ahead: int = 3,
) -> dict:
    """
    Run the ML model for a specific hour/day combination and return
    congestion predictions for the next N hours.

    Useful for testing the model without a WebSocket connection.
    `hour` = 0-23, `dow` = 0 (Mon) – 6 (Sun).
    """
    from app.services.ml_prediction_service import ml_model
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    target_hour = hour if hour != 0 else now.hour
    target_dow  = dow  if dow  != 0 else now.weekday()
    current = ml_model.predict(target_hour, target_dow, vehicle_count, average_speed)
    forecast = ml_model.predict_hours_ahead(target_hour, target_dow, vehicle_count, average_speed, hours_ahead)
    return {
        "input": {
            "hour": target_hour,
            "day_of_week": target_dow,
            "vehicle_count": vehicle_count,
            "average_speed": average_speed,
        },
        "current_prediction": current,
        "forecast": forecast,
        "model_info": ml_model.model_info(),
    }
