from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from database import Base


class TrafficRecord(Base):
    __tablename__ = "traffic_records"

    id = Column(Integer, primary_key=True, index=True)
    location = Column(String, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    congestion_level = Column(String, nullable=False)  # low | moderate | high | very_high
    speed_kmh = Column(Float, nullable=True)
    travel_time_mins = Column(Float, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
