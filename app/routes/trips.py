"""Trip history endpoints — log and review past journeys."""

import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.trip import TripHistory
from app.models.user import User
from app.services.auth_service import get_current_user
from app.utils.api_response import api_success, to_ist_iso

router = APIRouter(prefix="/trips", tags=["Trip History"])
logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_VALID_MODES = {"driving", "walking", "transit"}
_VALID_CONGESTION = {"low", "medium", "high"}
_SPEED_KMH = {
    "driving": {"low": 40.0, "medium": 25.0, "high": 12.0},
    "walking": {"low": 5.0, "medium": 4.5, "high": 4.0},
    "transit": {"low": 28.0, "medium": 18.0, "high": 10.0},
}


def _to_ist(dt: Optional[datetime]) -> Optional[str]:
    return to_ist_iso(dt) if dt else None


def _as_utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize DB datetimes to naive UTC for safe comparisons."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _enrich_trip_fields(
    *,
    origin_name: str,
    destination_name: str,
    origin_lat: Optional[float],
    origin_lng: Optional[float],
    destination_lat: Optional[float],
    destination_lng: Optional[float],
    mode: str,
    distance_km: Optional[float],
    predicted_eta_minutes: Optional[float],
    congestion_at_departure: Optional[str],
) -> dict:
    """Fill missing coords / distance / ETA / congestion from geocode + weather."""
    try:
        from app.routes.route import _geocode, _haversine_km
    except Exception:
        _geocode = _haversine_km = None  # type: ignore

    o = _geocode(origin_name) if _geocode else None
    d = _geocode(destination_name) if _geocode else None

    if origin_lat is None and o:
        origin_lat = o["lat"]
    if origin_lng is None and o:
        origin_lng = o["lng"]
    if destination_lat is None and d:
        destination_lat = d["lat"]
    if destination_lng is None and d:
        destination_lng = d["lng"]

    if distance_km is None and _haversine_km and origin_lat is not None and destination_lat is not None:
        distance_km = round(
            _haversine_km(origin_lat, origin_lng, destination_lat, destination_lng) * 1.25,
            1,
        )

    if congestion_at_departure is None:
        try:
            from app.services.weather_service import weather_impact_for_location
            impact = weather_impact_for_location(origin_name)
            mod = impact.get("congestion_modifier", "none")
            congestion_at_departure = {
                "none": "low", "light": "low", "moderate": "medium", "severe": "high",
            }.get(mod, "medium")
        except Exception:
            congestion_at_departure = "medium"

    if predicted_eta_minutes is None and distance_km:
        speed = _SPEED_KMH.get(mode, _SPEED_KMH["driving"]).get(
            congestion_at_departure or "medium", 25.0
        )
        predicted_eta_minutes = round((distance_km / max(speed, 1.0)) * 60, 1)

    return {
        "origin_lat": origin_lat,
        "origin_lng": origin_lng,
        "destination_lat": destination_lat,
        "destination_lng": destination_lng,
        "distance_km": distance_km,
        "predicted_eta_minutes": predicted_eta_minutes,
        "congestion_at_departure": congestion_at_departure,
    }


def _serialize_trip(t: TripHistory) -> dict:
    return {
        "id":                      str(t.id),
        "origin":                  t.origin_name,
        "destination":             t.destination_name,
        "origin_lat":              t.origin_lat,
        "origin_lng":              t.origin_lng,
        "destination_lat":         t.destination_lat,
        "destination_lng":         t.destination_lng,
        "mode":                    t.mode,
        "distance_km":             t.distance_km,
        "predicted_eta_minutes":   t.predicted_eta_minutes,
        "congestion_at_departure": t.congestion_at_departure,
        "taken_at":                _to_ist(t.created_at),
    }


