"""
Push notification endpoints for real-time and historical notifications.

Provides WebSocket live alerts and REST endpoints for notification management.
"""

import asyncio
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationResponse, NotificationSummary
from app.services.auth_service import get_current_user
from app.services.connection_manager import manager
from app.services.notification_service import (
    get_user_notifications,
    mark_all_read,
    mark_notification_read,
)

router = APIRouter(prefix="/notifications", tags=["Push Notifications"])

logger = logging.getLogger(__name__)

WEBSOCKET_KEEPALIVE_INTERVAL = 30


@router.websocket("/ws/{user_id}")
async def websocket_notifications_endpoint(websocket: WebSocket, user_id: int) -> None:
    """
    WebSocket endpoint for real-time push notifications.

    Maintains an active connection per user and sends notifications as they occur.
    Implements keepalive mechanism to detect stale connections.

    Args:
        websocket: WebSocket connection object
        user_id: ID of the user receiving notifications

    Returns:
        None (connection-based protocol)

    Connection flow:
        1. Client connects → receive welcome message
        2. Server sends ping every 30 seconds
        3. Client can send "pong" to acknowledge
        4. On disconnect → cleanup
    """
    try:
        await manager.connect(user_id, websocket)

        welcome_message = {
            "type": "connected",
            "message": "Connected to FlowCast alerts",
            "user_id": user_id,
            "timestamp": asyncio.get_event_loop().time(),
        }
        await websocket.send_json(welcome_message)

        logger.info("User %s connected to WebSocket notifications", user_id)

        while True:
            try:
                # Wait for incoming message with 30 second timeout
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=WEBSOCKET_KEEPALIVE_INTERVAL,
                )

                try:
                    message = asyncio.run(asyncio.create_task(
                        asyncio.to_thread(lambda: {"type": data})
                    ))

                    if data == "pong":
                        logger.debug("Received pong from user %s", user_id)
                        continue

                except Exception:
                    pass

            except asyncio.TimeoutError:
                # Send keepalive ping when timeout occurs
                await manager.send_ping(user_id)
                continue

            except WebSocketDisconnect:
                manager.disconnect(user_id)
                logger.info("User %s WebSocket disconnected", user_id)
                break

            except Exception as error:
                logger.error("WebSocket error for user %s: %s", user_id, error)
                manager.disconnect(user_id)
                break

    except Exception as error:
        logger.error("WebSocket connection failed for user %s: %s", user_id, error)
        manager.disconnect(user_id)


