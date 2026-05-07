"""
Route optimization models.

Defines a saved route entity for storing user-defined commute routes.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class SavedRoute(Base):
    """
    Represents a user-saved route for commute optimization.

    A saved route includes origin/destination coordinates, human readable
    labels, and a soft-delete flag.
    """

    __tablename__ = "saved_routes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    route_name = Column(String(255), nullable=False)
    origin_lat = Column(Float, nullable=False)
    origin_lng = Column(Float, nullable=False)
    destination_lat = Column(Float, nullable=False)
    destination_lng = Column(Float, nullable=False)
    origin_name = Column(String(255), nullable=False)
    destination_name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="saved_routes")

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
