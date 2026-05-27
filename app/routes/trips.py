"""Trip history endpoints — log and review past journeys."""

import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.trip import TripHistory
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/trips", tags=["Trip History"])
logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")


def _to_ist(dt: datetime) -> str:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()

_VALID_MODES = {"driving", "walking", "transit"}
_VALID_CONGESTION = {"low", "medium", "high"}


class TripCreate(BaseModel):
    origin_name: str = Field(..., min_length=2, max_length=200)
    destination_name: str = Field(..., min_length=2, max_length=200)
    origin_lat: Optional[float] = Field(None, ge=-90, le=90)
    origin_lng: Optional[float] = Field(None, ge=-180, le=180)
    destination_lat: Optional[float] = Field(None, ge=-90, le=90)
    destination_lng: Optional[float] = Field(None, ge=-180, le=180)
    mode: str = Field("driving", description="driving / walking / transit")
    distance_km: Optional[float] = Field(None, gt=0, le=500)
    predicted_eta_minutes: Optional[float] = Field(None, gt=0)
    congestion_at_departure: Optional[str] = Field(None, description="low / medium / high")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "origin_name": "Gachibowli",
            "destination_name": "Hitech City",
            "origin_lat": 17.4401,
            "origin_lng": 78.3489,
            "destination_lat": 17.4486,
            "destination_lng": 78.3908,
            "mode": "driving",
            "distance_km": 7.5,
            "predicted_eta_minutes": 22.0,
            "congestion_at_departure": "medium",
        }
    })

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        if v not in _VALID_MODES:
            raise ValueError("mode must be driving, walking, or transit")
        return v

    @field_validator("congestion_at_departure")
    @classmethod
    def validate_congestion(cls, v):
        if v and v not in _VALID_CONGESTION:
            raise ValueError("congestion_at_departure must be low, medium, or high")
        return v


@router.post("/", status_code=status.HTTP_201_CREATED)
def log_trip(
    payload: TripCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Log a trip to your history. Call this after requesting a route to track commute patterns."""
    trip = TripHistory(
        user_id=current_user.id,
        origin_name=payload.origin_name,
        destination_name=payload.destination_name,
        origin_lat=payload.origin_lat,
        origin_lng=payload.origin_lng,
        destination_lat=payload.destination_lat,
        destination_lng=payload.destination_lng,
        mode=payload.mode,
        distance_km=payload.distance_km,
        predicted_eta_minutes=payload.predicted_eta_minutes,
        congestion_at_departure=payload.congestion_at_departure,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    logger.info("Trip logged for user %s: %s → %s", current_user.id, payload.origin_name, payload.destination_name)
    return {
        "id":                      trip.id,
        "message":                 "Trip logged",
        "origin":                  trip.origin_name,
        "destination":             trip.destination_name,
        "mode":                    trip.mode,
        "distance_km":             trip.distance_km,
        "predicted_eta_minutes":   trip.predicted_eta_minutes,
        "congestion_at_departure": trip.congestion_at_departure,
        "taken_at":                _to_ist(trip.created_at),
    }


@router.get("/", status_code=status.HTTP_200_OK)
def get_trip_history(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    mode: Optional[str] = Query(None, description="Filter by travel mode"),
) -> dict:
    """Paginated trip history for the current user, newest first."""
    query = db.query(TripHistory).filter(TripHistory.user_id == current_user.id)
    if mode:
        if mode not in _VALID_MODES:
            raise HTTPException(status_code=400, detail="mode must be driving, walking, or transit")
        query = query.filter(TripHistory.mode == mode)

    total = query.count()
    trips = query.order_by(TripHistory.created_at.desc()).offset(offset).limit(limit).all()
    returned = len(trips)
    has_more = (offset + returned) < total

    response: dict = {
        "total":     total,
        "offset":    offset,
        "limit":     limit,
        "returned":  returned,
        "has_more":  has_more,
        "trips": [
            {
                "id":                      t.id,
                "origin":                  t.origin_name,
                "destination":             t.destination_name,
                "mode":                    t.mode,
                "distance_km":             t.distance_km,
                "predicted_eta_minutes":   t.predicted_eta_minutes,
                "congestion_at_departure": t.congestion_at_departure,
                "taken_at":                _to_ist(t.created_at),
            }
            for t in trips
        ],
    }

    if total > 0 and offset >= total:
        response["warning"] = (
            f"offset={offset} exceeds total={total}. "
            f"Use offset=0 to see all trips."
        )

    return response


@router.get("/stats", status_code=status.HTTP_200_OK)
def get_trip_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Personal travel stats: most-used routes, average ETA, mode breakdown, and 30-day activity."""
    all_trips = db.query(TripHistory).filter(TripHistory.user_id == current_user.id).all()

    now_naive = datetime.utcnow()

    if not all_trips:
        return {
            "message": "No trips logged yet. Log a trip to see your stats.",
            "total_trips": 0,
            "generated_at": _to_ist(now_naive),
        }

    route_counter     = Counter(f"{t.origin_name} → {t.destination_name}" for t in all_trips)
    mode_counter      = Counter(t.mode for t in all_trips)
    congestion_counter = Counter(t.congestion_at_departure for t in all_trips if t.congestion_at_departure)

    etas      = [t.predicted_eta_minutes for t in all_trips if t.predicted_eta_minutes]
    distances = [t.distance_km for t in all_trips if t.distance_km]

    cutoff_30d = now_naive - timedelta(days=30)
    cutoff_7d  = now_naive - timedelta(days=7)
    last_30 = [t for t in all_trips if t.created_at >= cutoff_30d]
    last_7  = [t for t in all_trips if t.created_at >= cutoff_7d]

    top_routes = [{"route": r, "count": c} for r, c in route_counter.most_common(5)]

    sorted_trips = sorted(all_trips, key=lambda t: t.created_at)
    first_trip_at = _to_ist(sorted_trips[0].created_at)
    last_trip_at  = _to_ist(sorted_trips[-1].created_at)

    avg_eta      = round(sum(etas) / len(etas), 1) if etas else None
    total_dist   = round(sum(distances), 1) if distances else None
    avg_dist     = round(sum(distances) / len(distances), 1) if distances else None

    return {
        "total_trips":          len(all_trips),
        "trips_last_30_days":   len(last_30),
        "trips_last_7_days":    len(last_7),
        "most_frequent_route":  route_counter.most_common(1)[0][0],
        "top_5_routes":         top_routes,
        "mode_breakdown":       dict(mode_counter),
        "congestion_breakdown": dict(congestion_counter),
        "eta_stats": {
            "average_eta_minutes": avg_eta,
        },
        "distance_stats": {
            "total_distance_km":   total_dist,
            "average_distance_km": avg_dist,
        },
        "first_trip_at":  first_trip_at,
        "last_trip_at":   last_trip_at,
        "generated_at":   _to_ist(now_naive),
    }


@router.delete("/{trip_id}", status_code=status.HTTP_200_OK)
def delete_trip(
    trip_id: uuid.UUID = Path(
        ...,
        description="Trip UUID — get this from `GET /api/v1/trips/` (copy any `id`)",
    ),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Remove a trip from history."""
    trip = db.query(TripHistory).filter(
        TripHistory.id == trip_id,
        TripHistory.user_id == current_user.id,
    ).first()
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found")
    db.delete(trip)
    db.commit()
    logger.info("User %s deleted trip %s", current_user.id, trip_id)
    return {"message": "Trip removed from history", "id": trip_id}
