"""
Heatmap schemas for traffic heatmap endpoints.

Defines the payload structures used to return heatmap points and summary metadata.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class HeatmapPoint(BaseModel):
    """Represents a single point on the traffic heatmap."""

    latitude: float = Field(
        ...,
        description="Latitude of the traffic point",
        example=17.4486,
    )
    longitude: float = Field(
        ...,
        description="Longitude of the traffic point",
        example=78.3908,
    )
    intensity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Normalized congestion intensity between 0.0 and 1.0",
        example=0.92,
    )
    congestion_level: str = Field(
        ...,
        description="Traffic congestion level",
        example="high",
    )
    location: str = Field(
        ...,
        description="Human readable location name",
        example="Hitech City",
    )
    vehicle_count: int = Field(
        ...,
        ge=0,
        description="Number of vehicles observed at this location",
        example=145,
    )
    average_speed: float = Field(
        ...,
        ge=0.0,
        description="Average speed in km/h at this location",
        example=12.5,
    )
    timestamp: datetime = Field(
        ...,
        description="Timestamp when the traffic record was captured",
        example="2026-05-07T10:30:00",
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class HeatmapResponse(BaseModel):
    """Response schema for traffic heatmap data."""

    points: List[HeatmapPoint] = Field(
        ...,
        description="List of heatmap points representing monitored locations",
    )
    total_points: int = Field(
        ...,
        ge=0,
        description="Total number of heatmap points returned",
        example=42,
    )
    high_congestion_count: int = Field(
        ...,
        ge=0,
        description="Count of points with high congestion intensity",
        example=8,
    )
    average_intensity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="City-wide average intensity across returned points",
        example=0.54,
    )
    coverage_area: str = Field(
        ...,
        description="Geographic area covered by the heatmap",
        example="Hyderabad, Telangana, India",
    )
    generated_at: datetime = Field(
        ...,
        description="Timestamp when the heatmap data was generated",
        example="2026-05-07T10:31:00",
    )
    hours_lookback: int = Field(
        ...,
        ge=1,
        le=24,
        description="Number of hours of traffic data included in the heatmap",
        example=1,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


class HeatmapFilter(BaseModel):
    """Request filter schema for heatmap queries."""

    hours: int = Field(
        1,
        ge=1,
        le=24,
        description="Hours of historical traffic data to include",
        example=1,
    )
    congestion_filter: Optional[str] = Field(
        None,
        description="Optional congestion level filter: low, medium, high",
        example="high",
    )
    min_intensity: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Minimum intensity threshold for returned points",
        example=0.2,
    )
    limit: int = Field(
        500,
        ge=1,
        le=1000,
        description="Maximum number of heatmap points to return",
        example=500,
    )

    model_config = ConfigDict(
        extra="forbid",
    )
