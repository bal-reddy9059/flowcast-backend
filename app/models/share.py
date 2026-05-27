"""Route share token model — public read-only links for saved routes."""

from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class RouteShareToken(Base):
    __tablename__ = "route_share_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_id = Column(UUID(as_uuid=True), ForeignKey("saved_routes.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True)   # None = never expires
    view_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_route_share_tokens_route_id", "route_id"),
    )
