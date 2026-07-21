"""Favorite locations endpoints — bookmark spots for quick status checks."""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.favorite import FavoriteLocation
from app.models.predictor import Incident, TrafficRecord
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.city_aliases import CITY_ALIASES, location_filter

router = APIRouter(prefix="/favorites", tags=["Favorite Locations"])
logger = logging.getLogger(__name__)

# Only treat readings newer than this as "live"
_LIVE_MAX_AGE = timedelta(hours=6)
_STALE_MAX_AGE = timedelta(hours=24)

_SUGGESTED_LOCATIONS = [
    {"name": "Hitech City",   "lat": 17.4486, "lng": 78.3908},
    {"name": "Gachibowli",   "lat": 17.4401, "lng": 78.3489},
    {"name": "Banjara Hills", "lat": 17.4239, "lng": 78.4738},
    {"name": "Ameerpet",     "lat": 17.4375, "lng": 78.4483},
    {"name": "Kukatpally",   "lat": 17.4848, "lng": 78.4138},
    {"name": "Secunderabad", "lat": 17.4399, "lng": 78.4983},
]


class FavoriteCreate(BaseModel):
    location_name: str = Field(..., min_length=2, max_length=200, description="Location name")
    nickname: Optional[str] = Field(None, max_length=100, description="Short label e.g. Home, Office")
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "location_name": "Hitech City",
            "nickname": "Office",
            "latitude": 17.4486,
            "longitude": 78.3908,
        }
    })


def _resolve_coords(location_name: str, lat: Optional[float], lng: Optional[float]) -> tuple[str, Optional[float], Optional[float]]:
    """Fill missing coordinates via INDIA_LOCATIONS geocoder; canonicalize name when known."""
    if lat is not None and lng is not None:
        return location_name, lat, lng
    try:
        from app.routes.route import _geocode
        geo = _geocode(location_name)
    except Exception:
        geo = None
    if geo:
        return geo["name"], geo["lat"], geo["lng"]
    return location_name, lat, lng


def _record_ts(record: TrafficRecord) -> Optional[datetime]:
    ts = record.timestamp or record.created_at
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _live_status(location_name: str, db: Session, *, lat: Optional[float] = None, lng: Optional[float] = None) -> dict:
    """Latest fresh traffic for a location — never present months-old rows as live."""
    now = datetime.now(timezone.utc)
    live_since = now - _LIVE_MAX_AGE
    stale_since = now - _STALE_MAX_AGE

    name_filter = location_filter(TrafficRecord.location, location_name)
    # Also try exact/canonical geocoded name
    try:
        from app.routes.route import _geocode
        geo = _geocode(location_name)
        if geo and geo["name"].lower() != location_name.lower():
            name_filter = or_(name_filter, TrafficRecord.location.ilike(f"%{geo['name']}%"))
    except Exception:
        pass

    def _latest(since: datetime) -> Optional[TrafficRecord]:
        q = (
            db.query(TrafficRecord)
            .filter(name_filter)
            .filter(
                or_(
                    TrafficRecord.timestamp >= since,
                    TrafficRecord.created_at >= since,
                )
            )
        )
        # Prefer geo-near records when coords known
        if lat is not None and lng is not None:
            q = q.filter(
                TrafficRecord.latitude.isnot(None),
                TrafficRecord.longitude.isnot(None),
                TrafficRecord.latitude.between(lat - 0.05, lat + 0.05),
                TrafficRecord.longitude.between(lng - 0.05, lng + 0.05),
            )
        return q.order_by(TrafficRecord.timestamp.desc().nullslast(), TrafficRecord.created_at.desc()).first()

    record = _latest(live_since)
    is_stale = False
    if record is None:
        # Widen without geo constraint for name match
        record = (
            db.query(TrafficRecord)
            .filter(name_filter)
            .filter(
                or_(
                    TrafficRecord.timestamp >= live_since,
                    TrafficRecord.created_at >= live_since,
                )
            )
            .order_by(TrafficRecord.timestamp.desc().nullslast(), TrafficRecord.created_at.desc())
            .first()
        )
    if record is None:
        # Stale fallback (24h) — mark clearly so UI doesn't treat as live
        record = (
            db.query(TrafficRecord)
            .filter(name_filter)
            .filter(
                or_(
                    TrafficRecord.timestamp >= stale_since,
                    TrafficRecord.created_at >= stale_since,
                )
            )
            .order_by(TrafficRecord.timestamp.desc().nullslast(), TrafficRecord.created_at.desc())
            .first()
        )
        is_stale = record is not None

    incident_count = (
        db.query(Incident)
        .filter(
            location_filter(Incident.location, location_name),
            Incident.is_active.is_(True),
            Incident.reported_at >= live_since,
        )
        .count()
    )

    if not record:
        return {
            "congestion_level": "unknown",
            "average_speed_kmh": None,
            "vehicle_count": None,
            "last_updated": None,
            "active_incidents": incident_count,
            "is_live": False,
            "is_stale": False,
            "message": "No recent traffic data for this location",
        }

    ts = _record_ts(record)
    age_hours = round((now - ts).total_seconds() / 3600, 1) if ts else None
    # Anything older than live window is stale even if we found it somehow
    if ts and ts < live_since:
        is_stale = True

    return {
        "congestion_level": "unknown" if is_stale else (record.congestion_level or "unknown"),
        "average_speed_kmh": None if is_stale else record.average_speed,
        "vehicle_count": None if is_stale else record.vehicle_count,
        "last_updated": ts.isoformat() if ts else None,
        "active_incidents": incident_count,
        "is_live": not is_stale,
        "is_stale": is_stale,
        "data_age_hours": age_hours,
        "message": (
            "Traffic data is stale — waiting for a fresh reading"
            if is_stale
            else None
        ),
    }


