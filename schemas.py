from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class TrafficData(BaseModel):
    location: str
    latitude: float
    longitude: float
    congestion_level: str
    speed_kmh: float
    travel_time_mins: float
    timestamp: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TrafficResponse(BaseModel):
    status: str
    source: str  # "google_maps" | "dummy"
    data: list[TrafficData]


class TrafficQueryParams(BaseModel):
    origin: Optional[str] = None
    destination: Optional[str] = None
