import json
from typing import Dict, List

from fastapi import WebSocket


class CrowdConnectionManager:
    def __init__(self):
        self._all: List[WebSocket] = []
        self._station: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, station_id: str | None = None):
        await websocket.accept()
        if station_id:
            self._station.setdefault(station_id, []).append(websocket)
        else:
            self._all.append(websocket)

    def disconnect(self, websocket: WebSocket, station_id: str | None = None):
        if station_id:
            subs = self._station.get(station_id, [])
            if websocket in subs:
                subs.remove(websocket)
        else:
            if websocket in self._all:
                self._all.remove(websocket)

    async def _send(self, websocket: WebSocket, data: dict) -> bool:
        try:
            await websocket.send_text(json.dumps(data))
            return True
        except Exception:
            return False

    async def broadcast_all(self, data: dict):
        dead = [ws for ws in self._all if not await self._send(ws, data)]
        for ws in dead:
            self._all.remove(ws)

    async def broadcast_station(self, station_id: str, data: dict):
        subs = self._station.get(station_id, [])
        dead = [ws for ws in subs if not await self._send(ws, data)]
        for ws in dead:
            subs.remove(ws)

    @property
    def total_subscribers(self) -> int:
        return len(self._all) + sum(len(v) for v in self._station.values())


crowd_ws_manager = CrowdConnectionManager()
