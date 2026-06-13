"""Geofence zone and zone alert models."""

import uuid
from datetime import datetime
from sqlalchemy import Column, ForeignKey, String, Boolean, DateTime, Float, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class GeofenceZone(Base):
    __tablename__ = "geofence_zones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    zone_type = Column(String(20), nullable=False, default="rectangle")  # rectangle / circle
    # Rectangle bounds
    lat_min = Column(Float, nullable=True)
    lat_max = Column(Float, nullable=True)
    lng_min = Column(Float, nullable=True)
    lng_max = Column(Float, nullable=True)
    # Circle (nullable — only for zone_type="circle")
    center_lat = Column(Float, nullable=True)
    center_lng = Column(Float, nullable=True)
    radius_km = Column(Float, nullable=True)
    # Alert config
    congestion_threshold = Column(String(20), default="high", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    org = relationship("Organization", back_populates="zones", foreign_keys=[org_id])
    zone_alerts = relationship("ZoneAlert", back_populates="zone", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_geofence_zones_user", "user_id"),)


class ZoneAlert(Base):
    __tablename__ = "zone_alerts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("geofence_zones.id", ondelete="CASCADE"), nullable=False, index=True)
    triggered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    congestion_level = Column(String(20), nullable=False)
    affected_locations = Column(Text, nullable=True)  # JSON list
    avg_speed_kmh = Column(Float, nullable=True)

    zone = relationship("GeofenceZone", back_populates="zone_alerts", foreign_keys=[zone_id])
