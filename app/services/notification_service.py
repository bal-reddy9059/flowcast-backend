"""
Notification service for managing push alerts and WebSocket delivery.

Handles notification creation, WebSocket delivery, background congestion monitoring,
and notification history management.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.notification import Notification
from app.models.route import SavedRoute
from app.models.predictor import TrafficRecord
from app.schemas.notification import NotificationSummary, NotificationResponse, WebSocketMessage
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

CONGESTION_RADIUS_DEGREES = 0.05  # ~5.5 km — tight bbox; name lookup preferred
CONGESTION_CHECK_INTERVAL = 60
NOTIFICATION_COOLDOWN_MINUTES = 30


def _latest_high_congestion_near_route(db: Session, route: SavedRoute) -> TrafficRecord | None:
    """Fast congestion probe — indexed name first, then tiny geo bbox. Never raises."""
    from sqlalchemy import text

    since = datetime.now(timezone.utc) - timedelta(hours=2)
    try:
        db.execute(text("SET LOCAL statement_timeout = '600ms'"))
        db.execute(text("SET LOCAL lock_timeout = '300ms'"))

        # 1) Indexed equality on origin_name (preferred)
        if route.origin_name:
            row = (
                db.query(TrafficRecord)
                .filter(
                    TrafficRecord.location == route.origin_name,
                    TrafficRecord.created_at >= since,
                )
                .order_by(TrafficRecord.created_at.desc())
                .limit(1)
                .first()
            )
            if row and (row.congestion_level or "").lower() == "high":
                return row
            # Prefix match for "Hyderabad, Telangana" style names
            row = (
                db.query(TrafficRecord)
                .filter(
                    TrafficRecord.location.ilike(f"{route.origin_name.split(',')[0].strip()}%"),
                    TrafficRecord.created_at >= since,
                    TrafficRecord.congestion_level == "high",
                )
                .order_by(TrafficRecord.created_at.desc())
                .limit(1)
                .first()
            )
            if row:
                return row

        # 2) Small geo bbox — only fetch the columns we need
        lat, lng = float(route.origin_lat), float(route.origin_lng)
        r = CONGESTION_RADIUS_DEGREES
        mapping = db.execute(
            text(
                """
                SELECT location, congestion_level, created_at, average_speed, vehicle_count,
                       latitude, longitude, id
                FROM traffic_records
                WHERE latitude BETWEEN :lat0 AND :lat1
                  AND longitude BETWEEN :lng0 AND :lng1
                  AND created_at >= :since
                  AND congestion_level = 'high'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {
                "lat0": lat - r,
                "lat1": lat + r,
                "lng0": lng - r,
                "lng1": lng + r,
                "since": since,
            },
        ).mappings().first()
        if not mapping:
            return None

        # Lightweight stand-in so callers can use .location / .congestion_level
        class _Snap:
            pass

        snap = _Snap()
        for k, v in mapping.items():
            setattr(snap, k, v)
        return snap  # type: ignore[return-value]
    except Exception as exc:
        logger.warning(
            "Congestion probe skipped for route %s: %s",
            getattr(route, "id", "?"),
            type(exc).__name__,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


async def create_notification(
    user_id: uuid.UUID,
    route_id: Optional[uuid.UUID],
    title: str,
    message: str,
    notification_type: str,
    severity: str,
    location: str,
    db: Session,
) -> Notification:
    """
    Create and persist a notification record to the database.

    Args:
        user_id: ID of the user receiving the notification
        route_id: Optional ID of the associated saved route
        title: Short notification title (max 200 chars)
        message: Full notification message (max 500 chars)
        notification_type: Type of notification (congestion_alert, incident_alert, etc.)
        severity: Alert severity level (low, medium, high, critical)
        location: Location where alert is triggered
        db: Database session

    Returns:
        Created Notification object with ID and timestamps

    Raises:
        Exception: If database commit fails
    """
    try:
        notification = Notification(
            user_id=user_id,
            route_id=route_id,
            title=title,
            message=message,
            notification_type=notification_type,
            severity=severity,
            location=location,
            is_read=False,
            is_sent=False,
        )

        db.add(notification)
        db.commit()
        db.refresh(notification)

        logger.info(
            "Notification created for user %s: %s (type: %s, severity: %s)",
            user_id,
            title,
            notification_type,
            severity,
        )

        return notification

    except Exception as error:
        db.rollback()
        logger.error("Failed to create notification for user %s: %s", user_id, error)
        raise


async def send_websocket_notification(
    user_id: str,
    notification: Notification,
    manager: Any,
    db: Session,
) -> bool:
    """
    Send notification to user via WebSocket (and email if the user opted in).

    Checks UserPreferences for notify_email. If True and SMTP is configured,
    also dispatches an HTML email. WebSocket delivery happens regardless.
    """
    from app.models.preferences import UserPreferences
    from app.models.user import User
    from app.services.email_service import (
        smtp_configured, send_congestion_alert,
        send_departure_alert, send_report_ready, send_generic_notification,
    )

    # ── 1. WebSocket delivery ─────────────────────────────────────────────────
    # Clients often connect with email; callers usually pass UUID — try both.
    from app.utils.api_response import to_ist_iso

    ws_keys = [str(user_id)]
    try:
        import uuid as _uuid
        uid = user_id if isinstance(user_id, _uuid.UUID) else _uuid.UUID(str(user_id))
        user_row = db.query(User).filter(User.id == uid).first()
        if user_row and user_row.email:
            ws_keys.append(user_row.email)
            ws_keys.append(user_row.email.lower())
    except (ValueError, AttributeError, TypeError):
        # user_id may already be an email string
        if "@" in str(user_id):
            ws_keys.append(str(user_id).lower())

    ws_sent = False
    try:
        payload = WebSocketMessage(
            type="notification",
            data={
                "id": str(notification.id),
                "title": notification.title,
                "message": notification.message,
                "severity": notification.severity,
                "type": notification.notification_type,
                "notification_type": notification.notification_type,
                "location": notification.location,
                "created_at": to_ist_iso(notification.created_at),
            },
        )
        delivery_key = ",".join(dict.fromkeys(ws_keys))
        ws_sent = await manager.send_to_user(delivery_key, payload.model_dump(mode="json"))
        if ws_sent:
            logger.info("WebSocket notification sent to user %s (id: %s)", user_id, notification.id)
        else:
            logger.warning(
                "User %s not connected — WS notification %s not delivered",
                user_id,
                notification.id,
            )
    except Exception as error:
        logger.error("WS notification error for user %s: %s", user_id, error)

    # ── 2. Email delivery (if opted in) ──────────────────────────────────────
    email_sent = False
    if smtp_configured():
        try:
            import uuid as _uuid
            uid = _uuid.UUID(str(user_id)) if not isinstance(user_id, _uuid.UUID) else user_id
            prefs = db.query(UserPreferences).filter(UserPreferences.user_id == uid).first()
            if prefs and prefs.notify_email:
                user = db.query(User).filter(User.id == uid).first()
                if user and user.email:
                    ntype = notification.notification_type or ""
                    if "congestion" in ntype or "zone" in ntype or "rule" in ntype:
                        email_sent = await send_congestion_alert(
                            user.email, notification.title, notification.message,
                            notification.location or "", notification.severity or "medium",
                        )
                    elif "departure" in ntype:
                        email_sent = await send_departure_alert(
                            user.email, notification.title, notification.message,
                            notification.location or "",
                        )
                    elif "system" in ntype or "report" in ntype:
                        email_sent = await send_report_ready(
                            user.email, notification.title, notification.message,
                        )
                    else:
                        email_sent = await send_generic_notification(
                            user.email, notification.title, notification.message,
                        )
        except Exception as exc:
            logger.warning("Email delivery error for user %s: %s", user_id, exc)

    # ── 3. Persist delivery status ────────────────────────────────────────────
    try:
        notification.is_sent = ws_sent or email_sent
        channels = []
        if ws_sent:    channels.append("websocket")
        if email_sent: channels.append("email")
        notification.sent_via = ",".join(channels) if channels else "none"
        db.commit()
    except Exception as error:
        db.rollback()
        logger.error("Failed to update notification delivery status: %s", error)

    return ws_sent or email_sent


async def check_saved_routes_for_congestion(db: Session, manager: Any) -> None:
    """
    Background task that checks all active saved routes for high congestion.

    Critical: every DB read is followed by rollback() so we never sit
    idle-in-transaction (that used to lock traffic_records for hours).
    """
    try:
        routes = db.query(SavedRoute).filter(SavedRoute.is_active.is_(True)).all()
        snapshots = [
            {
                "id": r.id,
                "user_id": r.user_id,
                "route_name": r.route_name,
                "origin_lat": r.origin_lat,
                "origin_lng": r.origin_lng,
                "origin_name": r.origin_name,
            }
            for r in routes
        ]
        db.rollback()  # release AccessShareLock immediately

        if not snapshots:
            logger.debug("No active saved routes to check for congestion")
            return

        alerts_sent = 0

        for snap in snapshots:
            class _Route:
                pass

            route = _Route()
            for k, v in snap.items():
                setattr(route, k, v)

            try:
                recent_traffic = _latest_high_congestion_near_route(db, route)
                db.rollback()

                if not recent_traffic:
                    continue

                cooldown_threshold = datetime.now(timezone.utc) - timedelta(
                    minutes=NOTIFICATION_COOLDOWN_MINUTES
                )
                recent_alert = (
                    db.query(Notification)
                    .filter(
                        Notification.user_id == route.user_id,
                        Notification.route_id == route.id,
                        Notification.created_at > cooldown_threshold,
                    )
                    .first()
                )
                db.rollback()

                if recent_alert:
                    logger.debug(
                        "Skipping duplicate alert for route %s (user %s) — alert sent within cooldown period",
                        route.id,
                        route.user_id,
                    )
                    continue

                loc_name = getattr(recent_traffic, "location", "your area")
                notification = await create_notification(
                    user_id=route.user_id,
                    route_id=route.id,
                    title=f"High Traffic Alert — {route.route_name}",
                    message=(
                        f"Heavy congestion detected near {loc_name} "
                        f"on your {route.route_name} route. "
                        f"Expect significant delays."
                    ),
                    notification_type="congestion_alert",
                    severity="high",
                    location=loc_name,
                    db=db,
                )
                db.rollback()

                sent = await send_websocket_notification(
                    user_id=str(route.user_id),
                    notification=notification,
                    manager=manager,
                    db=db,
                )
                db.rollback()

                if sent:
                    alerts_sent += 1

            except Exception as error:
                logger.warning(
                    "Error checking congestion for route %s: %s",
                    getattr(route, "id", "?"),
                    type(error).__name__,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
                continue

        logger.info(
            "Congestion check completed: scanned %s routes — %s alerts sent",
            len(snapshots),
            alerts_sent,
        )

    except Exception as error:
        logger.error("Background congestion check failed: %s", type(error).__name__)
        try:
            db.rollback()
        except Exception:
            pass


async def get_user_notifications(
    user_id: uuid.UUID,
    skip: int,
    limit: int,
    unread_only: bool,
    db: Session,
) -> NotificationSummary:
    """
    Retrieve paginated notifications for a user with summary statistics.

    Args:
        user_id: ID of the user
        skip: Number of records to skip for pagination
        limit: Maximum number of records to return
        unread_only: If True, return only unread notifications
        db: Database session

    Returns:
        NotificationSummary with total, unread, critical counts and paginated notifications

    Raises:
        Exception: If database query fails
    """
    try:
        base_query = db.query(Notification).filter(Notification.user_id == user_id)

        # Summary counts — always unfiltered
        total_count    = base_query.count()
        unread_count   = base_query.filter(Notification.is_read.is_(False)).count()
        critical_count = base_query.filter(Notification.severity == "critical").count()

        # Filtered + paginated results
        filtered_query = db.query(Notification).filter(Notification.user_id == user_id)
        if unread_only:
            filtered_query = filtered_query.filter(Notification.is_read.is_(False))

        page_total = filtered_query.count()
        notifications = (
            filtered_query
            .order_by(Notification.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        notification_responses = [
            NotificationResponse.model_validate(n) for n in notifications
        ]

        logger.info(
            "Retrieved %s notifications for user %s (total=%s, unread=%s, page_total=%s)",
            len(notification_responses), user_id, total_count, unread_count, page_total,
        )

        return NotificationSummary(
            total=total_count,
            unread=unread_count,
            critical=critical_count,
            page_total=page_total,
            notifications=notification_responses,
        )

    except Exception as error:
        logger.error("Failed to retrieve notifications for user %s: %s", user_id, error)
        raise


async def mark_notification_read(
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> Notification:
    """
    Mark a single notification as read by the authenticated user.

    Args:
        notification_id: ID of the notification to mark as read
        user_id: ID of the user (for authorization check)
        db: Database session

    Returns:
        Updated Notification object

    Raises:
        HTTPException 404: If notification not found
        HTTPException 403: If user is not the owner of the notification
        Exception: If database update fails
    """
    try:
        notification = db.query(Notification).filter(
            Notification.id == notification_id
        ).first()

        if not notification:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Notification not found",
            )

        if notification.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to read this notification",
            )

        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)

        logger.info(
            "Notification %s marked as read by user %s",
            notification_id,
            user_id,
        )

        return notification

    except HTTPException:
        raise
    except Exception as error:
        db.rollback()
        logger.error(
            "Failed to mark notification %s as read for user %s: %s",
            notification_id,
            user_id,
            error,
        )
        raise


async def mark_all_read(user_id: uuid.UUID, db: Session) -> dict:
    """
    Mark all unread notifications as read for a user.

    Args:
        user_id: UUID of the user
        db: Database session

    Returns:
        Dict with marked_count, already_read_count, total, and marked_at
    """
    try:
        now = datetime.now(timezone.utc)

        total_count = db.query(Notification).filter(
            Notification.user_id == user_id
        ).count()

        already_read = db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_read.is_(True),
        ).count()

        marked_count = (
            db.query(Notification)
            .filter(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .update(
                {
                    Notification.is_read: True,
                    Notification.read_at: now,
                }
            )
        )

        db.commit()

        logger.info(
            "Marked %s notifications as read for user %s (%s were already read)",
            marked_count,
            user_id,
            already_read,
        )

        return {
            "marked_count": marked_count,
            "already_read_count": already_read,
            "total": total_count,
            "marked_at": now,
        }

    except Exception as error:
        db.rollback()
        logger.error(
            "Failed to mark all notifications as read for user %s: %s",
            user_id,
            error,
        )
        raise
