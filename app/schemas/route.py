"""
Route schemas for route optimization endpoints.

Defines request and response models for route calculations, segment details,
and saved route persistence.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


HYDERABAD_LAT_RANGE = (17.0, 17.8)
HYDERABAD_LNG_RANGE = (78.0, 78.9)
ALLOWED_MODES = {"driving", "walking", "transit"}
CONGESTION_LEVELS = {"low", "medium", "high"}


class RouteRequest(BaseModel):
    """Request body for route optimization based on origin and destination."""

    origin_lat: float = Field(
        ...,
        ge=-90,
        le=90,
        description="Origin latitude",
        example=17.3850,
    )
    origin_lng: float = Field(
        ...,
        ge=-180,
        le=180,
        description="Origin longitude",
        example=78.4867,
    )
    destination_lat: float = Field(
        ...,
        ge=-90,
        le=90,
        description="Destination latitude",
        example=17.4400,
    )
    destination_lng: float = Field(
        ...,
        ge=-180,
        le=180,
        description="Destination longitude",
        example=78.3900,
    )
    mode: str = Field(
        "driving",
        description="Travel mode for the route",
        example="driving",
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in ALLOWED_MODES:
            raise ValueError("mode must be one of: driving, walking, transit")
        return value

    @model_validator(mode="after")
    def validate_hyderabad_bounds(self) -> "RouteRequest":
        if not (
            HYDERABAD_LAT_RANGE[0] <= self.origin_lat <= HYDERABAD_LAT_RANGE[1]
            and HYDERABAD_LAT_RANGE[0] <= self.destination_lat <= HYDERABAD_LAT_RANGE[1]
            and HYDERABAD_LNG_RANGE[0] <= self.origin_lng <= HYDERABAD_LNG_RANGE[1]
            and HYDERABAD_LNG_RANGE[0] <= self.destination_lng <= HYDERABAD_LNG_RANGE[1]
        ):
            raise ValueError("Only Hyderabad routes supported currently")
        return self


class CoordinatePair(BaseModel):
    """Simple latitude/longitude pair used in route segments."""

    lat: float = Field(..., description="Latitude coordinate", example=17.3850)
    lng: float = Field(..., description="Longitude coordinate", example=78.4867)

    model_config = ConfigDict(
        extra="forbid",
    )


class RouteSegment(BaseModel):
    """Represents an individual segment within an optimized route."""

    start_location: CoordinatePair = Field(..., description="Segment start location")
    end_location: CoordinatePair = Field(..., description="Segment end location")
    distance_km: float = Field(..., description="Distance for the segment in kilometers", example=2.4)
    duration_minutes: float = Field(..., description="Estimated duration for the segment in minutes", example=8.5)
    congestion_level: str = Field(..., description="Congestion classification for the segment", example="medium")
    congestion_warning: Optional[str] = Field(
        None,
        description="Optional congestion warning for the segment",
        example="Expect heavy traffic near the junction",
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("congestion_level")
    @classmethod
    def validate_congestion_level(cls, value: str) -> str:
        if value not in CONGESTION_LEVELS:
            raise ValueError("congestion_level must be one of: low, medium, high")
        return value


class RouteResponse(BaseModel):
    """Response returned for optimized route requests."""

    origin: str = Field(..., description="Human readable origin name", example="Gachibowli")
    destination: str = Field(..., description="Human readable destination name", example="Hitech City")
    total_distance_km: float = Field(..., description="Total route distance in kilometers", example=12.0)
    total_eta_minutes: float = Field(..., description="Estimated travel time in minutes", example=35.0)
    total_eta_with_buffer_minutes: float = Field(..., description="ETA including buffer time in minutes", example=38.5)
    congestion_summary: str = Field(..., description="Overall congestion level for the route", example="medium")
    segments: List[RouteSegment] = Field(..., description="List of route segments")
    warnings: List[str] = Field(..., description="Active incident warnings on the route", example=["Accident near Hitech City"])
    google_maps_url: str = Field(..., description="Deep link to Google Maps directions", example="https://www.google.com/maps/dir/?api=1&origin=17.3850,78.4867&destination=17.4400,78.3900&travelmode=driving")
    fetched_at: datetime = Field(..., description="Timestamp when route data was fetched")

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )


class SavedRouteCreate(BaseModel):
    """Request schema for saving a user-defined route."""

    route_name: str = Field(..., min_length=3, max_length=100, description="Label for the saved route", example="Home to Office")
    origin_lat: float = Field(..., description="Origin latitude", example=17.3850)
    origin_lng: float = Field(..., description="Origin longitude", example=78.4867)
    destination_lat: float = Field(..., description="Destination latitude", example=17.4400)
    destination_lng: float = Field(..., description="Destination longitude", example=78.3900)
    origin_name: str = Field(..., max_length=200, description="Human readable origin label", example="Gachibowli")
    destination_name: str = Field(..., max_length=200, description="Human readable destination label", example="Hitech City")

    model_config = ConfigDict(
        extra="forbid",
    )


class SavedRouteResponse(BaseModel):
    """Response schema for a saved route record."""

    id: int
    user_id: int
    route_name: str
    origin_name: str
    destination_name: str
    origin_lat: float
    origin_lng: float
    destination_lat: float
    destination_lng: float
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
    )
