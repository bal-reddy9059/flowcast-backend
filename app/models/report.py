"""Scheduled report model."""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Index
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    org_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    # report_type: daily_summary / weekly_trend / zone_health / fleet_performance
    report_type = Column(String(30), nullable=False)
    location = Column(String(200), nullable=True)
    # schedule: daily / weekly / manual
    schedule = Column(String(20), nullable=False, default="manual")
    day_of_week = Column(Integer, nullable=True)  # 0=Mon … 6=Sun for weekly
    is_active = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_scheduled_reports_user", "user_id"),)
