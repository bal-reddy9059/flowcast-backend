"""
Push notification endpoints for real-time and historical notifications.

Provides WebSocket live alerts and REST endpoints for notification management.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, case
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
from app.utils.api_response import to_ist_iso

router = APIRouter(prefix="/notifications", tags=["Push Notifications"])

logger = logging.getLogger(__name__)

WEBSOCKET_KEEPALIVE_INTERVAL = 30


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_dict(n: NotificationResponse) -> dict:
    """Serialize a NotificationResponse, adding the 'type' alias the frontend expects."""
    return {
        "id":                str(n.id),
        "title":             n.title,
        "message":           n.message,
        "type":              n.notification_type,   # frontend reads .type
        "notification_type": n.notification_type,
        "severity":          n.severity,
        "location":          n.location,
        "is_read":           n.is_read,
        "is_sent":           n.is_sent,
        "created_at":        to_ist_iso(n.created_at),
        "read_at":           to_ist_iso(n.read_at) if n.read_at else None,
    }


def _summary_dict(summary: NotificationSummary) -> dict:
    """Same shape as GET /notifications for history / list consistency."""
    return {
        "total":           summary.total,
        "unread":          summary.unread,
        "unread_critical": summary.critical,
        "critical":        summary.critical,
        "page_total":      summary.page_total,
        "notifications":   [_to_dict(n) for n in summary.notifications],
    }


# ── Frontend-compatible endpoints (must come before parameterised routes) ──────

def _backfill_locations(db: Session) -> None:
    """One-time fix: extract location from notification title for seeded rows with location=None."""
    import re
    rows = (
        db.query(Notification)
        .filter(Notification.location.is_(None))
        .all()
    )
    changed = False
    for n in rows:
        m = re.search(r"—\s+(.+?)(?:\s*→|$)", n.title)
        if m:
            candidate = m.group(1).strip()
            # Skip phrases that are not place names
            if not any(w in candidate.lower() for w in ("leave in", "minutes", "weekly", "active", "ready")):
                n.location = candidate
                changed = True
    if changed:
        try:
            db.commit()
        except Exception:
            db.rollback()


@router.get("", status_code=status.HTTP_200_OK)
async def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """GET /notifications — list current user's notifications, auto-seeding on first call."""
    from sqlalchemy import func as _func
    existing = db.query(_func.count(Notification.id)).filter(
        Notification.user_id == current_user.id
    ).scalar() or 0
    if existing == 0:
        await _seed_notifications(current_user.id, db)

    _backfill_locations(db)

    summary = await get_user_notifications(
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        unread_only=unread_only,
        db=db,
    )
    return _summary_dict(summary)


@router.put("/read-all", status_code=status.HTTP_200_OK)
async def put_mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """PUT /notifications/read-all — mark every unread notification as read."""
    result = await mark_all_read(user_id=current_user.id, db=db)
    marked = result["marked_count"]
    return {
        "message":      f"{marked} notifications marked as read" if marked else "All already read",
        "marked_count": marked,
        "already_read_count": result.get("already_read_count", 0),
        "total_notifications": result.get("total", 0),
        "marked_at":    to_ist_iso(result["marked_at"]) if result.get("marked_at") else to_ist_iso(),
    }


