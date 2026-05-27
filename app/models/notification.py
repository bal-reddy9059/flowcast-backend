"""
Push notification models.

Defines the Notification table for storing and tracking user alerts.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Notification(Base):
    """
    Represents a push notification alert sent to a user.

    Tracks notification metadata, read status, delivery method,
    and delivery timestamps.
    """

    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    route_id = Column(UUID(as_uuid=True), ForeignKey("saved_routes.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(200), nullable=False)
    message = Column(String(500), nullable=False)
    notification_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    location = Column(String(200), nullable=True)

    is_read = Column(Boolean, default=False, nullable=False, index=True)
    is_sent = Column(Boolean, default=False, nullable=False)
    sent_via = Column(String(50), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="notifications")
    route = relationship("SavedRoute", back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_user_id_is_read", "user_id", "is_read"),
        Index("ix_notifications_created_at_desc", "created_at"),
    )

    def __repr__(self) -> str:
        """
        Return a readable string representation of the notification.

        Returns:
            str: human-readable notification identifier
        """
        return (
            f"<Notification(id={self.id}, user_id={self.user_id}, "
            f"route_id={self.route_id}, title='{self.title}', "
            f"notification_type='{self.notification_type}', "
            f"severity='{self.severity}', is_read={self.is_read}, "
            f"is_sent={self.is_sent})>"
        )
