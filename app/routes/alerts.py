"""Smart departure alerts — get notified N minutes before you need to leave."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, List, Optional, Union
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.alert import DepartureAlert
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/alerts/departure", tags=["Departure Alerts"])
logger = logging.getLogger(__name__)

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DAY_FULL  = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_MAP   = {name: i for i, name in enumerate(_DAY_FULL)}
# Also accept short forms from the API / frontend
_DAY_MAP.update({abbr.lower(): i for i, abbr in enumerate(_DAY_NAMES)})
_DAY_MAP.update({"tues": 1, "thur": 3, "thurs": 3})
_IST = ZoneInfo("Asia/Kolkata")


def _to_ist(dt: datetime) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()


def _estimate_distance_km(origin: str, destination: str) -> Optional[float]:
    """Geocode both ends and return road-ish km (haversine × 1.25), or None."""
    try:
        from app.routes.route import _geocode, _haversine_km
    except Exception:
        return None
    o = _geocode(origin)
    d = _geocode(destination)
    if not o or not d:
        return None
    straight = _haversine_km(o["lat"], o["lng"], d["lat"], d["lng"])
    return round(straight * 1.25, 1)


def _next_trigger_ist(alert: DepartureAlert, now: Optional[datetime] = None) -> Optional[str]:
    """Next IST datetime when this alert should fire (departure − notice)."""
    now = now or datetime.now(_IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_IST)
    else:
        now = now.astimezone(_IST)

    try:
        dep_h, dep_m = map(int, alert.departure_time.split(":"))
    except Exception:
        return None
    notice = alert.advance_notice_minutes or 15
    scheduled = {int(d) for d in alert.days_of_week.split(",") if d.strip().isdigit()}
    if not scheduled:
        return None

    for offset in range(0, 8):
        day = now + timedelta(days=offset)
        if day.weekday() not in scheduled:
            continue
        fire = day.replace(hour=dep_h, minute=dep_m, second=0, microsecond=0) - timedelta(minutes=notice)
        if fire > now:
            return fire.isoformat()
    return None


class AlertCreate(BaseModel):
    route_name: str = Field(..., min_length=2, max_length=100)
    # Accept either origin_name (API) or origin (frontend form field)
    origin_name: Optional[str] = Field(None, min_length=2, max_length=200)
    destination_name: Optional[str] = Field(None, min_length=2, max_length=200)
    origin: Optional[str] = Field(None, min_length=2, max_length=200)
    destination: Optional[str] = Field(None, min_length=2, max_length=200)
    departure_time: str = Field(..., description="HH:MM (Asia/Kolkata)")
    # Accept integers [0-6] OR day-name strings ['monday', 'Mon', ...]
    days_of_week: List[Union[int, str]] = Field(...)
    advance_notice_minutes: int = Field(15, ge=5, le=120)
    mode: str = Field("driving")
    distance_km: Optional[float] = Field(None, gt=0, le=500)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "route_name": "Morning Commute",
            "origin": "Gachibowli",
            "destination": "Hitech City",
            "departure_time": "08:30",
            "days_of_week": ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "advance_notice_minutes": 15,
        }
    })

    @field_validator("departure_time")
    @classmethod
    def validate_time(cls, v):
        try:
            datetime.strptime(v, "%H:%M")
        except ValueError:
            raise ValueError("departure_time must be HH:MM, e.g. 08:30")
        return v

    @field_validator("days_of_week", mode="before")
    @classmethod
    def normalize_days(cls, v):
        if not v:
            raise ValueError("days_of_week must be a non-empty list")
        converted = []
        for d in v:
            if isinstance(d, str):
                idx = _DAY_MAP.get(d.lower().strip())
                if idx is None:
                    raise ValueError(f"Unknown day name: {d!r}. Use monday–sunday or 0–6.")
                converted.append(idx)
            elif isinstance(d, int):
                if not 0 <= d <= 6:
                    raise ValueError("Integer days must be 0–6")
                converted.append(d)
            else:
                raise ValueError(f"days_of_week items must be strings or integers, got {type(d)}")
        return sorted(set(converted))

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        if v not in {"driving", "walking", "transit"}:
            raise ValueError("mode must be driving, walking, or transit")
        return v


def _fmt_alert(a: DepartureAlert) -> dict:
    indices = [int(d) for d in a.days_of_week.split(",") if d.strip().isdigit()]
    days_abbr = [_DAY_NAMES[i] for i in indices]
    days_full = [_DAY_FULL[i] for i in indices]
    result = {
        "id":                     str(a.id),
        "route_name":             a.route_name,
        "origin":                 a.origin_name,
        "destination":            a.destination_name,
        "departure_time":         a.departure_time,
        "timezone":               "Asia/Kolkata",
        "days":                   days_abbr,
        "days_of_week":           days_full,
        "advance_notice_minutes": a.advance_notice_minutes,
        "mode":                   a.mode,
        "distance_km":            a.distance_km,
        "is_active":              a.is_active,
        "created_at":             _to_ist(a.created_at),
        "next_trigger_at":        _next_trigger_ist(a) if a.is_active else None,
    }
    if a.last_triggered_at is not None:
        result["last_triggered_at"] = _to_ist(a.last_triggered_at)
    return result


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_alert(
    payload: AlertCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """Create a departure time alert.

    The background task will send you a WebSocket notification
    `advance_notice_minutes` before your scheduled departure on the configured days
    (times are interpreted in Asia/Kolkata).
    """
    existing = db.query(DepartureAlert).filter(
        DepartureAlert.user_id == current_user.id,
        DepartureAlert.route_name == payload.route_name,
        DepartureAlert.departure_time == payload.departure_time,
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"An alert named '{payload.route_name}' at {payload.departure_time} already exists. "
                f"Delete or update the existing alert (id: {existing.id})."
            ),
        )

    origin_name = payload.origin_name or payload.origin
    destination_name = payload.destination_name or payload.destination
    if not origin_name or not destination_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="origin (or origin_name) and destination (or destination_name) are required",
        )

    distance_km = payload.distance_km
    if distance_km is None:
        distance_km = _estimate_distance_km(origin_name, destination_name)

    days_str = ",".join(str(d) for d in sorted(payload.days_of_week))
    alert = DepartureAlert(
        user_id=current_user.id,
        route_name=payload.route_name,
        origin_name=origin_name,
        destination_name=destination_name,
        departure_time=payload.departure_time,
        days_of_week=days_str,
        advance_notice_minutes=payload.advance_notice_minutes,
        mode=payload.mode,
        distance_km=distance_km,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    logger.info(
        "User %s created departure alert '%s' at %s IST",
        current_user.id, payload.route_name, payload.departure_time,
    )
    return {
        **_fmt_alert(alert),
        "message": (
            f"Alert set — you'll be notified {payload.advance_notice_minutes} min "
            f"before {payload.departure_time} IST"
        ),
    }


@router.get("/", status_code=status.HTTP_200_OK)
def list_alerts(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    active_only: bool = Query(False, description="Return only enabled alerts"),
) -> dict:
    """List all departure alerts for the current user."""
    query = db.query(DepartureAlert).filter(DepartureAlert.user_id == current_user.id)
    if active_only:
        query = query.filter(DepartureAlert.is_active.is_(True))
    alerts = query.order_by(DepartureAlert.departure_time.asc()).all()
    return {"total": len(alerts), "alerts": [_fmt_alert(a) for a in alerts]}


def _do_toggle(alert_id: uuid.UUID, current_user: User, db: Session) -> dict:
    alert = db.query(DepartureAlert).filter(
        DepartureAlert.id == alert_id,
        DepartureAlert.user_id == current_user.id,
    ).first()
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    alert.is_active = not alert.is_active
    db.commit()
    db.refresh(alert)
    state = "enabled" if alert.is_active else "disabled"
    logger.info("User %s %s departure alert %s", current_user.id, state, alert_id)
    return {
        "id":              str(alert.id),
        "route_name":      alert.route_name,
        "departure_time":  alert.departure_time,
        "is_active":       alert.is_active,
        "next_trigger_at": _next_trigger_ist(alert) if alert.is_active else None,
        "message":         f"Alert '{alert.route_name}' {state}",
    }


@router.patch("/{alert_id}/toggle", status_code=status.HTTP_200_OK)
def toggle_alert(
    alert_id: uuid.UUID = Path(..., description="Alert UUID"),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Enable or disable a departure alert without deleting it."""
    return _do_toggle(alert_id, current_user, db)


@router.put("/{alert_id}/toggle", status_code=status.HTTP_200_OK)
def toggle_alert_put(
    alert_id: uuid.UUID = Path(..., description="Alert UUID"),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Enable or disable a departure alert (PUT alias for PATCH)."""
    return _do_toggle(alert_id, current_user, db)


@router.delete("/{alert_id}", status_code=status.HTTP_200_OK)
def delete_alert(
    alert_id: uuid.UUID = Path(..., description="Alert UUID — from `GET /api/v1/alerts/departure/`"),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Permanently delete a departure alert."""
    alert = db.query(DepartureAlert).filter(
        DepartureAlert.id == alert_id,
        DepartureAlert.user_id == current_user.id,
    ).first()
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    db.delete(alert)
    db.commit()
    logger.info("User %s deleted departure alert %s", current_user.id, alert_id)
    return {"message": "Alert deleted", "id": str(alert_id)}
