"""
Route optimization models.

Defines a saved route entity for storing user-defined commute routes.
"""

import uuid as _uuid
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class SavedRoute(Base):
    """
    Represents a user-saved route for commute optimization.

    A saved route includes origin/destination coordinates, human readable
    labels, and a soft-delete flag.
    """

    __tablename__ = "saved_routes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    route_name = Column(String(100), nullable=False)
    origin_lat = Column(Float, nullable=False)
    origin_lng = Column(Float, nullable=False)
    origin_name = Column(String(200), nullable=False)
    destination_lat = Column(Float, nullable=False)
    destination_lng = Column(Float, nullable=False)
    destination_name = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="saved_routes")
    # route_id on Notification is nullable (SET NULL on delete), so no delete-orphan here
    notifications = relationship("Notification", back_populates="route")

    __table_args__ = (
        Index("ix_saved_routes_user_id", "user_id"),
        Index("ix_saved_routes_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        """
        Return a readable string representation of the saved route.

        Returns:
            str: human-readable route identifier
        """
        return (
            f"<SavedRoute(id={self.id}, user_id={self.user_id}, route_name='{self.route_name}', "
            f"origin=({self.origin_lat},{self.origin_lng}), "
            f"destination=({self.destination_lat},{self.destination_lng}), "
            f"is_active={self.is_active})>"
        )
