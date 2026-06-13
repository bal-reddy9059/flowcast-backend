"""Fleet vehicle and driver assignment models."""

import uuid
from datetime import datetime
from sqlalchemy import Column, ForeignKey, String, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class FleetVehicle(Base):
    __tablename__ = "fleet_vehicles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    registration = Column(String(20), nullable=True)
    vehicle_type = Column(String(30), nullable=False, default="car")  # car/truck/bike/bus/van
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    org = relationship("Organization", back_populates="vehicles", foreign_keys=[org_id])
    assignments = relationship("FleetAssignment", back_populates="vehicle", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_fleet_vehicles_org", "org_id"),)


class FleetAssignment(Base):
    __tablename__ = "fleet_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vehicle_id = Column(UUID(as_uuid=True), ForeignKey("fleet_vehicles.id", ondelete="CASCADE"), nullable=False, index=True)
    driver_id = Column(UUID(as_uuid=True), nullable=True)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    unassigned_at = Column(DateTime, nullable=True)
    is_current = Column(Boolean, default=True, nullable=False, index=True)

    vehicle = relationship("FleetVehicle", back_populates="assignments", foreign_keys=[vehicle_id])
