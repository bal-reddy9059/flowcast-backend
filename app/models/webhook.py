"""Webhook and delivery log models."""

import uuid
from datetime import datetime
from sqlalchemy import Column, ForeignKey, String, Boolean, DateTime, Integer, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    name   = Column(String(200), nullable=True)   # friendly label, e.g. "My Slack Bot"
    url = Column(String(500), nullable=False)
    secret = Column(String(64), nullable=False)
    # comma-separated: congestion_spike,zone_alert,departure_alert,incident_new,rule_triggered
    events = Column(String(200), nullable=False, default="congestion_spike")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_triggered_at = Column(DateTime, nullable=True)
    total_deliveries = Column(Integer, default=0, nullable=False)
    failed_deliveries = Column(Integer, default=0, nullable=False)

    org = relationship("Organization", back_populates="webhooks", foreign_keys=[org_id])
    deliveries = relationship("WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_webhooks_user", "user_id"),)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    payload = Column(Text, nullable=True)
    http_status = Column(Integer, nullable=True)
    attempt = Column(Integer, default=1, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    webhook = relationship("Webhook", back_populates="deliveries", foreign_keys=[webhook_id])