@router.get(
    "/history",
    status_code=status.HTTP_200_OK,
)
async def get_notification_history(
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(20, ge=1, le=100, description="Maximum records to return"),
    unread_only: bool = Query(False, description="Filter to unread notifications only"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Get paginated notification history for the current authenticated user.

    Same response shape as GET /notifications (IST timestamps + type alias).
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

    return _summary_dict(summary)


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

    Auto-seeds 10 realistic notifications on first call when the table is empty.
    """
    uid = current_user.id

    existing = db.query(func.count(Notification.id)).filter(
        Notification.user_id == uid
    ).scalar() or 0
    if existing == 0:
        await _seed_notifications(uid, db)

    row = db.query(
        func.count(Notification.id).label("total"),
        func.sum(case((Notification.is_read.is_(False), 1), else_=0)).label("unread"),
        func.sum(case((Notification.is_read.is_(True),  1), else_=0)).label("read"),
        func.sum(case((Notification.severity == "critical", 1), else_=0)).label("critical"),
        func.sum(case((Notification.severity == "high",     1), else_=0)).label("high"),
        func.sum(case((Notification.severity == "medium",   1), else_=0)).label("medium"),
        func.sum(case((Notification.severity == "low",      1), else_=0)).label("low"),
        func.sum(case(((Notification.severity == "critical") & Notification.is_read.is_(False), 1), else_=0)).label("unread_critical"),
        func.sum(case((Notification.notification_type == "congestion_alert", 1), else_=0)).label("congestion_alerts"),
        func.sum(case((Notification.notification_type == "incident_alert",   1), else_=0)).label("incident_alerts"),
        func.sum(case((Notification.notification_type == "route_update",     1), else_=0)).label("route_updates"),
        func.sum(case((Notification.notification_type == "system",           1), else_=0)).label("system"),
        func.max(Notification.created_at).label("last_at"),
    ).filter(Notification.user_id == uid).one()

    total         = int(row.total or 0)
    unread_count  = int(row.unread or 0)
    read_count    = int(row.read   or 0)
    unread_crit   = int(row.unread_critical or 0)

    logger.info(
        "User %s retrieved notification stats (total=%s, unread=%s, read=%s)",
        uid, total, unread_count, read_count,
    )

    return {
        "user_id":             str(uid),
        "total_notifications": total,
        "unread_count":        unread_count,
        "read_count":          read_count,
        "total":               total,
        "unread":              unread_count,
        "unread_critical":     unread_crit,
        "severity_breakdown": {
            "critical": int(row.critical or 0),
            "high":     int(row.high     or 0),
            "medium":   int(row.medium   or 0),
            "low":      int(row.low      or 0),
        },
        "type_breakdown": {
            "congestion_alert": int(row.congestion_alerts or 0),
            "incident_alert":   int(row.incident_alerts   or 0),
            "route_update":     int(row.route_updates     or 0),
            "system":           int(row.system            or 0),
        },
        "last_notification_at":  to_ist_iso(row.last_at) if row.last_at else None,
        "active_ws_connections": manager.get_connection_count(),
    }


@router.post(
    "/mark-all-read",
    status_code=status.HTTP_200_OK,
)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Mark all unread notifications as read (alias of PUT /read-all).
    """
    result = await mark_all_read(user_id=current_user.id, db=db)

    marked  = result["marked_count"]
    already = result["already_read_count"]
    total   = result["total"]

    return {
        "message": (
            f"{marked} notifications marked as read"
            if marked > 0
            else "All notifications were already read"
        ),
        "marked_count":       marked,
        "already_read_count": already,
        "total_notifications": total,
        "user_id":   str(current_user.id),
        "marked_at": to_ist_iso(result["marked_at"]) if result.get("marked_at") else to_ist_iso(),
    }


@router.post(
    "/test",
    status_code=status.HTTP_201_CREATED,
)
async def send_test_notification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Create a sample notification for the current user.

    Useful for verifying that the notification history and WebSocket delivery work.
    """
    from app.services.notification_service import create_notification
    from app.services.connection_manager import manager as ws_manager
    from app.services.notification_service import send_websocket_notification

    severities = [
        ("High Traffic Alert — Hitech City",   "Heavy congestion detected near Hitech City. Expect delays of 20-30 minutes.", "congestion_alert", "high",     "Hitech City"),
        ("Moderate Delay — Gachibowli",        "Moderate traffic on your Gachibowli route. Allow extra 10 minutes.",          "congestion_alert", "medium",   "Gachibowli"),
        ("Incident Reported — Ameerpet",       "Road incident reported near Ameerpet. Consider alternate routes.",             "incident_alert",   "critical", "Ameerpet"),
    ]
    import random
    title, message, ntype, severity, location = random.choice(severities)

    notification = await create_notification(
        user_id=current_user.id,
        route_id=None,
        title=title,
        message=message,
        notification_type=ntype,
        severity=severity,
        location=location,
        db=db,
    )
    ws_delivered = await send_websocket_notification(
        user_id=str(current_user.id),
        notification=notification,
        manager=ws_manager,
        db=db,
    )

    logger.info("Test notification created for user %s", current_user.id)
    return {
        "message": "Test notification created successfully.",
        "notification_id": str(notification.id),
        "title":    notification.title,
        "severity": notification.severity,
        "location": notification.location,
        "websocket_delivered": bool(ws_delivered),
        "created_at": to_ist_iso(notification.created_at),
    }


@router.post(
    "/mark-read/{notification_id}",
    status_code=status.HTTP_200_OK,
)
async def mark_notification_as_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Mark a single notification as read (alias of PUT /{id}/read).
    """
    notification = await mark_notification_read(
        notification_id=notification_id,
        user_id=current_user.id,
        db=db,
    )
    return {**_to_dict(NotificationResponse.model_validate(notification)), "message": "Marked as read"}


@router.put("/{notification_id}/read", status_code=status.HTTP_200_OK)
async def put_mark_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """PUT /notifications/{id}/read — mark a single notification as read."""
    notification = await mark_notification_read(
        notification_id=notification_id,
        user_id=current_user.id,
        db=db,
    )
    return {**_to_dict(NotificationResponse.model_validate(notification)), "message": "Marked as read"}


@router.delete("/{notification_id}", status_code=status.HTTP_200_OK)
async def delete_notification(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """DELETE /notifications/{id} — permanently remove a notification."""
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    db.delete(notif)
    db.commit()
    logger.info("User %s deleted notification %s", current_user.id, notification_id)
    return {"message": "Notification deleted", "id": str(notification_id)}


@router.websocket("/ws/{user_id}")
async def websocket_notifications_endpoint(websocket: WebSocket, user_id: str) -> None:
    """
    WebSocket endpoint for real-time push notifications.

    Path may be email or UUID. Optional ``?token=`` registers both email and UUID
    aliases so pushes keyed by either identifier reach the client.
    """
    connect_key = user_id.strip()
    aliases: list[str] = []

    token = websocket.query_params.get("token")
    if token:
        try:
            from app.services.auth_service import decode_access_token
            from app.database import SessionLocal

            token_data = decode_access_token(token)
            if token_data.email:
                aliases.append(token_data.email)
            if token_data.user_id:
                aliases.append(str(token_data.user_id))
            # Prefer UUID as primary when token provides it
            if token_data.user_id and "@" in connect_key:
                aliases.append(connect_key)
                connect_key = str(token_data.user_id)
        except Exception as error:
            logger.debug("WS token alias resolve failed: %s", error)

    # If path is email without token, look up UUID so UUID-keyed pushes still work
    if "@" in connect_key and str(connect_key) not in aliases:
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.email.ilike(connect_key)).first()
                if user:
                    aliases.append(str(user.id))
                    aliases.append(user.email)
            finally:
                db.close()
        except Exception as error:
            logger.debug("WS email→UUID lookup failed: %s", error)

    if "@" in connect_key:
        aliases.append(connect_key.lower())

    try:
        await manager.connect(connect_key, websocket, aliases=aliases)

        welcome_message = {
            "type": "connected",
            "message": "Connected to FlowCast alerts",
            "user_id": connect_key,
            "timestamp": to_ist_iso(),
        }
        await websocket.send_json(welcome_message)

        logger.info("User %s connected to WebSocket notifications", connect_key)

        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=WEBSOCKET_KEEPALIVE_INTERVAL,
                )

                if data == "pong":
                    logger.debug("Received pong from user %s", connect_key)
                    continue

            except asyncio.TimeoutError:
                await manager.send_ping(connect_key)
                continue

            except WebSocketDisconnect:
                manager.disconnect(connect_key)
                logger.info("User %s WebSocket disconnected", connect_key)
                break

            except Exception as error:
                logger.error("WebSocket error for user %s: %s", connect_key, error)
                manager.disconnect(connect_key)
                break

    except Exception as error:
        logger.error("WebSocket connection failed for user %s: %s", connect_key, error)
        manager.disconnect(connect_key)


async def _seed_notifications(user_id: uuid.UUID, db: Session) -> None:
    """
    Create a realistic initial batch of notifications for a new user.
    Uses real location names from the traffic data when available.
    """
    from datetime import timedelta
    from app.models.predictor import TrafficRecord

    now = datetime.now(timezone.utc)

    # Pull real high-congestion locations from DB; fall back to static names
    recent = (
        db.query(TrafficRecord.location)
        .filter(
            TrafficRecord.congestion_level == "high",
            TrafficRecord.created_at >= now - timedelta(hours=6),
        )
        .order_by(TrafficRecord.created_at.desc())
        .limit(10)
        .all()
    )
    hot_locs = list({r.location for r in recent if r.location})
    if len(hot_locs) < 3:
        hot_locs += ["Silk Board Junction", "Hitech City", "Ameerpet", "Gachibowli", "Koramangala"]
    hot_locs = hot_locs[:5]

    l0 = hot_locs[0]
    l1 = hot_locs[1]
    l2 = hot_locs[2]
    l3 = hot_locs[min(3, len(hot_locs) - 1)]
    l4 = hot_locs[min(4, len(hot_locs) - 1)]

    seed_data = [
        # (title, message, type, severity, is_read, minutes_ago, location)
        (
            f"Critical Congestion — {l0}",
            f"Severe gridlock at {l0}. Speed dropped to under 5 km/h. Avoid this route for the next 45 minutes.",
            "congestion_alert", "critical", False, 8, l0,
        ),
        (
            f"High Traffic Alert — {l1}",
            f"Heavy congestion detected near {l1}. Expect delays of 20–30 minutes on your usual route.",
            "congestion_alert", "high", False, 22, l1,
        ),
        (
            f"Accident Reported — {l2}",
            f"Road accident blocking 2 lanes near {l2}. Emergency services on site. Use alternate routes.",
            "incident_alert", "critical", False, 35, l2,
        ),
        (
            f"Road Closure — {l3}",
            f"Partial road closure near {l3} due to water-main work. One lane open.",
            "incident_alert", "high", False, 55, l3,
        ),
        (
            f"Moderate Delay — {l4}",
            f"Moderate traffic buildup at {l4}. Allow 10 extra minutes.",
            "congestion_alert", "medium", True, 90, l4,
        ),
        (
            "Route Optimized — Koramangala → Whitefield",
            "A faster route via Outer Ring Road is now available. Saves approximately 12 minutes.",
            "route_update", "low", True, 130, "Koramangala",
        ),
        (
            "Traffic Clearing — MG Road",
            "Congestion on MG Road has cleared. Normal speeds resumed — good time to travel.",
            "congestion_alert", "low", True, 180, "MG Road",
        ),
        (
            "Departure Alert — Leave in 10 minutes",
            "Based on current traffic, you should leave in 10 minutes to reach your destination on time.",
            "route_update", "medium", True, 240, None,
        ),
        (
            "FlowCast Live Traffic Active",
            "Real-time monitoring is active for your area. You'll receive alerts for congestion, incidents, and route changes.",
            "system", "low", True, 360, None,
        ),
        (
            "Weekly Traffic Summary Ready",
            "Your traffic report for the past 7 days is ready. Average commute time improved by 8% this week.",
            "system", "low", True, 1440, None,
        ),
    ]

    for title, message, ntype, severity, is_read, minutes_ago, location in seed_data:
        created = now - timedelta(minutes=minutes_ago)
        n = Notification(
            user_id=user_id,
            route_id=None,
            title=title,
            message=message,
            notification_type=ntype,
            severity=severity,
            location=location,
            is_read=is_read,
            is_sent=True,
            sent_via="system",
            created_at=created,
            read_at=created + timedelta(minutes=5) if is_read else None,
        )
        db.add(n)

    try:
        db.commit()
        logger.info("Seeded 10 initial notifications for user %s", user_id)
    except Exception as exc:
        db.rollback()
        logger.error("Notification seed failed for user %s: %s", user_id, exc)


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
