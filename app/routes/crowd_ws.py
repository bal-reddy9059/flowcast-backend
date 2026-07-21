import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.crowd_station_service import resolve_station_id
from app.utils.crowd_ws_manager import crowd_ws_manager
from app.utils import crowd_live_cache

router = APIRouter(tags=["Crowd — WebSocket"])


@router.websocket("/ws/crowd")
async def ws_all_crowd(websocket: WebSocket):
    await crowd_ws_manager.connect(websocket)
    if crowd_live_cache.is_warm():
        await websocket.send_text(json.dumps({
            "type": "crowd_update",
            "stations": crowd_live_cache.get_all_crowd(),
        }))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        crowd_ws_manager.disconnect(websocket)


@router.websocket("/ws/crowd/{station_id}")
async def ws_station_crowd(websocket: WebSocket, station_id: str):
    resolved = resolve_station_id(station_id)
    await crowd_ws_manager.connect(websocket, resolved)
    cached = crowd_live_cache.get_station_crowd(resolved)
    if cached:
        await websocket.send_text(json.dumps({
            "type": "crowd_update",
            "station": cached,
        }))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        crowd_ws_manager.disconnect(websocket, resolved)
