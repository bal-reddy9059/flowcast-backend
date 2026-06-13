"""Smart alert tuner — learns which notifications users act on and auto-adjusts thresholds."""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_MIN_SAMPLE_SIZE = 10  # minimum notifications before tuning kicks in
_DISMISS_RATE_THRESHOLD = 0.75  # if 75%+ of a type are unread, consider relaxing


async def tune_user_alerts(db) -> int:
    """Evaluate notification read rates and update user preferences. Returns tuned user count."""
    from app.models.notification import Notification
    from app.models.preferences import UserPreferences
    from app.models.user import User

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=14)
    tuned = 0

    users = db.query(User).filter(User.is_active.is_(True)).all()

    for user in users:
        notifs = (
            db.query(Notification)
            .filter(Notification.user_id == user.id, Notification.created_at >= since)
            .all()
        )

        if len(notifs) < _MIN_SAMPLE_SIZE:
            continue

        # Calculate dismiss rate for congestion alerts specifically
        congestion_notifs = [n for n in notifs if n.notification_type == "congestion_alert"]
        if len(congestion_notifs) < 5:
            continue

        unread_count = sum(1 for n in congestion_notifs if not n.is_read)
        dismiss_rate = unread_count / len(congestion_notifs)

        prefs = (
            db.query(UserPreferences)
            .filter(UserPreferences.user_id == user.id)
            .first()
        )

        if dismiss_rate >= _DISMISS_RATE_THRESHOLD:
            # User is ignoring most congestion alerts — raise the threshold
            if prefs is None:
                prefs = UserPreferences(user_id=user.id, congestion_threshold="high")
                db.add(prefs)
            elif prefs.congestion_threshold != "high":
                prefs.congestion_threshold = "high"
                db.commit()

                # Notify the user about the change
                from app.models.notification import Notification as Notif
                notif = Notif(
                    user_id=user.id,
                    title="Smart Alert Tuning",
                    message=(
                        f"We noticed you've been dismissing most medium-congestion alerts "
                        f"(dismiss rate: {round(dismiss_rate * 100)}%). "
                        "We've raised your threshold to high-congestion only. "
                        "Adjust anytime in Preferences."
                    ),
                    notification_type="system",
                    severity="low",
                    location="",
                )
                db.add(notif)
                db.commit()
                tuned += 1
                logger.info(
                    "Alert tuner: raised threshold to 'high' for user %s (dismiss_rate=%.0f%%)",
                    user.id, dismiss_rate * 100,
                )

    return tuned
