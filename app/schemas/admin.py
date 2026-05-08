"""
Admin dashboard schemas for FlowCast system monitoring and management.

Defines the data structures returned by admin-only dashboard endpoints.
"""

from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict, Field


class TableInfo(BaseModel):
    """Schema for individual database table metadata."""

    name: str = Field(..., description="Table name in the database", example="traffic_records")
    row_count: int = Field(..., ge=0, description="Number of rows in the table", example=12842)

    model_config = ConfigDict(
        extra="forbid",
    )


class SystemStats(BaseModel):
    """System-level overview metrics for the admin dashboard."""

    total_users: int = Field(..., ge=0, description="Total registered users", example=523)
    active_users_today: int = Field(..., ge=0, description="Users active in the last 24 hours", example=124)
    total_traffic_records: int = Field(..., ge=0, description="Total traffic records stored", example=987654)
    records_today: int = Field(..., ge=0, description="Traffic records created in the last 24 hours", example=8421)
    total_predictions: int = Field(..., ge=0, description="Total traffic prediction entries", example=4321)
    total_incidents: int = Field(..., ge=0, description="Total active and historical incident entries", example=185)
    total_notifications: int = Field(..., ge=0, description="Total notifications created", example=2104)
    unread_notifications: int = Field(..., ge=0, description="Unread notifications across all users", example=23)
    active_ws_connections: int = Field(..., ge=0, description="Current active WebSocket connections", example=12)
    cache_hit_rate: float = Field(..., ge=0.0, le=1.0, description="Current Redis cache hit rate", example=0.87)
    uptime_seconds: int = Field(..., ge=0, description="Seconds since the application started", example=86400)
    api_version: str = Field(..., description="API version currently running", example="1.0.0")

    model_config = ConfigDict(
        extra="forbid",
    )


class RequestStats(BaseModel):
    """API request analytics data for the admin dashboard."""

    total_requests_today: int = Field(..., ge=0, description="Total number of API requests received today", example=14523)
    avg_response_time_ms: float = Field(..., ge=0.0, description="Average API response time in milliseconds", example=128.5)
    error_rate_percent: float = Field(..., ge=0.0, le=100.0, description="Percentage of requests that returned errors", example=2.3)
    most_used_endpoint: str = Field(..., description="Most frequently called endpoint today", example="/traffic/heatmap")
    peak_hour: str = Field(..., description="Hour with the highest traffic volume", example="14:00-15:00")

    model_config = ConfigDict(
        extra="forbid",
    )


class DatabaseStats(BaseModel):
    """Database health and size metrics for the admin dashboard."""

    total_records: int = Field(..., ge=0, description="Total number of records across core tables", example=1245789)
    db_size_mb: float = Field(..., ge=0.0, description="Estimated database size in megabytes", example=512.4)
    oldest_record: datetime = Field(..., description="Timestamp of the oldest stored record", example="2026-04-01T08:15:00")
    newest_record: datetime = Field(..., description="Timestamp of the most recent stored record", example="2026-05-07T10:45:00")
    tables: List[TableInfo] = Field(..., description="List of monitored table row counts")

    model_config = ConfigDict(
        extra="forbid",
    )
