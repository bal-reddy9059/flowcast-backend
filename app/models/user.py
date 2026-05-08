"""
User authentication models.

Defines the User table for storing user credentials, profile information,
and authentication status. Includes email indexing for fast lookups.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index

from app.database import Base


class User(Base):
    """
    User model for authentication and user management.

    Stores user credentials, profile data, and authentication status.
    Email is indexed for efficient login lookups.
    """

    __tablename__ = "users"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Authentication Fields
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)

    # Profile Fields
    full_name = Column(String(100), nullable=False)

    # Status Fields
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    # Activity Fields
    last_login = Column(DateTime, nullable=True)

    # Timestamp Fields
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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
