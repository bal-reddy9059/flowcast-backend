from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from services.traffic_service import get_dummy_traffic, get_traffic_data

router = APIRouter(prefix="/traffic", tags=["Traffic"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=schemas.TrafficResponse, summary="Live traffic snapshot")
async def get_traffic(
    origin: Optional[str] = Query(
        None,
        description="Origin address/place name (enables Google Maps live data)",
        examples=["Hyderabad", "MG Road, Bangalore"],
    ),
    destination: Optional[str] = Query(
        None,
        description="Destination address/place name (enables Google Maps live data)",
        examples=["Kamala", "Airport Road, Hyderabad"],
    ),
    db: Session = Depends(get_db),
):
    """
    Returns current traffic conditions.

    - With **GOOGLE_MAPS_DISTANCE_MATRIX_API_KEY** set and both `origin` + `destination` valid,
      the endpoint attempts to fetch live traffic data from Google Maps Distance Matrix.
    - If the API key is missing, the origin/destination values are invalid,
      or the Google API call fails / returns no results,
      the endpoint falls back to randomized dummy traffic data.

    Always returns HTTP 200 with traffic data, and never fails with a 500 due
    to external Google API issues.
    """
    origin_value = origin.strip() if origin else None
    destination_value = destination.strip() if destination else None

    use_google = bool(origin_value and destination_value)
    if use_google and (len(origin_value) < 2 or len(destination_value) < 2):
        logger.warning(
            "Invalid traffic query values, falling back to dummy data: origin=%r destination=%r",
            origin_value,
            destination_value,
        )
        use_google = False

    if use_google:
        try:
            traffic_list, source = await get_traffic_data(origin_value, destination_value)
            if not traffic_list or source != "google_maps":
                logger.warning(
                    "Google Maps returned no live results for origin=%r destination=%r; using dummy data",
                    origin_value,
                    destination_value,
                )
                traffic_list, source = get_dummy_traffic(), "dummy"
        except Exception as exc:
            logger.error(
                "Google Maps traffic API error, falling back to dummy data: %s",
                exc,
                exc_info=True,
            )
            traffic_list, source = get_dummy_traffic(), "dummy"
    else:
        traffic_list, source = get_dummy_traffic(), "dummy"

    for item in traffic_list:
        record = models.TrafficRecord(
            location=item["location"],
            latitude=item["latitude"],
            longitude=item["longitude"],
            congestion_level=item["congestion_level"],
            speed_kmh=item["speed_kmh"],
            travel_time_mins=item["travel_time_mins"],
        )
        db.add(record)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to persist traffic snapshot, continuing with response: %s", exc, exc_info=True)

    return schemas.TrafficResponse(
        status="ok",
        source=source,
        data=[schemas.TrafficData(**item) for item in traffic_list],
    )


@router.get("/dummy", response_model=schemas.TrafficResponse, summary="Dummy traffic data (no DB write)")
async def get_dummy():
    """Returns randomised dummy traffic data without hitting the DB or Google Maps."""
    data = get_dummy_traffic()
    return schemas.TrafficResponse(
        status="ok",
        source="dummy",
        data=[schemas.TrafficData(**item) for item in data],
    )


@router.get(
    "/history",
    response_model=list[schemas.TrafficData],
    summary="Stored traffic history",
)
async def get_history(
    limit: int = Query(
        50,
        ge=1,
        le=500,
        description="Max records to return",
        examples=[50],
    ),
    location: Optional[str] = Query(
        None,
        description="Filter by location name (partial match)",
        examples=["Hyderabad"],
    ),
    db: Session = Depends(get_db),
):
    """Reads stored traffic records from PostgreSQL, newest first."""
    query = db.query(models.TrafficRecord)
    if location:
        query = query.filter(models.TrafficRecord.location.ilike(f"%{location}%"))
    records = (
        query.order_by(models.TrafficRecord.timestamp.desc()).limit(limit).all()
    )
    return [schemas.TrafficData.model_validate(r) for r in records]


# ---------------------------------------------------------------------------
# WebSocket — real-time push every 5 seconds
# ---------------------------------------------------------------------------


class _ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.remove(ws)

    async def send(self, ws: WebSocket, payload: str) -> None:
        try:
            await ws.send_text(payload)
        except Exception:
            self.disconnect(ws)


_manager = _ConnectionManager()


@router.websocket("/ws")
async def websocket_traffic(websocket: WebSocket):
    """
    WebSocket endpoint — pushes a fresh dummy traffic snapshot every 5 seconds.

    Connect with:  ws://localhost:8000/traffic/ws
    """
    await _manager.connect(websocket)
    try:
        while True:
            snapshot = get_dummy_traffic()
            payload = json.dumps(
                {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "traffic": [
                        {
                            **{k: v for k, v in item.items() if k != "timestamp"},
                            "timestamp": item["timestamp"].isoformat() + "Z",
                        }
                        for item in snapshot
                    ],
                }
            )
            await _manager.send(websocket, payload)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        _manager.disconnect(websocket)