class TripCreate(BaseModel):
    origin_name: Optional[str] = Field(None, min_length=2, max_length=200)
    destination_name: Optional[str] = Field(None, min_length=2, max_length=200)
    # Frontend aliases
    origin: Optional[str] = Field(None, min_length=2, max_length=200)
    destination: Optional[str] = Field(None, min_length=2, max_length=200)
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
            "origin": "Gachibowli",
            "destination": "Hitech City",
            "mode": "driving",
        }
    })

    @model_validator(mode="after")
    def require_origin_destination(self):
        if not (self.origin_name or self.origin):
            raise ValueError("origin or origin_name is required")
        if not (self.destination_name or self.destination):
            raise ValueError("destination or destination_name is required")
        return self

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
    """Log a trip to your history. Call this after requesting a route to track commute patterns.

    Missing distance / ETA / congestion / coordinates are filled automatically from
    geocoding and current weather impact when possible.
    """
    origin_name = (payload.origin_name or payload.origin or "").strip()
    destination_name = (payload.destination_name or payload.destination or "").strip()

    enriched = _enrich_trip_fields(
        origin_name=origin_name,
        destination_name=destination_name,
        origin_lat=payload.origin_lat,
        origin_lng=payload.origin_lng,
        destination_lat=payload.destination_lat,
        destination_lng=payload.destination_lng,
        mode=payload.mode,
        distance_km=payload.distance_km,
        predicted_eta_minutes=payload.predicted_eta_minutes,
        congestion_at_departure=payload.congestion_at_departure,
    )

    trip = TripHistory(
        user_id=current_user.id,
        origin_name=origin_name,
        destination_name=destination_name,
        mode=payload.mode,
        **enriched,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    logger.info(
        "Trip logged for user %s: %s → %s (%.1f km, %.1f min)",
        current_user.id, origin_name, destination_name,
        trip.distance_km or 0, trip.predicted_eta_minutes or 0,
    )
    return api_success(data=_serialize_trip(trip), message="Trip logged")


@router.get("/", status_code=status.HTTP_200_OK)
def get_trip_history(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(
        0, ge=0,
        description="0-based skip count (NOT a page number). Use 0 for the first page.",
    ),
    page: Optional[int] = Query(
        None, ge=1,
        description="Optional 1-based page number. If set, overrides offset as (page-1)*limit.",
    ),
    mode: Optional[str] = Query(None, description="Filter by travel mode"),
) -> dict:
    """Paginated trip history for the current user, newest first.

    **Pagination tip:** `offset` is how many rows to skip (start at **0**).
    To get page 2 of 20, use `offset=20` or `page=2`.
    """
    query = db.query(TripHistory).filter(TripHistory.user_id == current_user.id)
    if mode:
        if mode not in _VALID_MODES:
            raise HTTPException(status_code=400, detail="mode must be driving, walking, or transit")
        query = query.filter(TripHistory.mode == mode)

    total = query.count()
    warning = None
    effective_offset = offset

    if page is not None:
        effective_offset = (page - 1) * limit

    # Swagger/clients often send offset=1 thinking it means "page 1"
    if total > 0 and effective_offset >= total:
        warning = (
            f"offset={effective_offset} is past the end (total={total}). "
            f"Clamped to offset=0. Use offset=0 or page=1 for the first page."
        )
        effective_offset = 0

    trips = (
        query.order_by(TripHistory.created_at.desc())
        .offset(effective_offset)
        .limit(limit)
        .all()
    )
    returned = len(trips)
    has_more = (effective_offset + returned) < total
    current_page = (effective_offset // limit) + 1 if limit else 1
    total_pages = max(1, (total + limit - 1) // limit) if limit else 1

    data: dict = {
        "total":        total,
        "offset":       effective_offset,
        "limit":        limit,
        "page":         current_page,
        "total_pages":  total_pages,
        "returned":     returned,
        "has_more":     has_more,
        "trips":        [_serialize_trip(t) for t in trips],
    }
    if warning:
        data["warning"] = warning

    return api_success(data=data)


@router.get("/stats", status_code=status.HTTP_200_OK)
def get_trip_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Personal travel stats: most-used routes, average ETA, mode breakdown, and 30-day activity."""
    all_trips = db.query(TripHistory).filter(TripHistory.user_id == current_user.id).all()

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    if not all_trips:
        return api_success(
            data={
                "total_trips": 0,
                "trips_last_30_days": 0,
                "trips_last_7_days": 0,
                "most_frequent_route": None,
                "top_5_routes": [],
                "mode_breakdown": {},
                "congestion_breakdown": {},
                "eta_stats": {"average_eta_minutes": None, "min_eta_minutes": None, "max_eta_minutes": None},
                "distance_stats": {"total_distance_km": None, "average_distance_km": None},
                "generated_at": _to_ist(datetime.now(timezone.utc)),
            },
            message="No trips logged yet. Log a trip to see your stats.",
        )

    route_counter = Counter(f"{t.origin_name} → {t.destination_name}" for t in all_trips)
    mode_counter = Counter(t.mode for t in all_trips)
    congestion_counter = Counter(
        t.congestion_at_departure for t in all_trips if t.congestion_at_departure
    )

    etas = [t.predicted_eta_minutes for t in all_trips if t.predicted_eta_minutes]
    distances = [t.distance_km for t in all_trips if t.distance_km]

    cutoff_30d = now_naive - timedelta(days=30)
    cutoff_7d = now_naive - timedelta(days=7)
    last_30 = [t for t in all_trips if (_as_utc_naive(t.created_at) or now_naive) >= cutoff_30d]
    last_7 = [t for t in all_trips if (_as_utc_naive(t.created_at) or now_naive) >= cutoff_7d]

    top_routes = [{"route": r, "count": c} for r, c in route_counter.most_common(5)]

    sorted_trips = sorted(all_trips, key=lambda t: _as_utc_naive(t.created_at) or datetime.min)
    first_trip_at = _to_ist(sorted_trips[0].created_at)
    last_trip_at = _to_ist(sorted_trips[-1].created_at)

    return api_success(data={
        "total_trips":          len(all_trips),
        "trips_last_30_days":   len(last_30),
        "trips_last_7_days":    len(last_7),
        "most_frequent_route":  route_counter.most_common(1)[0][0],
        "top_5_routes":         top_routes,
        "mode_breakdown":       dict(mode_counter),
        "congestion_breakdown": dict(congestion_counter),
        "eta_stats": {
            "average_eta_minutes": round(sum(etas) / len(etas), 1) if etas else None,
            "min_eta_minutes":     round(min(etas), 1) if etas else None,
            "max_eta_minutes":     round(max(etas), 1) if etas else None,
            "trips_with_eta":      len(etas),
        },
        "distance_stats": {
            "total_distance_km":   round(sum(distances), 1) if distances else None,
            "average_distance_km": round(sum(distances) / len(distances), 1) if distances else None,
            "trips_with_distance": len(distances),
        },
        "first_trip_at": first_trip_at,
        "last_trip_at":  last_trip_at,
        "generated_at":  _to_ist(datetime.now(timezone.utc)),
    })


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
    return api_success(
        data={"id": str(trip_id)},
        message="Trip removed from history",
    )
