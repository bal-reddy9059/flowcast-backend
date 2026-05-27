"""Favorite location model for user-bookmarked traffic spots."""

import uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class FavoriteLocation(Base):
    """A location a user has bookmarked for quick traffic status checks."""

    __tablename__ = "favorite_locations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    location_name = Column(String(200), nullable=False)
    nickname = Column(String(100), nullable=True)   # e.g. "Home", "Office"
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_favorite_locations_user_id", "user_id"),
        UniqueConstraint("user_id", "location_name", name="uq_user_favorite_location"),
    )
