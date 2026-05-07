"""
ETA schemas for the FlowCast ETA calculation feature.

Defines request and response models for single and batch ETA lookups.
"""

from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_ETA_MODES = {"driving", "walking", "transit"}


class ETARequest(BaseModel):
    """Request schema for an ETA calculation at a single location."""

    location: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="Name of the monitored Hyderabad location",
        example="Hitech City",
    )
    distance_km: float = Field(
        ...,
        gt=0,
        le=500,
        description="Distance to travel in kilometers",
        example=12.5,
    )
    mode: str = Field(
        "driving",
        description="Travel mode used for ETA calculation",
        example="driving",
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in ALLOWED_ETA_MODES:
            raise ValueError("mode must be driving, walking, or transit")
        return value


class ETAResponse(BaseModel):
    """Response schema for a calculated ETA result."""

    location: str = Field(..., description="Location name used for the ETA calculation")
    distance_km: float = Field(..., description="Distance used for the ETA calculation")
    eta_minutes: float = Field(..., description="Estimated travel time in minutes")
    eta_with_buffer_minutes: float = Field(
        ..., description="ETA with a 10% buffer added"
    )
    congestion_level: str = Field(..., description="Current congestion classification")
    average_speed_kmh: float = Field(..., description="Average speed used for the calculation")
    vehicle_count: int = Field(..., description="Vehicle count observed at the location")
    traffic_condition: str = Field(
        ...,
        description="Human readable traffic condition description",
        example="Moderate traffic — slight delays possible",
    )
    confidence: str = Field(
        ...,
        description="Confidence level for the ETA estimate",
        example="high",
    )
    calculated_at: datetime = Field(..., description="Timestamp when the ETA was calculated")

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class ETABatchRequest(BaseModel):
    """Request schema for calculating ETA for multiple locations."""

    locations: List[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="List of Hyderabad locations to calculate ETA for",
        example=["Gachibowli", "Hitech City", "Banjara Hills"],
    )
    distance_km: float = Field(
        ...,
        gt=0,
        description="Distance in kilometers to use for all locations",
        example=12.5,
    )
    mode: str = Field(
        "driving",
        description="Travel mode used for all ETA calculations",
        example="driving",
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in ALLOWED_ETA_MODES:
            raise ValueError("mode must be driving, walking, or transit")
        return value


class ETABatchResponse(BaseModel):
    """Response schema for batch ETA results."""

    results: List[ETAResponse] = Field(..., description="Calculated ETA results for each location")
    total_locations: int = Field(..., description="Total number of requested locations")
    fastest_location: str = Field(..., description="Location with the lowest ETA")
    slowest_location: str = Field(..., description="Location with the highest ETA")
    calculated_at: datetime = Field(..., description="Timestamp when the batch ETA calculation completed")

    model_config = ConfigDict(
        extra="forbid",
    )
