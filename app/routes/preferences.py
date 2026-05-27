"""User preferences endpoints — notification settings and travel defaults."""

import logging
import uuid
from datetime import timezone
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.preferences import UserPreferences
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/user/preferences", tags=["User Preferences"])
logger = logging.getLogger(__name__)

_VALID_MODES = {"driving", "walking", "transit"}
_VALID_THRESHOLDS = {"low", "medium", "high"}


class PreferencesUpdate(BaseModel):
    preferred_mode: Optional[str] = Field(None, description="driving / walking / transit")
    alert_threshold: Optional[str] = Field(
        None, description="Minimum congestion level to trigger alerts: low / medium / high"
    )
    quiet_hours_start: Optional[int] = Field(None, ge=0, le=23, description="Hour (0-23) to begin quiet hours")
    quiet_hours_end: Optional[int] = Field(None, ge=0, le=23, description="Hour (0-23) to end quiet hours")
    notify_via_websocket: Optional[bool] = None
    notify_email: Optional[bool] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "preferred_mode": "driving",
            "alert_threshold": "high",
            "quiet_hours_start": 22,
            "quiet_hours_end": 7,
            "notify_via_websocket": True,
            "notify_email": False,
        }
    })

    @field_validator("preferred_mode")
    @classmethod
    def validate_mode(cls, v):
        if v and v not in _VALID_MODES:
            raise ValueError("preferred_mode must be driving, walking, or transit")
        return v

    @field_validator("alert_threshold")
    @classmethod
    def validate_threshold(cls, v):
        if v and v not in _VALID_THRESHOLDS:
            raise ValueError("alert_threshold must be low, medium, or high")
        return v


_IST = ZoneInfo("Asia/Kolkata")


def _to_ist(dt) -> str:
    """Convert a naive UTC datetime from the DB to an IST ISO string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()


def _get_or_create(user_id: uuid.UUID, db: Session) -> UserPreferences:
    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user_id).first()
    if not prefs:
        prefs = UserPreferences(user_id=user_id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return prefs


def _prefs_response(prefs: UserPreferences) -> dict:
    return {
        "user_id": prefs.user_id,
        "preferred_mode": prefs.preferred_mode,
        "alert_threshold": prefs.alert_threshold,
        "quiet_hours": {
            "start": prefs.quiet_hours_start,
            "end": prefs.quiet_hours_end,
            "description": (
                f"No alerts from {prefs.quiet_hours_start:02d}:00 to {prefs.quiet_hours_end:02d}:00"
            ),
        },
        "notifications": {
            "websocket": prefs.notify_via_websocket,
            "email": prefs.notify_email,
        },
        "updated_at": _to_ist(prefs.updated_at),
    }


@router.get("/", status_code=status.HTTP_200_OK)
def get_preferences(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Get current user's notification and travel preferences.

    Preferences are created with sensible defaults on first access:
    driving mode, high-congestion alerts only, quiet hours 22:00-07:00.
    """
    prefs = _get_or_create(current_user.id, db)
    return _prefs_response(prefs)


@router.patch("/", status_code=status.HTTP_200_OK)
def update_preferences(
    payload: PreferencesUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Update user preferences. Only provided fields are changed."""
    prefs = _get_or_create(current_user.id, db)

    if payload.preferred_mode is not None:
        prefs.preferred_mode = payload.preferred_mode
    if payload.alert_threshold is not None:
        prefs.alert_threshold = payload.alert_threshold
    if payload.quiet_hours_start is not None:
        prefs.quiet_hours_start = payload.quiet_hours_start
    if payload.quiet_hours_end is not None:
        prefs.quiet_hours_end = payload.quiet_hours_end
    if payload.notify_via_websocket is not None:
        prefs.notify_via_websocket = payload.notify_via_websocket
    if payload.notify_email is not None:
        prefs.notify_email = payload.notify_email

    db.commit()
    db.refresh(prefs)
    logger.info("User %s updated preferences", current_user.id)
    return {"message": "Preferences updated", **_prefs_response(prefs)}
