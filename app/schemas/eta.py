"""
ETA calculation schemas for FlowCast.

Pydantic v2 models for single and batch ETA requests and responses.
"""

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field, field_validator, ConfigDict


class ETARequest(BaseModel):
    """Request schema for single location ETA calculation.

    Calculates estimated travel time based on real-time traffic data.
    """
    model_config = ConfigDict(extra="forbid")

    location: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Location name in Hyderabad",
        examples=["Hitech City", "Gachibowli", "Banjara Hills"]
    )
    distance_km: float = Field(
        ...,
        gt=0,
        le=500,
        description="Distance to travel in kilometers",
        examples=[12.5, 25.0, 8.3]
    )
    mode: str = Field(
        default="driving",
        description="Travel mode",
        examples=["driving", "walking", "transit"]
    )

    @field_validator('mode')
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate that mode is one of the allowed values."""
        allowed_modes = ["driving", "walking", "transit"]
        if v not in allowed_modes:
            raise ValueError("mode must be driving, walking or transit")
        return v


class ETAResponse(BaseModel):
    """Response schema for ETA calculation results.

    Contains detailed traffic information and estimated travel times.
    """
    model_config = ConfigDict(from_attributes=True)

    location: str = Field(..., description="Location name")
    distance_km: float = Field(..., description="Distance in kilometers")
    eta_minutes: float = Field(..., description="Estimated travel time in minutes")
    eta_with_buffer_minutes: float = Field(
        ...,
        description="ETA with 10% buffer for Hyderabad traffic"
    )
    congestion_level: str = Field(
        ...,
        description="Current congestion level",
        examples=["low", "medium", "high"]
    )
    average_speed_kmh: float = Field(
        ...,
        description="Average speed in km/h based on congestion"
    )
    vehicle_count: int = Field(
        ...,
        description="Number of vehicles in the area"
    )
    traffic_condition: str = Field(
        ...,
        description="Human-readable traffic condition description"
    )
    confidence: str = Field(
        ...,
        description="Data confidence level",
        examples=["high", "medium", "low"]
    )
    calculated_at: datetime = Field(
        ...,
        description="Timestamp when calculation was performed"
    )


class ETABatchRequest(BaseModel):
    """Request schema for batch ETA calculation across multiple locations.

    Calculates ETA for multiple Hyderabad locations simultaneously.
    """
    model_config = ConfigDict(extra="forbid")

    locations: List[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="List of location names in Hyderabad",
        examples=[["Gachibowli", "Hitech City", "Banjara Hills"]]
    )
    distance_km: float = Field(
        ...,
        gt=0,
        le=500,
        description="Distance to travel in kilometers",
        examples=[12.5, 25.0, 8.3]
    )
    mode: str = Field(
        default="driving",
        description="Travel mode",
        examples=["driving", "walking", "transit"]
    )

    @field_validator('mode')
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate that mode is one of the allowed values."""
        allowed_modes = ["driving", "walking", "transit"]
        if v not in allowed_modes:
            raise ValueError("mode must be driving, walking or transit")
        return v


class ETABatchResponse(BaseModel):
    """Response schema for batch ETA calculation results.

    Contains ETA results for multiple locations with summary statistics.
    """
    model_config = ConfigDict(from_attributes=True)

    results: List[ETAResponse] = Field(
        ...,
        description="List of ETA results for each location"
    )
    total_locations: int = Field(
        ...,
        description="Total number of locations processed"
    )
    fastest_location: str = Field(
        ...,
        description="Location with the fastest ETA"
    )
    slowest_location: str = Field(
        ...,
        description="Location with the slowest ETA"
    )
    average_eta_minutes: float = Field(
        ...,
        description="Average ETA across all locations"
    )
    calculated_at: datetime = Field(
        ...,
        description="Timestamp when batch calculation was performed"
    )
