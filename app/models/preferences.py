"""User preferences model — notification settings and travel defaults."""

from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    preferred_mode = Column(String(20), default="driving", nullable=False)
    # Minimum congestion level that triggers an alert (low/medium/high)
    alert_threshold = Column(String(20), default="high", nullable=False)
    quiet_hours_start = Column(Integer, default=22, nullable=False)  # 0-23
    quiet_hours_end = Column(Integer, default=7, nullable=False)     # 0-23
    notify_via_websocket = Column(Boolean, default=True, nullable=False)
    notify_email = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_user_preferences_user_id", "user_id"),
    )