@router.get(
    "/history",
    response_model=NotificationSummary,
    status_code=status.HTTP_200_OK,
)
async def get_notification_history(
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Maximum records to return"),
    unread_only: bool = Query(False, description="Filter to unread notifications only"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSummary:
    """
    Get paginated notification history for the current authenticated user.

    Supports filtering by unread status and pagination controls.

    Args:
        skip: Pagination offset (default: 0)
        limit: Pagination limit (default: 20, max: 100)
        unread_only: If True, return only unread notifications (default: False)
        current_user: Authenticated user from JWT
        db: Database session

    Returns:
        NotificationSummary with aggregated stats and paginated notifications

    Response includes:
        - total: total notifications for user
        - unread: count of unread notifications
        - critical: count of critical severity notifications
        - notifications: paginated list of notification records
    """
    summary = await get_user_notifications(
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        unread_only=unread_only,
        db=db,
    )

    logger.info(
        "User %s retrieved notification history (skip=%s, limit=%s, unread_only=%s)",
        current_user.id,
        skip,
        limit,
        unread_only,
    )

    return summary


@router.post(
    "/mark-read/{notification_id}",
    response_model=NotificationResponse,
    status_code=status.HTTP_200_OK,
)
async def mark_notification_as_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationResponse:
    """
    Mark a single notification as read by the authenticated user.

    Only the notification owner can mark it as read.

    Args:
        notification_id: ID of the notification to mark as read
        current_user: Authenticated user from JWT
        db: Database session

    Returns:
        Updated NotificationResponse

    Raises:
        HTTPException 404: Notification not found
        HTTPException 403: User does not own this notification
    """
    notification = await mark_notification_read(
        notification_id=notification_id,
        user_id=current_user.id,
        db=db,
    )

    return NotificationResponse.model_validate(notification)


@router.post(
    "/mark-all-read",
    status_code=status.HTTP_200_OK,
)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Mark all unread notifications as read for the current authenticated user.

    Args:
        current_user: Authenticated user from JWT
        db: Database session

    Returns:
        Dictionary with success message and count of marked notifications

    Response format:
        {
            "message": "X notifications marked as read",
            "count": X,
            "user_id": user_id
        }
    """
    count = await mark_all_read(user_id=current_user.id, db=db)

    return {
        "message": f"{count} notifications marked as read",
        "count": count,
        "user_id": current_user.id,
    }


@router.get(
    "/stats",
    status_code=status.HTTP_200_OK,
)
async def get_notification_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Get notification statistics for the current authenticated user.

    Includes counts of total, unread, and critical notifications,
    plus timestamp of most recent notification.

    Args:
        current_user: Authenticated user from JWT
        db: Database session

    Returns:
        Dictionary with notification statistics

    Response format:
        {
            "user_id": user_id,
            "total_notifications": int,
            "unread_count": int,
            "critical_count": int,
            "last_notification_at": datetime or None,
            "active_ws_connections": int
        }
    """
    total_count = db.query(func.count(Notification.id)).filter(
        Notification.user_id == current_user.id
    ).scalar() or 0

    unread_count = db.query(func.count(Notification.id)).filter(
        Notification.user_id == current_user.id,
        Notification.is_read.is_(False),
    ).scalar() or 0

    critical_count = db.query(func.count(Notification.id)).filter(
        Notification.user_id == current_user.id,
        Notification.severity == "critical",
    ).scalar() or 0

    last_notification = db.query(func.max(Notification.created_at)).filter(
        Notification.user_id == current_user.id
    ).scalar()

    logger.info(
        "User %s retrieved notification stats (total=%s, unread=%s, critical=%s)",
        current_user.id,
        total_count,
        unread_count,
        critical_count,
    )

    return {
        "user_id": current_user.id,
        "total_notifications": total_count,
        "unread_count": unread_count,
        "critical_count": critical_count,
        "last_notification_at": last_notification,
        "active_ws_connections": manager.get_connection_count(),
    }


# ────────────────────────────────────────────────────────────────────────────────
# TESTING EXAMPLES
# ────────────────────────────────────────────────────────────────────────────────

# SECTION A — JavaScript WebSocket Test (Browser Console)
# ────────────────────────────────────────────────────────────────────────────────
#
# Open browser console (F12) and paste:
#
# ```javascript
# const ws = new WebSocket("ws://localhost:8000/notifications/ws/1")
#
# ws.onopen = () => {
#   console.log("✅ Connected to FlowCast alerts")
# }
#
# ws.onmessage = (event) => {
#   const data = JSON.parse(event.data)
#   console.log("🔔 Alert received:", data)
#
#   // Handle different message types
#   if (data.type === "connected") {
#     console.log("📍 Connection established:", data.message)
#   } else if (data.type === "notification") {
#     console.log("🚨 Notification:", data.data.title, "—", data.data.severity)
#   } else if (data.type === "ping") {
#     ws.send("pong")  // Send keepalive acknowledgement
#   }
# }
#
# ws.onclose = () => {
#   console.log("❌ Disconnected from alerts")
# }
#
# ws.onerror = (event) => {
#   console.error("⚠️ WebSocket error:", event)
# }
# ```
#
# Expected sequence:
# 1. Connection opens → "Connected to FlowCast alerts" message
# 2. Server sends ping every 30 seconds
# 3. On congestion alert → notification message received
#
# Keep this open to receive live alerts!


# SECTION B — curl Commands for REST Endpoints
# ────────────────────────────────────────────────────────────────────────────────

# 1. GET /notifications/history — Get all notifications with pagination
# ```bash
# curl -X GET "http://localhost:8000/notifications/history?skip=0&limit=20" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN"
# ```
#
# Response (200):
# {
#   "total": 5,
#   "unread": 2,
#   "critical": 0,
#   "notifications": [
#     {
#       "id": 5,
#       "user_id": 1,
#       "route_id": 1,
#       "title": "High Traffic Alert — Home to Office",
#       "message": "Heavy congestion detected near Hitech City...",
#       "notification_type": "congestion_alert",
#       "severity": "high",
#       "location": "Hitech City",
#       "is_read": false,
#       "is_sent": true,
#       "sent_via": "websocket",
#       "created_at": "2026-05-07T10:30:00Z",
#       "read_at": null
#     }
#   ]
# }


# 2. GET /notifications/history?unread_only=true — Get only unread notifications
# ```bash
# curl -X GET "http://localhost:8000/notifications/history?unread_only=true&limit=5" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN"
# ```
#
# Returns only notifications where is_read = false


# 3. POST /notifications/mark-read/{notification_id} — Mark single notification as read
# ```bash
# curl -X POST "http://localhost:8000/notifications/mark-read/5" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN"
# ```
#
# Response (200):
# {
#   "id": 5,
#   "user_id": 1,
#   "title": "High Traffic Alert — Home to Office",
#   "is_read": true,
#   "read_at": "2026-05-07T10:35:00Z"
#   ... (full notification fields)
# }
#
# Error cases:
# - 404: "Notification not found"
# - 403: "You do not have permission to read this notification"


# 4. POST /notifications/mark-all-read — Mark all unread as read
# ```bash
# curl -X POST "http://localhost:8000/notifications/mark-all-read" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN"
# ```
#
# Response (200):
# {
#   "message": "3 notifications marked as read",
#   "count": 3,
#   "user_id": 1
# }


# 5. GET /notifications/stats — Get notification statistics
# ```bash
# curl -X GET "http://localhost:8000/notifications/stats" \
#   -H "Authorization: Bearer YOUR_JWT_TOKEN"
# ```
#
# Response (200):
# {
#   "user_id": 1,
#   "total_notifications": 5,
#   "unread_count": 0,
#   "critical_count": 0,
#   "last_notification_at": "2026-05-07T10:30:00Z",
#   "active_ws_connections": 1
# }


# ────────────────────────────────────────────────────────────────────────────────
# COMPLETE TESTING WORKFLOW
# ────────────────────────────────────────────────────────────────────────────────
#
# Step 1: Login to get JWT token
# ```bash
# curl -X POST "http://localhost:8000/auth/login" \
#   -H "Content-Type: application/json" \
#   -d '{"email": "user@example.com", "password": "SecurePass123!"}'
# ```
# Copy the access_token
#
# Step 2: Open WebSocket in browser console (replace user_id with yours):
# ```javascript
# const ws = new WebSocket("ws://localhost:8000/notifications/ws/1")
# ```
# You should see: "✅ Connected to FlowCast alerts"
#
# Step 3: Get notification stats to verify setup:
# ```bash
# curl -X GET "http://localhost:8000/notifications/stats" \
#   -H "Authorization: Bearer YOUR_TOKEN"
# ```
#
# Step 4: Get notification history:
# ```bash
# curl -X GET "http://localhost:8000/notifications/history" \
#   -H "Authorization: Bearer YOUR_TOKEN"
# ```
#
# Step 5: When a congestion alert is triggered (automatically by background task),
# you will see it appear in:
#   - Browser console: "🔔 Alert received: ..."
#   - stats endpoint: unread_count will increase
#   - history endpoint: new notification appears
#
# Step 6: Mark notification as read:
# ```bash
# curl -X POST "http://localhost:8000/notifications/mark-read/1" \
#   -H "Authorization: Bearer YOUR_TOKEN"
# ```
