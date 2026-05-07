from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text
from sqlalchemy.sql import func
from app.database import Base


class TrafficRecord(Base):
    """Raw traffic observations stored per location."""
    __tablename__ = "traffic_records"

    id = Column(Integer, primary_key=True, index=True)
    location = Column(String(255), nullable=False, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    vehicle_count = Column(Integer, nullable=False, default=0)
    average_speed = Column(Float, nullable=True)        # km/h
    congestion_level = Column(String(50), nullable=True)  # low / medium / high
    road_type = Column(String(100), nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PredictionResult(Base):
    """ML model predictions for future traffic congestion."""
    __tablename__ = "prediction_results"

    id = Column(Integer, primary_key=True, index=True)
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
    location = Column(String(255), nullable=False, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    incident_type = Column(String(100), nullable=False)   # accident / roadwork / closure / event
    severity = Column(String(50), nullable=True)          # minor / moderate / severe
    description = Column(Text, nullable=True)
    reported_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
