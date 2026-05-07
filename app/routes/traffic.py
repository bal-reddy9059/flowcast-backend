from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models.predictor import TrafficRecord, PredictionResult, Incident

router = APIRouter(prefix="/traffic", tags=["Traffic"])


# ─── Pydantic Schemas ──────────────────────────────────────────────────────────

class TrafficRecordCreate(BaseModel):
    location: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    vehicle_count: int
    average_speed: Optional[float] = None
    congestion_level: Optional[str] = None
    road_type: Optional[str] = None


class TrafficRecordOut(TrafficRecordCreate):
    id: int
    timestamp: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class PredictionOut(BaseModel):
    id: int
    location: str
    predicted_congestion: str
    confidence_score: Optional[float]
    prediction_for: datetime
    model_version: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class IncidentCreate(BaseModel):
    location: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    incident_type: str
    severity: Optional[str] = None
    description: Optional[str] = None


class IncidentOut(IncidentCreate):
    id: int
    reported_at: datetime
    is_active: bool
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ─── Traffic Records ───────────────────────────────────────────────────────────

@router.get("/records", response_model=List[TrafficRecordOut])
def get_traffic_records(
    location: Optional[str] = Query(None, description="Filter by location name"),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """Fetch recent traffic records, optionally filtered by location."""
    query = db.query(TrafficRecord).order_by(desc(TrafficRecord.timestamp))
    if location:
        query = query.filter(TrafficRecord.location.ilike(f"%{location}%"))
    return query.limit(limit).all()


@router.post("/records", response_model=TrafficRecordOut, status_code=201)
def create_traffic_record(payload: TrafficRecordCreate, db: Session = Depends(get_db)):
    """Save a new traffic observation to the database."""
    record = TrafficRecord(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/records/{record_id}", response_model=TrafficRecordOut)
def get_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(TrafficRecord).filter(TrafficRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Traffic record not found")
    return record


# ─── Predictions ───────────────────────────────────────────────────────────────

@router.get("/predictions", response_model=List[PredictionOut])
def get_predictions(
    location: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """Retrieve ML predictions for traffic congestion."""
    query = db.query(PredictionResult).filter(
        PredictionResult.is_active == True
    ).order_by(desc(PredictionResult.created_at))
    if location:
        query = query.filter(PredictionResult.location.ilike(f"%{location}%"))
    return query.limit(limit).all()


# ─── Incidents ─────────────────────────────────────────────────────────────────

@router.get("/incidents", response_model=List[IncidentOut])
def get_incidents(
    active_only: bool = Query(True),
    location: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Get road incidents, optionally filtered."""
    query = db.query(Incident)
    if active_only:
        query = query.filter(Incident.is_active == True)
    if location:
        query = query.filter(Incident.location.ilike(f"%{location}%"))
    return query.order_by(desc(Incident.reported_at)).all()


@router.post("/incidents", response_model=IncidentOut, status_code=201)
def report_incident(payload: IncidentCreate, db: Session = Depends(get_db)):
    """Report a new road incident."""
    incident = Incident(**payload.model_dump())
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


@router.patch("/incidents/{incident_id}/resolve", response_model=IncidentOut)
def resolve_incident(incident_id: int, db: Session = Depends(get_db)):
    """Mark an incident as resolved."""
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.is_active = False
    incident.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(incident)
    return incident


# ─── Summary ───────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Quick stats overview of the traffic database."""
    total_records = db.query(TrafficRecord).count()
    total_predictions = db.query(PredictionResult).count()
    active_incidents = db.query(Incident).filter(Incident.is_active == True).count()
    return {
        "total_traffic_records": total_records,
        "total_predictions": total_predictions,
        "active_incidents": active_incidents,
    }
