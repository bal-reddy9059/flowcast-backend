"""Trip history model — log of every route query a user runs."""

import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class TripHistory(Base):
    __tablename__ = "trip_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    origin_name = Column(String(200), nullable=False)
    destination_name = Column(String(200), nullable=False)
    origin_lat = Column(Float, nullable=True)
    origin_lng = Column(Float, nullable=True)
    destination_lat = Column(Float, nullable=True)
    destination_lng = Column(Float, nullable=True)
    mode = Column(String(20), nullable=False, default="driving")
    distance_km = Column(Float, nullable=True)
    predicted_eta_minutes = Column(Float, nullable=True)
    congestion_at_departure = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_trip_history_user_id", "user_id"),
        Index("ix_trip_history_created_at", "created_at"),
    )