def _location_terms(location_name: str) -> list[str]:
    normalized = re.sub(
        r"\s+(urban|rural|district|city)$",
        "",
        location_name.strip().lower(),
    ).strip()
    terms = list(CITY_ALIASES.get(normalized, [location_name]))
    try:
        from app.routes.route import _geocode
        geo = _geocode(location_name)
        if geo:
            terms.append(geo["name"])
    except Exception:
        pass
    return list(dict.fromkeys(term.strip() for term in terms if term and term.strip()))


def _batch_live_status(locations: list[dict], db: Session) -> dict[str, dict]:
    """Resolve traffic and incidents for every location in two bounded queries."""
    if not locations:
        return {}

    now = datetime.now(timezone.utc)
    live_since = now - _LIVE_MAX_AGE
    stale_since = now - _STALE_MAX_AGE
    terms_by_name = {
        item["location_name"]: _location_terms(item["location_name"])
        for item in locations
    }
    all_terms = list(dict.fromkeys(
        term for terms in terms_by_name.values() for term in terms
    ))

    traffic_conditions = [
        TrafficRecord.location.ilike(f"%{term}%") for term in all_terms
    ]
    records = (
        db.query(TrafficRecord)
        .filter(
            or_(*traffic_conditions),
            or_(
                TrafficRecord.timestamp >= stale_since,
                TrafficRecord.created_at >= stale_since,
            ),
        )
        .order_by(
            TrafficRecord.timestamp.desc().nullslast(),
            TrafficRecord.created_at.desc(),
        )
        .limit(min(500, max(100, len(locations) * 40)))
        .all()
    )

    incident_conditions = [
        Incident.location.ilike(f"%{term}%") for term in all_terms
    ]
    incident_locations = (
        db.query(Incident.location)
        .filter(
            or_(*incident_conditions),
            Incident.is_active.is_(True),
            Incident.reported_at >= live_since,
        )
        .all()
    )

    def matches(value: str, terms: list[str]) -> bool:
        candidate = (value or "").lower()
        return any(term.lower() in candidate for term in terms)

    result: dict[str, dict] = {}
    for item in locations:
        name = item["location_name"]
        terms = terms_by_name[name]
        record = next(
            (row for row in records if matches(row.location, terms)),
            None,
        )
        incident_count = sum(
            1 for (incident_location,) in incident_locations
            if matches(incident_location, terms)
        )
        if record is None:
            result[name] = {
                "congestion_level": "unknown",
                "average_speed_kmh": None,
                "vehicle_count": None,
                "last_updated": None,
                "active_incidents": incident_count,
                "is_live": False,
                "is_stale": False,
                "message": "No recent traffic data for this location",
            }
            continue

        observed_at = _record_ts(record)
        is_stale = observed_at is None or observed_at < live_since
        result[name] = {
            "congestion_level": "unknown" if is_stale else (record.congestion_level or "unknown"),
            "average_speed_kmh": None if is_stale else record.average_speed,
            "vehicle_count": None if is_stale else record.vehicle_count,
            "last_updated": observed_at.isoformat() if observed_at else None,
            "active_incidents": incident_count,
            "is_live": not is_stale,
            "is_stale": is_stale,
            "data_age_hours": (
                round((now - observed_at).total_seconds() / 3600, 1)
                if observed_at else None
            ),
            "message": (
                "Traffic data is stale — waiting for a fresh reading"
                if is_stale else None
            ),
        }
    return result


