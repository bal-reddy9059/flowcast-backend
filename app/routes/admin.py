"""Admin dashboard endpoints for FlowCast system monitoring.

All endpoints require admin authentication.
"""

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.alert import DepartureAlert
from app.models.favorite import FavoriteLocation
from app.models.notification import Notification
from app.models.predictor import Incident, PredictionResult, TrafficRecord
from app.models.preferences import UserPreferences
from app.models.route import SavedRoute
from app.models.share import RouteShareToken
from app.models.trip import TripHistory
from app.models.user import User
from app.schemas.admin import DatabaseStats, SystemStats, TableInfo
from app.schemas.user import UserResponse
from app.services.auth_service import get_current_admin_user
from app.services.connection_manager import manager as ws_manager

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)

_start_time = time.time()


@router.get("/stats", response_model=SystemStats)
def get_system_stats(
    current_user: Annotated[User, Depends(get_current_admin_user)],
    db: Session = Depends(get_db),
) -> SystemStats:
    """System-level overview â€” users, records, incidents, uptime, WS connections."""
    # Use naive UTC midnight â€” DB stores DateTime without timezone info
    today = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)

    return SystemStats(
        total_users=db.query(func.count(User.id)).scalar() or 0,
        active_users_today=(
            db.query(func.count(User.id))
            .filter(User.last_login >= today)
            .scalar() or 0
        ),
        total_traffic_records=db.query(func.count(TrafficRecord.id)).scalar() or 0,
        records_today=(
            db.query(func.count(TrafficRecord.id))
            .filter(TrafficRecord.created_at >= today)
            .scalar() or 0
        ),
        total_predictions=db.query(func.count(PredictionResult.id)).scalar() or 0,
        total_incidents=db.query(func.count(Incident.id)).scalar() or 0,
        total_notifications=db.query(func.count(Notification.id)).scalar() or 0,
        unread_notifications=(
            db.query(func.count(Notification.id))
            .filter(Notification.is_read.is_(False))
            .scalar() or 0
        ),
        active_ws_connections=ws_manager.get_connection_count(),
        cache_hit_rate=0.0,
        uptime_seconds=int(time.time() - _start_time),
        api_version="1.0.0",
    )


@router.get("/users", response_model=List[UserResponse])
def list_users(
    skip: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=200, description="Max users to return"),
    active_only: bool = Query(False, description="Only return active users"),
    current_user: Annotated[User, Depends(get_current_admin_user)] = None,
    db: Session = Depends(get_db),
) -> List[UserResponse]:
    """Paginated list of all registered users."""
    query = db.query(User)
    if active_only:
        query = query.filter(User.is_active.is_(True))
    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    logger.info("Admin user list requested by %s â€” returned %s", current_user.id, len(users))
    return [UserResponse.model_validate(u) for u in users]


@router.patch("/users/{user_id}/deactivate", response_model=UserResponse)
def deactivate_user(
    user_id: uuid.UUID = Path(
        ...,
        description="User UUID â€” get this from `GET /api/v1/admin/users` (copy any `id`)",
    ),
    current_user: Annotated[User, Depends(get_current_admin_user)] = None,
    db: Session = Depends(get_db),
) -> UserResponse:
    """Deactivate a user account so they can no longer log in."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = False
    db.commit()
    db.refresh(user)
    logger.info("User %s deactivated by admin %s", user_id, current_user.id)
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}/activate", response_model=UserResponse)
def activate_user(
    user_id: uuid.UUID = Path(
        ...,
        description="User UUID â€” get this from `GET /api/v1/admin/users` (copy any `id`)",
    ),
    current_user: Annotated[User, Depends(get_current_admin_user)] = None,
    db: Session = Depends(get_db),
) -> UserResponse:
    """Reactivate a previously deactivated user account."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = True
    db.commit()
    db.refresh(user)
    logger.info("User %s activated by admin %s", user_id, current_user.id)
    return UserResponse.model_validate(user)


@router.get("/db", response_model=DatabaseStats)
def get_db_stats(
    current_user: Annotated[User, Depends(get_current_admin_user)],
    db: Session = Depends(get_db),
) -> DatabaseStats:
    """Database health â€” table row counts, DB size, oldest/newest records."""
    tables = [
        TableInfo(name="users",              row_count=db.query(func.count(User.id)).scalar() or 0),
        TableInfo(name="traffic_records",    row_count=db.query(func.count(TrafficRecord.id)).scalar() or 0),
        TableInfo(name="prediction_results", row_count=db.query(func.count(PredictionResult.id)).scalar() or 0),
        TableInfo(name="incidents",          row_count=db.query(func.count(Incident.id)).scalar() or 0),
        TableInfo(name="notifications",      row_count=db.query(func.count(Notification.id)).scalar() or 0),
        TableInfo(name="saved_routes",       row_count=db.query(func.count(SavedRoute.id)).scalar() or 0),
        TableInfo(name="favorite_locations", row_count=db.query(func.count(FavoriteLocation.id)).scalar() or 0),
        TableInfo(name="user_preferences",   row_count=db.query(func.count(UserPreferences.id)).scalar() or 0),
        TableInfo(name="trip_history",       row_count=db.query(func.count(TripHistory.id)).scalar() or 0),
        TableInfo(name="departure_alerts",   row_count=db.query(func.count(DepartureAlert.id)).scalar() or 0),
        TableInfo(name="route_share_tokens", row_count=db.query(func.count(RouteShareToken.id)).scalar() or 0),
    ]

    try:
        size_bytes = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
        db_size_mb = round(size_bytes / (1024 * 1024), 2) if size_bytes else 0.0
    except Exception:
        db_size_mb = 0.0

    oldest = db.query(func.min(TrafficRecord.created_at)).scalar() or datetime.now(timezone.utc)
    newest = db.query(func.max(TrafficRecord.created_at)).scalar() or datetime.now(timezone.utc)

    return DatabaseStats(
        total_records=sum(t.row_count for t in tables),
        db_size_mb=db_size_mb,
        oldest_record=oldest,
        newest_record=newest,
        tables=tables,
    )


@router.delete("/traffic/old-records", status_code=status.HTTP_200_OK)
def purge_old_traffic_records(
    days_old: int = Query(30, ge=7, le=365, description="Delete records older than N days"),
    current_user: Annotated[User, Depends(get_current_admin_user)] = None,
    db: Session = Depends(get_db),
) -> dict:
    """Hard-delete traffic records older than N days to control DB growth."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)
    deleted = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info(
        "Admin %s purged %s traffic records older than %s days",
        current_user.id, deleted, days_old,
    )
    return {"deleted_records": deleted, "cutoff_date": cutoff.isoformat()}
