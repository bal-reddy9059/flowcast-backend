"""Favorite locations endpoints — bookmark Hyderabad spots for quick status checks."""

import logging
import uuid
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.favorite import FavoriteLocation
from app.models.predictor import Incident, TrafficRecord
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/favorites", tags=["Favorite Locations"])
logger = logging.getLogger(__name__)

_SUGGESTED_LOCATIONS = [
    {"name": "Hitech City",   "lat": 17.4486, "lng": 78.3908},
    {"name": "Gachibowli",   "lat": 17.4401, "lng": 78.3489},
    {"name": "Ameerpet",     "lat": 17.4375, "lng": 78.4483},
    {"name": "Kukatpally",   "lat": 17.4848, "lng": 78.4138},
    {"name": "Secunderabad", "lat": 17.4399, "lng": 78.4983},
]


class FavoriteCreate(BaseModel):
    location_name: str = Field(..., min_length=2, max_length=200, description="Hyderabad location name")
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


class FavoriteResponse(BaseModel):
    id: int
    location_name: str
    nickname: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    created_at: str

    model_config = {"from_attributes": True}


def _live_status(location_name: str, db: Session) -> dict:
    """Get the latest traffic record for a location."""
    record = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.location.ilike(f"%{location_name}%"))
        .order_by(TrafficRecord.created_at.desc())
        .first()
    )
    incident_count = (
        db.query(Incident)
        .filter(
            Incident.location.ilike(f"%{location_name}%"),
            Incident.is_active.is_(True),
        )
        .count()
    )
    if record:
        return {
            "congestion_level": record.congestion_level or "unknown",
            "average_speed_kmh": record.average_speed,
            "vehicle_count": record.vehicle_count,
            "last_updated": record.created_at.isoformat() if record.created_at else None,
            "active_incidents": incident_count,
        }
    return {
        "congestion_level": "unknown",
        "average_speed_kmh": None,
        "vehicle_count": None,
        "last_updated": None,
        "active_incidents": incident_count,
    }


@router.post("/", status_code=status.HTTP_201_CREATED)
def add_favorite(
    payload: FavoriteCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Bookmark a Hyderabad location for quick traffic status access."""
    existing = (
        db.query(FavoriteLocation)
        .filter(
            FavoriteLocation.user_id == current_user.id,
            FavoriteLocation.location_name == payload.location_name,
        )
        .first()
    )
    if existing:
        if existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{payload.location_name}' is already in your favorites",
            )
        # Reactivate a previously removed favorite instead of inserting a new row
        # (a new INSERT would violate the unique constraint on user_id+location_name)
        existing.is_active = True
        existing.nickname = payload.nickname
        existing.latitude = payload.latitude
        existing.longitude = payload.longitude
        db.commit()
        db.refresh(existing)
        logger.info("User %s re-added favorite: %s", current_user.id, payload.location_name)
        return {
            "id": str(existing.id),
            "location_name": existing.location_name,
            "nickname": existing.nickname,
            "message": "Added to favorites",
        }

    fav = FavoriteLocation(
        user_id=current_user.id,
        location_name=payload.location_name,
        nickname=payload.nickname,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    db.add(fav)
    db.commit()
    db.refresh(fav)
    logger.info("User %s added favorite: %s", current_user.id, payload.location_name)
    return {
        "id": str(fav.id),
        "location_name": fav.location_name,
        "nickname": fav.nickname,
        "message": "Added to favorites",
    }


@router.get("/", status_code=status.HTTP_200_OK)
def get_favorites(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """List all favorited locations with their current live traffic status.

    When the user has no saved favorites yet, returns suggested popular
    Hyderabad locations with live traffic so the response is always useful.
    """
    favorites = (
        db.query(FavoriteLocation)
        .filter(
            FavoriteLocation.user_id == current_user.id,
            FavoriteLocation.is_active.is_(True),
        )
        .order_by(FavoriteLocation.created_at.asc())
        .all()
    )

    results = []
    for fav in favorites:
        traffic = _live_status(fav.location_name, db)
        results.append({
            "id": str(fav.id),
            "location_name": fav.location_name,
            "nickname": fav.nickname or fav.location_name,
            "latitude": fav.latitude,
            "longitude": fav.longitude,
            "traffic_status": traffic,
            "created_at": fav.created_at.isoformat(),
        })

    if results:
        return {"total": len(results), "favorites": results}

    # No bookmarks yet — return suggested popular spots with live traffic
    suggestions = []
    for loc in _SUGGESTED_LOCATIONS:
        traffic = _live_status(loc["name"], db)
        suggestions.append({
            "location_name": loc["name"],
            "latitude": loc["lat"],
            "longitude": loc["lng"],
            "traffic_status": traffic,
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

    locations = []
    alerts = 0
    for fav in favorites:
        traffic = _live_status(fav.location_name, db)
        if traffic["congestion_level"] == "high":
            alerts += 1
        locations.append({
            "id": str(fav.id),
            "name": fav.nickname or fav.location_name,
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
    fav.nickname = nickname.strip()
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
