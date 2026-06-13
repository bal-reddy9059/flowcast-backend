"""Weather snapshot model — correlates weather with traffic conditions."""

from datetime import datetime
from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.sql import func
from app.database import Base


class WeatherSnapshot(Base):
    """One weather reading per city, fetched every 30 minutes."""
    __tablename__ = "weather_snapshots"

    id          = Column(Integer, primary_key=True, index=True)
    city        = Column(String(100), nullable=False, index=True)
    country     = Column(String(10), nullable=True, default="IN")
    condition   = Column(String(50),  nullable=True)   # Clear / Rain / Thunderstorm / Fog / Haze
    temp_c      = Column(Float, nullable=True)
    humidity    = Column(Integer, nullable=True)        # %
    wind_kmh    = Column(Float, nullable=True)
    rain_mm_1h  = Column(Float, nullable=True, default=0.0)  # rainfall in last hour
    visibility_km = Column(Float, nullable=True)
    # Derived congestion impact computed from weather
    congestion_modifier = Column(String(10), nullable=True)  # none / light / moderate / severe
    fetched_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
