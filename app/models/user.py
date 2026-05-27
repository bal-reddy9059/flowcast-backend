"""
User authentication models.

Defines the User table for storing user credentials, profile information,
and authentication status. Includes email indexing for fast lookups.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    """
    User model for authentication and user management.

    Stores user credentials, profile data, and authentication status.
    Email is indexed for efficient login lookups.
    """

    __tablename__ = "users"

    # Primary Key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # Authentication Fields
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=True)   # nullable for Google-only accounts
    auth_provider = Column(String(20), default="local", nullable=False)  # local / google
    google_id = Column(String(255), nullable=True, unique=True, index=True)

    # Profile Fields
    full_name = Column(String(100), nullable=False)
    picture_url = Column(String(500), nullable=True)

    # Status Fields
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    # Activity Fields
    last_login = Column(DateTime, nullable=True)

    # Timestamp Fields
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    saved_routes = relationship("SavedRoute", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")

    # Composite Index for active user email lookups
    __table_args__ = (
        Index("ix_users_email_active", "email", "is_active"),
    )

    def __repr__(self) -> str:
        """
        String representation of User instance.

        Returns:
            str: Human-readable user representation
        """
        return f"<User(id={self.id}, email='{self.email}', full_name='{self.full_name}', is_active={self.is_active}, is_admin={self.is_admin})>"
