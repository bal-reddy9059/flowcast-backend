"""API key model for developer access to FlowCast data."""

import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Index
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class ApiKey(Base):
    """
    Developer API key. The raw key is shown ONCE at creation.
    Only the SHA-256 hash is stored.

    Tiers:
      free       — 1 000 requests / day
      pro        — 50 000 requests / day
      enterprise — unlimited
    """
    __tablename__ = "api_keys"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    name       = Column(String(100), nullable=False)
    key_prefix = Column(String(12), nullable=False, index=True)   # e.g. "sk_live_xYz1"
    key_hash   = Column(String(64), nullable=False, unique=True, index=True)  # SHA-256 hex
    scopes     = Column(String(255), nullable=False, default="read:traffic")  # space-separated
    tier       = Column(String(20), nullable=False, default="free")           # free/pro/enterprise
    is_active  = Column(Boolean, default=True, nullable=False, index=True)
    daily_limit = Column(Integer, nullable=True)     # None = unlimited
    daily_count = Column(Integer, default=0, nullable=False)
    count_date  = Column(DateTime, nullable=True)    # date when daily_count was last reset
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at   = Column(DateTime(timezone=True), nullable=True)  # None = never
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_api_keys_user_active", "user_id", "is_active"),
    )


_TIER_LIMITS = {"free": 1_000, "pro": 50_000, "enterprise": None}
_VALID_SCOPES = {
    "read:traffic", "write:traffic",
    "read:fleet",   "write:fleet",
    "read:eta",
    "read:analytics",
    "read:incidents", "write:incidents",
}
