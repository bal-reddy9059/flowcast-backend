"""
Notification service for managing push alerts and WebSocket delivery.

Handles notification creation, WebSocket delivery, background congestion monitoring,
and notification history management.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.notification import Notification
from app.models.route import SavedRoute
from app.models.predictor import TrafficRecord
from app.schemas.notification import NotificationSummary, NotificationResponse, WebSocketMessage
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

HYDERABAD_RADIUS_DEGREES = 0.02
CONGESTION_CHECK_INTERVAL = 60
NOTIFICATION_COOLDOWN_MINUTES = 30


async def create_notification(
    user_id: int,
    route_id: Optional[int],
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
    user_id: int,
    notification: Notification,
    manager: Any,
    db: Session,
) -> bool:
    """
    Send notification to user via WebSocket and update delivery status.

    Args:
        user_id: ID of the user to send notification to
        notification: Notification object to send
        manager: ConnectionManager instance managing WebSocket connections
        db: Database session

    Returns:
        True if notification sent successfully, False if user not connected

    Raises:
        Exception: If database update fails
    """
    try:
        payload = WebSocketMessage(
            type="notification",
            data={
                "id": notification.id,
                "title": notification.title,
                "message": notification.message,
                "severity": notification.severity,
                "notification_type": notification.notification_type,
                "location": notification.location,
                "created_at": notification.created_at.isoformat(),
            },
        )

        await manager.send_to_user(user_id, payload.model_dump(mode="json"))

        notification.is_sent = True
        notification.sent_via = "websocket"
        db.commit()

        logger.info(
            "WebSocket notification sent to user %s (notification_id: %s)",
            user_id,
            notification.id,
        )

        return True

    except KeyError:
        notification.is_sent = False
        db.commit()

        logger.warning(
            "User %s not connected — notification %s queued for retry",
            user_id,
            notification.id,
        )

        return False

    except Exception as error:
        db.rollback()
        logger.error("Failed to send WebSocket notification to user %s: %s", user_id, error)
        return False


async def check_saved_routes_for_congestion(db: Session, manager: Any) -> None:
    """
    Background task that checks all active saved routes for high congestion.

    Runs periodically to detect congestion on user routes and send alerts.
    Prevents spam by checking if alert was sent in the last 30 minutes.

    Args:
        db: Database session
        manager: ConnectionManager for WebSocket delivery

    Returns:
        None (logs summary of alerts sent)
    """
    try:
        routes = db.query(SavedRoute).filter(SavedRoute.is_active.is_(True)).all()

        if not routes:
            logger.debug("No active saved routes to check for congestion")
            return

        alerts_sent = 0

        for route in routes:
            try:
                recent_traffic = (
                    db.query(TrafficRecord)
                    .filter(
                        TrafficRecord.latitude.between(
                            route.origin_lat - HYDERABAD_RADIUS_DEGREES,
                            route.origin_lat + HYDERABAD_RADIUS_DEGREES,
                        ),
                        TrafficRecord.longitude.between(
                            route.origin_lng - HYDERABAD_RADIUS_DEGREES,
                            route.origin_lng + HYDERABAD_RADIUS_DEGREES,
                        ),
                    )
                    .order_by(TrafficRecord.created_at.desc())
                    .first()
                )

                if not recent_traffic or recent_traffic.congestion_level != "high":
                    continue

                cooldown_threshold = datetime.utcnow() - timedelta(minutes=NOTIFICATION_COOLDOWN_MINUTES)
                recent_alert = (
                    db.query(Notification)
                    .filter(
                        Notification.user_id == route.user_id,
                        Notification.route_id == route.id,
                        Notification.created_at > cooldown_threshold,
                    )
                    .first()
                )

                if recent_alert:
                    logger.debug(
                        "Skipping duplicate alert for route %s (user %s) — alert sent within cooldown period",
                        route.id,
                        route.user_id,
                    )
                    continue

                notification = await create_notification(
                    user_id=route.user_id,
                    route_id=route.id,
                    title=f"High Traffic Alert — {route.route_name}",
                    message=(
                        f"Heavy congestion detected near {recent_traffic.location} "
                        f"on your {route.route_name} route. "
                        f"Expect significant delays."
                    ),
                    notification_type="congestion_alert",
                    severity="high",
                    location=recent_traffic.location,
                    db=db,
                )

                sent = await send_websocket_notification(
                    user_id=route.user_id,
                    notification=notification,
                    manager=manager,
                    db=db,
                )

                if sent:
                    alerts_sent += 1

            except Exception as error:
                logger.error(
                    "Error checking congestion for route %s: %s",
                    route.id,
                    error,
                )
                continue

        logger.info(
            "Congestion check completed: scanned %s routes — %s alerts sent",
            len(routes),
            alerts_sent,
        )

    except Exception as error:
        logger.error("Background congestion check failed: %s", error)


async def get_user_notifications(
    user_id: int,
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
        query = db.query(Notification).filter(Notification.user_id == user_id)

        if unread_only:
            query = query.filter(Notification.is_read.is_(False))

        total_count = query.count()
        unread_count = (
            db.query(Notification)
            .filter(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .count()
        )
        critical_count = (
            db.query(Notification)
            .filter(
                Notification.user_id == user_id,
                Notification.severity == "critical",
            )
            .count()
        )

        notifications = (
            query.order_by(Notification.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        notification_responses = [
            NotificationResponse.model_validate(n) for n in notifications
        ]

        logger.info(
            "Retrieved %s notifications for user %s (total: %s, unread: %s)",
            len(notifications),
            user_id,
            total_count,
            unread_count,
        )

        return NotificationSummary(
            total=total_count,
            unread=unread_count,
            critical=critical_count,
            notifications=notification_responses,
        )

    except Exception as error:
        logger.error("Failed to retrieve notifications for user %s: %s", user_id, error)
        raise


async def mark_notification_read(
    notification_id: int,
    user_id: int,
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
        notification.read_at = datetime.utcnow()
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


async def mark_all_read(user_id: int, db: Session) -> int:
    """
    Mark all unread notifications as read for a user.

    Args:
        user_id: ID of the user
        db: Database session

    Returns:
        Count of notifications marked as read

    Raises:
        Exception: If database update fails
    """
    try:
        now = datetime.utcnow()

        result = (
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
            "Marked %s notifications as read for user %s",
            result,
            user_id,
        )

        return result

    except Exception as error:
        db.rollback()
        logger.error(
            "Failed to mark all notifications as read for user %s: %s",
            user_id,
            error,
        )
        raise
