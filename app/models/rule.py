"""Custom alert rule and evaluation log models."""

import uuid
from datetime import datetime
from sqlalchemy import Column, ForeignKey, String, Boolean, DateTime, Integer, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    location = Column(String(200), nullable=False)
    # condition_metric: "congestion_level" / "average_speed" / "vehicle_count"
    condition_metric = Column(String(30), nullable=False, default="congestion_level")
    # condition_operator: ">=" / "<=" / "==" / ">" / "<"
    condition_operator = Column(String(10), nullable=False, default=">=")
    # condition_value: "high", "30", "1000"
    condition_value = Column(String(50), nullable=False, default="high")
    duration_minutes = Column(Integer, default=5, nullable=False)
    # action_type: "notify" / "webhook" / "both"
    action_type = Column(String(20), nullable=False, default="notify")
    action_webhook_id = Column(UUID(as_uuid=True), nullable=True)
    cooldown_minutes = Column(Integer, default=30, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    last_triggered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    org = relationship("Organization", back_populates="alert_rules", foreign_keys=[org_id])
    evaluations = relationship("RuleEvaluation", back_populates="rule", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_alert_rules_user", "user_id"),)


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False, index=True)
    triggered_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    metric_value = Column(String(50), nullable=True)
    location = Column(String(200), nullable=True)

    rule = relationship("AlertRule", back_populates="evaluations", foreign_keys=[rule_id])
