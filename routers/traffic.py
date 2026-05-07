from __future__ import annotations
import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from services.traffic_service import get_dummy_traffic, get_traffic_data

router = APIRouter(prefix="/traffic", tags=["Traffic"])


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=schemas.TrafficResponse, summary="Live traffic snapshot")
async def get_traffic(
    origin: Optional[str] = Query(
        None, description="Origin address/place name (enables Google Maps live data)"
    ),
    destination: Optional[str] = Query(
        None, description="Destination address/place name (enables Google Maps live data)"
    ),
    db: Session = Depends(get_db),
):
    """
    Returns current traffic conditions.

    - With **GOOGLE_MAPS_API_KEY** set **and** `origin`+`destination` provided →
      fetches live data from Google Maps Distance Matrix API.
    - Otherwise → returns randomised dummy data for 5 Indian city locations.

    Every call persists the snapshot to PostgreSQL for historical queries.
    """
    traffic_list, source = await get_traffic_data(origin, destination)

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
    db.commit()

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
    limit: int = Query(50, ge=1, le=500, description="Max records to return"),
    location: Optional[str] = Query(None, description="Filter by location name (partial match)"),
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
