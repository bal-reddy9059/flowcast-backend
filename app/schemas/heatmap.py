"""
Heatmap schemas for traffic heatmap endpoints.

Defines the payload structures used to return heatmap points and summary metadata.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class HeatmapPoint(BaseModel):
    """Represents a single point on the traffic heatmap."""

    latitude: float = Field(
        ...,
        description="Latitude of the traffic point",
        examples=[17.4486],
    )
    longitude: float = Field(
        ...,
        description="Longitude of the traffic point",
        examples=[78.3908],
    )
    intensity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Normalized congestion intensity between 0.0 and 1.0",
        examples=[0.92],
    )
    congestion_level: str = Field(
        ...,
        description="Traffic congestion level: low | medium | high",
        examples=["high"],
    )
    location: str = Field(
        ...,
        description="Human readable location name",
        examples=["Hitech City"],
    )
    vehicle_count: int = Field(
        ...,
        ge=0,
        description="Number of vehicles observed at this location",
        examples=[145],
    )
    average_speed: float = Field(
        ...,
        ge=0.0,
        description="Average speed in km/h at this location",
        examples=[12.5],
    )
    timestamp: str = Field(
        ...,
        description="Observation time in IST (ISO-8601)",
        examples=["2026-07-14T17:46:08.686829+05:30"],
    )

    model_config = ConfigDict(extra="forbid")


class HeatmapResponse(BaseModel):
    """Response schema for traffic heatmap data."""

    success: bool = Field(True, description="Whether the request succeeded")
    points: List[HeatmapPoint] = Field(
        ...,
        description="List of heatmap points representing monitored locations",
    )
    total_points: int = Field(
        ...,
        ge=0,
        description="Total number of heatmap points returned",
        examples=[42],
    )
    high_congestion_count: int = Field(
        ...,
        ge=0,
        description="Count of high-intensity / high-congestion points",
        examples=[8],
    )
    average_intensity: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Average intensity across returned points",
        examples=[0.54],
    )
    coverage_area: str = Field(
        ...,
        description="Geographic area covered by the heatmap",
        examples=["India"],
    )
    generated_at: str = Field(
        ...,
        description="Response generation time in IST (ISO-8601)",
        examples=["2026-07-14T18:00:00+05:30"],
    )
    hours_lookback: int = Field(
        ...,
        ge=1,
        le=24,
        description="Number of hours of traffic data included",
        examples=[1],
    )
    congestion_filter: Optional[str] = Field(
        None,
        description="Congestion filter applied (if any)",
        examples=["high"],
    )
    min_intensity: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Minimum intensity filter that was applied",
        examples=[0.0],
    )

    model_config = ConfigDict(extra="forbid")


class HeatmapFilter(BaseModel):
    """Request filter schema for heatmap queries."""

    hours: int = Field(
        1,
        ge=1,
        le=24,
        description="Hours of historical traffic data to include",
        examples=[1],
    )
    congestion_filter: Optional[str] = Field(
        None,
        description="Optional congestion level filter: low, medium, high",
        examples=["high"],
    )
    min_intensity: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Minimum intensity threshold (0.0–1.0). Use 0.0 for all points.",
        examples=[0.0],
    )
    limit: int = Field(
        500,
        ge=1,
        le=1000,
        description="Maximum number of heatmap points to return",
        examples=[500],
    )

    model_config = ConfigDict(extra="forbid")
