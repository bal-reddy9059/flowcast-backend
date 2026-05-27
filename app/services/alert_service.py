"""Background task: fire departure alert notifications before scheduled departure times."""

import logging
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.alert import DepartureAlert
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)

# Prevent double-firing within this window
_COOLDOWN_MINUTES = 30


async def check_departure_alerts(ws_manager) -> None:
    """Check all active departure alerts and send a WebSocket notification when due.

    Runs every 60 s from the app lifespan. An alert fires when the current time
    is within 1 minute of (departure_time − advance_notice_minutes) on a scheduled day.
    A 30-minute cooldown stops duplicate notifications for the same alert.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        now_naive = datetime.utcnow()   # naive UTC for comparing with DB DateTime columns
        today_dow = now.weekday()       # 0 = Monday
        now_total = now.hour * 60 + now.minute

        active = (
            db.query(DepartureAlert)
            .filter(DepartureAlert.is_active.is_(True))
            .all()
        )

        for alert in active:
            scheduled_days = [int(d) for d in alert.days_of_week.split(",") if d]
            if today_dow not in scheduled_days:
                continue

            dep_h, dep_m = map(int, alert.departure_time.split(":"))
            target_total = dep_h * 60 + dep_m - alert.advance_notice_minutes

            if abs(now_total - target_total) > 1:
                continue

            if alert.last_triggered_at:
                if now_naive - alert.last_triggered_at < timedelta(minutes=_COOLDOWN_MINUTES):
                    continue

            title = f"Departure reminder: {alert.route_name}"
            message = (
                f"Leave in {alert.advance_notice_minutes} min for {alert.destination_name} "
                f"(depart at {alert.departure_time} by {alert.mode})"
            )

            await create_notification(
                user_id=alert.user_id,
                route_id=None,
                title=title,
                message=message,
                notification_type="system",
                severity="medium",
                location=alert.origin_name,
                db=db,
            )

            await ws_manager.send_to_user(
                alert.user_id,
                {
                    "type": "departure_alert",
                    "alert_id": alert.id,
                    "title": title,
                    "message": message,
                    "departure_time": alert.departure_time,
                    "mode": alert.mode,
                },
            )

            alert.last_triggered_at = now_naive  # store naive UTC to match DB DateTime column
            db.commit()
            logger.info(
                "Departure alert fired for user %s: '%s' at %s",
                alert.user_id, alert.route_name, alert.departure_time,
            )

    except Exception as exc:
        logger.error("Departure alert check error: %s", exc)
    finally:
        db.close()
