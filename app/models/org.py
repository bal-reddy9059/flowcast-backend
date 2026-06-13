"""Organization and team membership models."""

import uuid
from datetime import datetime
from sqlalchemy import Column, ForeignKey, String, Boolean, DateTime, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(100), unique=True, nullable=False)
    slug = Column(String(60), unique=True, nullable=False, index=True)
    plan = Column(String(20), default="free", nullable=False)  # free / pro / enterprise
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    memberships = relationship("OrgMembership", back_populates="org", cascade="all, delete-orphan")
    vehicles = relationship("FleetVehicle", back_populates="org", cascade="all, delete-orphan")
    zones = relationship("GeofenceZone", back_populates="org", cascade="all, delete-orphan")
    webhooks = relationship("Webhook", back_populates="org", cascade="all, delete-orphan")
    alert_rules = relationship("AlertRule", back_populates="org", cascade="all, delete-orphan")


class OrgMembership(Base):
    __tablename__ = "org_memberships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False, default="member")  # owner / admin / member
    invited_by = Column(UUID(as_uuid=True), nullable=True)
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    org = relationship("Organization", back_populates="memberships", foreign_keys=[org_id])

    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_membership"),
        Index("ix_org_memberships_org_user", "org_id", "user_id"),
    )