@router.post("/", status_code=status.HTTP_201_CREATED)
def add_favorite(
    payload: FavoriteCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Bookmark a location for quick traffic status access.

    Coordinates are optional — when omitted, the name is geocoded against known India locations.
    """
    location_name, lat, lng = _resolve_coords(
        payload.location_name.strip(), payload.latitude, payload.longitude
    )

    existing = (
        db.query(FavoriteLocation)
        .filter(
            FavoriteLocation.user_id == current_user.id,
            FavoriteLocation.location_name == location_name,
        )
        .first()
    )
    if existing:
        if existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{location_name}' is already in your favorites",
            )
        existing.is_active = True
        existing.nickname = payload.nickname
        existing.latitude = lat
        existing.longitude = lng
        db.commit()
        db.refresh(existing)
        logger.info("User %s re-added favorite: %s", current_user.id, location_name)
        return {
            "id": str(existing.id),
            "location_name": existing.location_name,
            "nickname": existing.nickname,
            "latitude": existing.latitude,
            "longitude": existing.longitude,
            "message": "Added to favorites",
        }

    fav = FavoriteLocation(
        user_id=current_user.id,
        location_name=location_name,
        nickname=payload.nickname,
        latitude=lat,
        longitude=lng,
    )
    db.add(fav)
    db.commit()
    db.refresh(fav)
    logger.info("User %s added favorite: %s", current_user.id, location_name)
    return {
        "id": str(fav.id),
        "location_name": fav.location_name,
        "nickname": fav.nickname,
        "latitude": fav.latitude,
        "longitude": fav.longitude,
        "message": "Added to favorites",
    }


@router.get("/", status_code=status.HTTP_200_OK)
def get_favorites(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """List all favorited locations with current live traffic status."""
    favorites = (
        db.query(FavoriteLocation)
        .filter(
            FavoriteLocation.user_id == current_user.id,
            FavoriteLocation.is_active.is_(True),
        )
        .order_by(FavoriteLocation.created_at.asc())
        .all()
    )

    traffic_by_location = _batch_live_status(
        [{"location_name": fav.location_name} for fav in favorites],
        db,
    )
    results = []
    for fav in favorites:
        results.append({
            "id": str(fav.id),
            "location_name": fav.location_name,
            "nickname": fav.nickname or fav.location_name,
            "latitude": fav.latitude,
            "longitude": fav.longitude,
            "traffic_status": traffic_by_location[fav.location_name],
            "created_at": fav.created_at.isoformat(),
        })

    if results:
        return {"total": len(results), "favorites": results}

    traffic_by_location = _batch_live_status(
        [{"location_name": loc["name"]} for loc in _SUGGESTED_LOCATIONS],
        db,
    )
    suggestions = []
    for loc in _SUGGESTED_LOCATIONS:
        suggestions.append({
            "location_name": loc["name"],
            "latitude": loc["lat"],
            "longitude": loc["lng"],
            "traffic_status": traffic_by_location[loc["name"]],
        })

    return {
        "total": 0,
        "favorites": [],
        "message": "No favorites saved yet. Use POST /api/v1/favorites/ to bookmark locations.",
        "suggested_locations": suggestions,
    }


@router.get("/status", status_code=status.HTTP_200_OK)
def get_favorites_status(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Quick traffic status for all favorite locations — optimized for dashboard widgets."""
    favorites = (
        db.query(FavoriteLocation)
        .filter(
            FavoriteLocation.user_id == current_user.id,
            FavoriteLocation.is_active.is_(True),
        )
        .all()
    )

    if not favorites:
        return {"message": "No favorites saved yet", "locations": []}

    traffic_by_location = _batch_live_status(
        [{"location_name": fav.location_name} for fav in favorites],
        db,
    )
    locations = []
    alerts = 0
    for fav in favorites:
        traffic = traffic_by_location[fav.location_name]
        if traffic.get("is_live") and traffic["congestion_level"] == "high":
            alerts += 1
        locations.append({
            "id": str(fav.id),
            "name": fav.nickname or fav.location_name,
            "location_name": fav.location_name,
            **traffic,
        })

    return {
        "total": len(locations),
        "high_congestion_alerts": alerts,
        "locations": locations,
    }


@router.patch("/{favorite_id}", status_code=status.HTTP_200_OK)
def update_favorite(
    favorite_id: uuid.UUID = Path(
        ...,
        description="Favorite UUID — get this from `GET /api/v1/favorites/` (copy any `id`)",
    ),
    nickname: str = Query(..., max_length=100, description="New nickname for this location"),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Update the nickname of a favorited location."""
    fav = db.query(FavoriteLocation).filter(
        FavoriteLocation.id == favorite_id,
        FavoriteLocation.user_id == current_user.id,
        FavoriteLocation.is_active.is_(True),
    ).first()
    if not fav:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Favorite not found")
    cleaned = nickname.strip()
    if not cleaned:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="nickname cannot be empty")
    fav.nickname = cleaned
    db.commit()
    return {"id": str(fav.id), "location_name": fav.location_name, "nickname": fav.nickname}


@router.delete("/{favorite_id}", status_code=status.HTTP_200_OK)
def remove_favorite(
    favorite_id: uuid.UUID = Path(
        ...,
        description="Favorite UUID — get this from `GET /api/v1/favorites/` (copy any `id`)",
    ),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Remove a location from favorites."""
    fav = db.query(FavoriteLocation).filter(
        FavoriteLocation.id == favorite_id,
        FavoriteLocation.user_id == current_user.id,
        FavoriteLocation.is_active.is_(True),
    ).first()
    if not fav:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Favorite not found")
    fav.is_active = False
    db.commit()
    logger.info("User %s removed favorite %s", current_user.id, favorite_id)
    return {"message": "Removed from favorites", "id": str(favorite_id)}
