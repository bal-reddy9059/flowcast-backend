"""Background task: fire departure alert notifications before scheduled departure times."""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.database import SessionLocal
from app.models.alert import DepartureAlert
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# Prevent double-firing within this window
_COOLDOWN_MINUTES = 30

# Monitor runs every ~60s — allow ±2 minutes so a tick can't miss the window
_FIRE_WINDOW_MINUTES = 2


async def check_departure_alerts(ws_manager) -> None:
    """Check all active departure alerts and send a WebSocket notification when due.

    Runs every 60 s from the app lifespan. An alert fires when the current **IST**
    time is within ±2 minutes of (departure_time − advance_notice_minutes) on a
    scheduled day. A 30-minute cooldown stops duplicate notifications.
    """
    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(_IST)
        now_naive_utc = now_utc.replace(tzinfo=None)
        today_dow = now_ist.weekday()       # 0 = Monday (IST calendar day)
        now_total = now_ist.hour * 60 + now_ist.minute

        active = (
            db.query(DepartureAlert)
            .filter(DepartureAlert.is_active.is_(True))
            .all()
        )

        for alert in active:
            scheduled_days = [int(d) for d in alert.days_of_week.split(",") if d.strip().isdigit()]

            dep_h, dep_m = map(int, alert.departure_time.split(":"))
            notice = alert.advance_notice_minutes or 15
            target_total = dep_h * 60 + dep_m - notice

            # Overnight wrap: depart 00:10 with 15 min notice → fire 23:55 previous day.
            # scheduled days refer to the DEPARTURE calendar day (IST).
            if target_total < 0:
                target_total += 24 * 60
                departure_dow = (today_dow + 1) % 7
            else:
                departure_dow = today_dow

            if departure_dow not in scheduled_days:
                continue

            # Circular minute distance (handles 23:59 ↔ 00:01)
            diff = abs(now_total - target_total)
            diff = min(diff, 24 * 60 - diff)
            if diff > _FIRE_WINDOW_MINUTES:
                continue

            if alert.last_triggered_at:
                last = alert.last_triggered_at
                if last.tzinfo is not None:
                    last = last.replace(tzinfo=None)
                if now_naive_utc - last < timedelta(minutes=_COOLDOWN_MINUTES):
                    continue

            title = f"Departure reminder: {alert.route_name}"
            message = (
                f"Leave in {notice} min for {alert.destination_name} "
                f"(depart at {alert.departure_time} by {alert.mode})"
            )

            user_id_str = str(alert.user_id)
            alert_id_str = str(alert.id)

            await create_notification(
                user_id=alert.user_id,
                route_id=None,
                title=title,
                message=message,
                notification_type="departure_alert",
                severity="medium",
                location=alert.origin_name,
                db=db,
            )

            await ws_manager.send_to_user(
                user_id_str,
                {
                    "type": "departure_alert",
                    "alert_id": alert_id_str,
                    "title": title,
                    "message": message,
                    "route_name": alert.route_name,
                    "origin": alert.origin_name,
                    "destination": alert.destination_name,
                    "departure_time": alert.departure_time,
                    "advance_notice_minutes": notice,
                    "mode": alert.mode,
                    "triggered_at": now_ist.isoformat(),
                },
            )

            alert.last_triggered_at = now_naive_utc
            db.commit()
            logger.info(
                "Departure alert fired for user %s: '%s' at %s IST (notice %d min)",
                user_id_str, alert.route_name, alert.departure_time, notice,
            )

    except Exception as exc:
        logger.error("Departure alert check error: %s", exc)
    finally:
        db.close()
