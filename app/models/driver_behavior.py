"""Driver behavior analytics models for Fleet Management."""

import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class DriverBehaviorLog(Base):
    """One behavior event per log entry — speeding, harsh braking, idle, deviation."""
    __tablename__ = "driver_behavior_logs"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    vehicle_id = Column(UUID(as_uuid=True), ForeignKey("fleet_vehicles.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    driver_id  = Column(UUID(as_uuid=True), nullable=True, index=True)  # FK to users, nullable
    org_id     = Column(UUID(as_uuid=True), nullable=False, index=True)
    event_type = Column(String(40), nullable=False)   # speeding / harsh_braking / harsh_acceleration / idle / route_deviation
    severity   = Column(String(20), nullable=True)    # low / medium / high
    location   = Column(String(255), nullable=True)
    speed_kmh  = Column(Float, nullable=True)         # recorded speed at event time
    limit_kmh  = Column(Float, nullable=True)         # speed limit at that point
    details    = Column(String(500), nullable=True)   # free-text description
    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_behavior_vehicle_date", "vehicle_id", "recorded_at"),
        Index("ix_behavior_org_date", "org_id", "recorded_at"),
    )


class DriverDailyScore(Base):
    """Aggregated daily driver score — computed by the background scorer task."""
    __tablename__ = "driver_daily_scores"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vehicle_id = Column(UUID(as_uuid=True), ForeignKey("fleet_vehicles.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    driver_id  = Column(UUID(as_uuid=True), nullable=True, index=True)
    org_id     = Column(UUID(as_uuid=True), nullable=False, index=True)
    score_date = Column(DateTime(timezone=True), nullable=False, index=True)   # midnight UTC of the day
    score      = Column(Float, nullable=False, default=100.0)   # 0–100
    # Breakdown counts for the day
    speeding_count       = Column(Integer, default=0)
    harsh_braking_count  = Column(Integer, default=0)
    harsh_accel_count    = Column(Integer, default=0)
    idle_minutes         = Column(Float, default=0.0)
    deviation_count      = Column(Integer, default=0)
    total_events         = Column(Integer, default=0)
    grade                = Column(String(2), nullable=True)  # A / B / C / D / F
    created_at           = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_daily_score_vehicle_date", "vehicle_id", "score_date"),
    )
