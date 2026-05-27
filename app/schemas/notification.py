"""
Notification schemas for push notification endpoints.

Defines request and response models for creating, retrieving, and managing notifications.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator


ALLOWED_NOTIFICATION_TYPES = {"congestion_alert", "incident_alert", "route_update", "system"}
ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


class NotificationCreate(BaseModel):
    """Request schema for creating a new notification."""

    user_id: int = Field(..., gt=0, description="User ID to receive the notification")
    route_id: Optional[uuid.UUID] = Field(
        None,
        description="Optional saved route ID associated with the notification",
    )
    title: str = Field(
        ...,
        min_length=3,
        max_length=200,
        description="Short notification title",
        example="High Traffic Alert — Home to Office",
    )
    message: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Full notification message",
        example="Heavy congestion detected near Hitech City on your saved route",
    )
    notification_type: str = Field(
        ...,
        description="Type of notification",
        example="congestion_alert",
    )
    severity: str = Field(
        ...,
        description="Alert severity level",
        example="high",
    )
    location: str = Field(
        ...,
        max_length=200,
        description="Location where alert is triggered",
        example="Hitech City, Hyderabad",
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("notification_type")
    @classmethod
    def validate_notification_type(cls, value: str) -> str:
        """Validate notification type is one of allowed values."""
        if value not in ALLOWED_NOTIFICATION_TYPES:
            raise ValueError(
                f"notification_type must be one of: {', '.join(ALLOWED_NOTIFICATION_TYPES)}"
            )
        return value

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, value: str) -> str:
        """Validate severity is one of allowed values."""
        if value not in ALLOWED_SEVERITIES:
            raise ValueError(
                f"severity must be one of: {', '.join(ALLOWED_SEVERITIES)}"
            )
        return value


class NotificationResponse(BaseModel):
    """Response schema for a notification record."""

    id: uuid.UUID
    user_id: uuid.UUID          # UUID — matches Notification.user_id column
    route_id: Optional[uuid.UUID] = None
    title: str
    message: str
    notification_type: str
    severity: str
    location: Optional[str] = None
    is_read: bool
    is_sent: bool
    sent_via: Optional[str] = None
    created_at: datetime
    read_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        extra="ignore",         # ignore extra DB columns instead of raising
    )


class NotificationSummary(BaseModel):
    """Paginated notification response with summary statistics."""

    total: int = Field(..., ge=0, description="Total notifications for this user (all, unfiltered)")
    unread: int = Field(..., ge=0, description="Count of unread notifications")
    critical: int = Field(..., ge=0, description="Count of critical severity notifications")
    page_total: int = Field(..., ge=0, description="Total matching current filter (before pagination)")
    notifications: List[NotificationResponse] = Field(..., description="Paginated list of notification records")

    model_config = ConfigDict(extra="ignore")


class WebSocketMessage(BaseModel):
    """Message payload for WebSocket communication."""

    type: str = Field(
        ...,
        description="Message type: notification, ping, pong, connected, disconnected",
        example="notification",
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Message payload data",
        example={"title": "High Traffic Alert", "severity": "high", "location": "Hitech City"},
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Message timestamp",
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        """Validate message type is one of allowed values."""
        allowed_types = {"notification", "ping", "pong", "connected", "disconnected"}
        if value not in allowed_types:
            raise ValueError(
                f"type must be one of: {', '.join(allowed_types)}"
            )
        return value
