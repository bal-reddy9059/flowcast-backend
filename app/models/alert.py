"""Departure alert model — scheduled notifications before a commute."""

import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class DepartureAlert(Base):
    __tablename__ = "departure_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    route_name = Column(String(100), nullable=False)
    origin_name = Column(String(200), nullable=False)
    destination_name = Column(String(200), nullable=False)
    departure_time = Column(String(5), nullable=False)        # "HH:MM"
    # Comma-separated integers 0-6 (0=Monday … 6=Sunday)
    days_of_week = Column(String(20), nullable=False, default="0,1,2,3,4")
    advance_notice_minutes = Column(Integer, default=30, nullable=False)
    mode = Column(String(20), nullable=False, default="driving")
    distance_km = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_triggered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_departure_alerts_user_id", "user_id"),
        Index("ix_departure_alerts_is_active", "is_active"),
        UniqueConstraint("user_id", "route_name", "departure_time", name="uq_alert_user_route_time"),
    )
