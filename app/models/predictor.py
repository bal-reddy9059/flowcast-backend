import uuid as _uuid

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, Index, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base


def _new_uuid() -> str:
    return str(_uuid.uuid4())


class TrafficRecord(Base):
    """Raw traffic observations stored per location."""
    __tablename__ = "traffic_records"
    __table_args__ = (
        Index("ix_traffic_records_location_created", "location", "created_at"),
        Index("ix_traffic_records_created_at", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    record_uuid = Column(
        String(36),
        default=_new_uuid,
        unique=True,
        nullable=True,   # nullable so existing rows without the column still load
        index=True,
    )
    location = Column(String(255), nullable=False, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    vehicle_count = Column(Integer, nullable=False, default=0)
    average_speed = Column(Float, nullable=True)        # km/h
    congestion_level = Column(String(50), nullable=True)  # low / medium / high
    road_type = Column(String(100), nullable=True)
    data_source = Column(String(20), nullable=True, default="manual")  # here / tomtom / manual
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PredictionResult(Base):
    """ML model predictions for future traffic congestion."""
    __tablename__ = "prediction_results"

    id = Column(Integer, primary_key=True, index=True)
    prediction_uuid = Column(
        String(36),
        default=_new_uuid,
        unique=True,
        nullable=True,
        index=True,
    )
    location = Column(String(255), nullable=False, index=True)
    predicted_congestion = Column(String(50), nullable=False)   # low / medium / high
    confidence_score = Column(Float, nullable=True)             # 0.0 – 1.0
    prediction_for = Column(DateTime(timezone=True), nullable=False, index=True)
    model_version = Column(String(50), nullable=True, default="v1.0")
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Incident(Base):
    """Road incidents like accidents, roadworks, closures."""
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    incident_uuid = Column(
        String(36),
        default=_new_uuid,
        unique=True,
        nullable=True,
        index=True,
    )
    location = Column(String(255), nullable=False, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    incident_type = Column(String(100), nullable=False)   # accident / roadwork / closure / event / flooding / police
    severity = Column(String(50), nullable=True)          # minor / moderate / severe
    description = Column(Text, nullable=True)
    reported_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Crowdsourcing fields (added via run_column_migrations)
    reported_by = Column(String(36), nullable=True, index=True)   # user UUID
    upvotes = Column(Integer, default=0, nullable=False)
    downvotes = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)   # auto-resolve time


class IncidentVote(Base):
    """One vote per user per incident (up or down). Switching replaces the prior vote."""
    __tablename__ = "incident_votes"
    __table_args__ = (
        UniqueConstraint("incident_id", "user_id", name="uq_incident_vote_user"),
        Index("ix_incident_votes_incident_id", "incident_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, nullable=False)
    user_id = Column(String(36), nullable=False, index=True)
    vote = Column(String(8), nullable=False)  # "up" | "down"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
