from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime, timedelta, timezone
from collections import Counter
import csv
import io
import random

from app.database import get_db
from app.models.predictor import TrafficRecord, PredictionResult, Incident
from app.services.prediction_service import predict_traffic_congestion, save_prediction
from app.services.realtime import classify_congestion
from app.services.city_aliases import CITY_ALIASES as _CITY_ALIASES, location_filter as _location_filter_fn
from app.services.incident_seeder import (
    _INCIDENT_SEEDS,
    _INCIDENT_SEED_AREAS,
    auto_seed_incidents as _auto_seed_incidents,
)

router = APIRouter(prefix="/traffic", tags=["Traffic"])


# ─── Pydantic Schemas ──────────────────────────────────────────────────────────

ALLOWED_CONGESTION = {"low", "medium", "high"}


class TrafficRecordCreate(BaseModel):
    location: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    vehicle_count: int
    average_speed: Optional[float] = None
    congestion_level: Optional[str] = None
    road_type: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "location": "Hitech City",
            "latitude": 17.4486,
            "longitude": 78.3908,
            "vehicle_count": 245,
            "average_speed": 32.5,
            "congestion_level": "medium",
            "road_type": "arterial",
        }
    })


class TrafficRecordBulkCreate(BaseModel):
    records: List[TrafficRecordCreate] = Field(..., min_length=1, max_length=50)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "records": [
                {
                    "location": "Hitech City",
                    "latitude": 17.4486,
                    "longitude": 78.3908,
                    "vehicle_count": 245,
                    "average_speed": 32.5,
                    "congestion_level": "medium",
                    "road_type": "arterial",
                },
                {
                    "location": "Gachibowli",
                    "latitude": 17.4401,
                    "longitude": 78.3489,
                    "vehicle_count": 110,
                    "average_speed": 52.0,
                    "congestion_level": "low",
                    "road_type": "arterial",
                },
            ]
        }
    })


class PredictionRequest(BaseModel):
    location: str = Field(..., min_length=2, description="Hyderabad location name")
    hours_ahead: int = Field(1, ge=1, le=24, description="Hours into the future to predict")

    model_config = ConfigDict(json_schema_extra={
        "example": {"location": "Gachibowli", "hours_ahead": 2}
    })

    @field_validator("hours_ahead")
    @classmethod
    def validate_hours(cls, v: int) -> int:
        if not 1 <= v <= 24:
            raise ValueError("hours_ahead must be between 1 and 24")
        return v


class TrafficRecordOut(TrafficRecordCreate):
    id: int = Field(..., description="Auto-generated record ID — use this in GET /traffic/records/{record_id}")
    timestamp: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class PredictionOut(BaseModel):
    id: int
    prediction_uuid: Optional[str] = Field(None, description="Unique UUID — use this to reference this prediction")
    location: str
    predicted_congestion: str
    confidence_score: Optional[float]
    prediction_for: datetime
    model_version: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


_SEVERITY_RESOLUTION_HOURS = {"minor": 1, "moderate": 3, "severe": 6}


class IncidentCreate(BaseModel):
    location: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    incident_type: str
    severity: Optional[str] = None
    description: Optional[str] = None
    resolved_at: Optional[datetime] = Field(
        None,
        description="Set only if reporting a past incident that is already resolved. Omit for active incidents.",
    )

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "location": "Hitech City",
            "latitude": 17.4486,
            "longitude": 78.3908,
            "incident_type": "accident",
            "severity": "moderate",
            "description": "Two-vehicle collision near Cyber Towers junction",
        }
    })


class IncidentOut(BaseModel):
    id: int
    incident_uuid: Optional[str] = Field(None, description="Unique UUID for this incident")
    location: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    incident_type: str
    severity: Optional[str] = None
    description: Optional[str] = None
    reported_at: datetime
    is_active: bool
    resolved_at: Optional[datetime] = None
    estimated_resolution: Optional[datetime] = Field(
        None,
        description="Estimated clearance time based on severity (minor=+1h, moderate=+3h, severe=+6h). Null for resolved incidents.",
    )

    class Config:
        from_attributes = True


# ─── Traffic Records ───────────────────────────────────────────────────────────

@router.get("/records", response_model=List[TrafficRecordOut])
def get_traffic_records(
    location: Optional[str] = Query(None, description="Filter by location name or city (e.g. hyderabad, bangalore)"),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """Fetch recent traffic records, optionally filtered by location or city name."""
    query = db.query(TrafficRecord).order_by(desc(TrafficRecord.timestamp))
    if location:
        query = query.filter(_location_filter_fn(TrafficRecord.location, location))
    return query.limit(limit).all()


@router.post("/records", response_model=TrafficRecordOut, status_code=201)
def create_traffic_record(payload: TrafficRecordCreate, db: Session = Depends(get_db)):
    """Save a new traffic observation to the database."""
    record = TrafficRecord(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.post("/records/bulk", status_code=201)
def create_traffic_records_bulk(
    payload: TrafficRecordBulkCreate,
    db: Session = Depends(get_db),
) -> dict:
    """Insert up to 50 traffic observations in a single request.

    Each created record's `id` is returned in the `record_ids` list so you can
    immediately use them with `GET /api/v1/traffic/records/{record_id}`.
    """
    records = [TrafficRecord(**r.model_dump()) for r in payload.records]
    db.add_all(records)
    db.commit()
    for r in records:
        db.refresh(r)
    return {
        "inserted": len(records),
        "message": f"{len(records)} records saved",
        "record_ids": [r.id for r in records],
        "records": [
            {
                "id": r.id,
                "location": r.location,
                "congestion_level": r.congestion_level,
                "average_speed": r.average_speed,
                "vehicle_count": r.vehicle_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }


@router.get("/records/{record_id}", response_model=TrafficRecordOut)
def get_record(
    record_id: int = Path(
        ...,
        description="Traffic record ID — get this from `GET /api/v1/traffic/records` (copy any `id` from the response)",
        openapi_examples={"default": {"value": 1}},
    ),
    db: Session = Depends(get_db),
):
    record = db.query(TrafficRecord).filter(TrafficRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Traffic record not found")
    return record


# ─── Predictions ───────────────────────────────────────────────────────────────

def _auto_generate_predictions(location: str, db: Session) -> List[PredictionResult]:
    """Generate predictions for the next 6 hours for a location and persist them."""
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
    ist_hour = datetime.now(_IST).hour
    results = []
    for hours_ahead in range(1, 7):
        target_hour = (ist_hour + hours_ahead) % 24
        pred = predict_traffic_congestion(location, target_hour, db)
        saved = save_prediction(
            location=location,
            predicted_congestion=pred["predicted_congestion"],
            confidence_score=pred["confidence_score"],
            hours_ahead=hours_ahead,
            db=db,
        )
        results.append(saved)
    return results


@router.get("/predictions", response_model=List[PredictionOut])
def get_predictions(
    location: Optional[str] = Query(None, description="Filter by location name (e.g. Bangalore, Gachibowli)"),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """Retrieve saved traffic congestion predictions.

    If no predictions exist yet for the requested location they are
    auto-generated for the next 6 hours from historical data and saved
    before being returned — so this endpoint always returns data.
    """
    query = db.query(PredictionResult).filter(
        PredictionResult.is_active == True
    ).order_by(desc(PredictionResult.created_at))
    if location:
        query = query.filter(PredictionResult.location.ilike(f"%{location}%"))

    results = query.limit(limit).all()

    # Auto-generate if the table has no predictions for this location
    if not results and location:
        results = _auto_generate_predictions(location, db)

    return results


# ─── Incidents ─────────────────────────────────────────────────────────────────

# Sample incident templates keyed by lowercase city/area name fragments
_INCIDENT_SEEDS: dict[str, list[dict]] = {
    "bangalore": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Road widening work near Silk Board Junction — expect 15–20 min delays"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Minor fender-bender on MG Road near Trinity Circle, one lane blocked"},
        {"incident_type": "closure",   "severity": "moderate", "description": "Whitefield main road partially closed for metro pillar construction"},
        {"incident_type": "event",     "severity": "minor",    "description": "Cultural event at Koramangala 5th Block causing parking overflow onto main road"},
        {"incident_type": "accident",  "severity": "moderate", "description": "Multi-vehicle collision at Hebbal flyover — cleared by traffic police", "resolved_hours_ago": 3},
        {"incident_type": "roadwork",  "severity": "minor",    "description": "Pothole patching on Electronic City Phase 1 — completed and reopened", "resolved_hours_ago": 1},
    ],
    "hyderabad": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "GHMC pothole repair on Hitech City main road — right lane closed"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Two-wheeler collision near Gachibowli flyover, partially cleared"},
        {"incident_type": "closure",   "severity": "severe",   "description": "Ameerpet underpass flooded — full closure, use LB Nagar diversion"},
        {"incident_type": "event",     "severity": "minor",    "description": "IT corridor flash mob at Begumpet — dispersed, traffic restored", "resolved_hours_ago": 2},
        {"incident_type": "accident",  "severity": "moderate", "description": "Bus breakdown on LB Nagar flyover — towed, lanes reopened", "resolved_hours_ago": 1},
    ],
    "mumbai": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Metro line 3 utility shifting on LBS Road — one lane closed"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Vehicle breakdown on Worli Sea Link blocking slow lane"},
        {"incident_type": "closure",   "severity": "severe",   "description": "Bandra-Kurla flooding during heavy rain — access restored", "resolved_hours_ago": 4},
    ],
    "delhi": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Delhi PWD road repair at Connaught Place inner circle — lane restriction"},
        {"incident_type": "event",     "severity": "minor",    "description": "VIP movement on Rajpath causing signal holds on Janpath"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Auto-rickshaw collision at Lajpat Nagar crossroads — cleared", "resolved_hours_ago": 2},
    ],
    "chennai": [
        {"incident_type": "roadwork",  "severity": "moderate", "description": "Storm drain work on Anna Salai — left lane closed near Gemini flyover"},
        {"incident_type": "accident",  "severity": "minor",    "description": "Lorry breakdown on OMR Road — moved to shoulder, traffic flowing", "resolved_hours_ago": 1},
    ],
    "pune": [
        {"incident_type": "accident",  "severity": "moderate", "description": "Two-vehicle collision on Pune-Solapur highway, police on scene"},
        {"incident_type": "roadwork",  "severity": "minor",    "description": "Pothole repair on Baner road — completed", "resolved_hours_ago": 2},
    ],
}
# (location_name, latitude, longitude)
_INCIDENT_SEED_AREAS: dict[str, list[tuple]] = {
    "bangalore": [
        ("MG Road, Bangalore",  12.9756, 77.6099),
        ("Silk Board Junction", 12.9172, 77.6235),
        ("Whitefield",          12.9698, 77.7500),
        ("Koramangala",         12.9352, 77.6245),
        ("Electronic City",     12.8399, 77.6770),
        ("Hebbal Flyover",      13.0450, 77.5950),
    ],
    "hyderabad": [
        ("Hitech City",  17.4486, 78.3908),
        ("Gachibowli",   17.4401, 78.3489),
        ("Ameerpet",     17.4374, 78.4487),
        ("Begumpet",     17.4432, 78.4682),
        ("LB Nagar",     17.3481, 78.5494),
    ],
    "mumbai": [
        ("LBS Road, Mumbai",      19.0748, 72.8856),
        ("Worli Sea Link",        19.0195, 72.8144),
        ("Bandra Kurla Complex",  19.0660, 72.8680),
    ],
    "delhi": [
        ("Connaught Place", 28.6315, 77.2167),
        ("Rajpath",         28.6129, 77.2295),
        ("Lajpat Nagar",    28.5700, 77.2430),
    ],
    "chennai": [
        ("Anna Salai",   13.0524, 80.2494),
        ("OMR Road Chennai", 12.9010, 80.2279),
    ],
    "pune": [
        ("Pune-Solapur Highway", 18.5204, 73.8567),
        ("Baner Road",           18.5590, 73.7868),
    ],
}


def _auto_seed_incidents(location: str, db: Session) -> None:
    """Idempotent: insert any missing seed incidents for a location."""
    key = next((k for k in _INCIDENT_SEEDS if k in location.lower()), None)
    if key is None:
        return

    templates = _INCIDENT_SEEDS[key]
    areas = _INCIDENT_SEED_AREAS.get(key, [(location, None, None)])
    now = datetime.now(timezone.utc)
    added = False
    for i, tmpl in enumerate(templates):
        area_name, lat, lon = areas[i % len(areas)]
        exists = (
            db.query(Incident)
            .filter(Incident.location == area_name, Incident.incident_type == tmpl["incident_type"])
            .first()
        )
        if exists:
            continue
        resolved_hours = tmpl.get("resolved_hours_ago")
        resolved_at = now - timedelta(hours=resolved_hours) if resolved_hours else None
        inc = Incident(
            location=area_name,
            latitude=lat,
            longitude=lon,
            incident_type=tmpl["incident_type"],
            severity=tmpl["severity"],
            description=tmpl["description"],
            is_active=resolved_at is None,
            resolved_at=resolved_at,
            reported_at=now - timedelta(minutes=random.randint(10, 180)),
        )
        db.add(inc)
        added = True
    if added:
        db.commit()


@router.get("/incidents")
def get_incidents(
    active_only: bool = Query(True),
    location: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Get road incidents, optionally filtered.

    Active incidents include an `estimated_resolution` timestamp based on severity.
    Resolved incidents show the actual `resolved_at` timestamp.
    """
    query = db.query(Incident)
    if active_only:
        query = query.filter(Incident.is_active == True)
    if location:
        query = query.filter(_location_filter_fn(Incident.location, location))
    results = query.order_by(desc(Incident.reported_at)).all()

    if location:
        city_key = next((k for k in _INCIDENT_SEEDS if k in location.lower()), None)
        expected = len(_INCIDENT_SEEDS[city_key]) if city_key else 0
        if expected > 0 and len(results) < expected:
            _auto_seed_incidents(location, db)
            results = query.order_by(desc(Incident.reported_at)).all()

    return [_incident_out(i) for i in results]


def _incident_out(inc: Incident) -> dict:
    """Serialize an Incident ORM object to a response dict with uuid and estimated_resolution."""
    hours = _SEVERITY_RESOLUTION_HOURS.get(inc.severity or "moderate", 3)
    estimated = (
        None if not inc.is_active
        else (inc.reported_at + timedelta(hours=hours))
    )
    return {
        "id": inc.id,
        "incident_uuid": inc.incident_uuid,
        "location": inc.location,
        "latitude": inc.latitude,
        "longitude": inc.longitude,
        "incident_type": inc.incident_type,
        "severity": inc.severity,
        "description": inc.description,
        "reported_at": inc.reported_at,
        "is_active": inc.is_active,
        "resolved_at": inc.resolved_at,
        "estimated_resolution": estimated,
    }


@router.post("/incidents", status_code=201)
def report_incident(payload: IncidentCreate, db: Session = Depends(get_db)):
    """Report a new road incident.

    - Leave `resolved_at` empty for an active incident (most common case).
    - Set `resolved_at` only when reporting a past incident that has already been cleared.
    - Returns the created incident **plus** all currently active incidents at that location.
    """
    data = payload.model_dump()
    resolved_at = data.pop("resolved_at", None)
    is_active = resolved_at is None
    incident = Incident(**data, is_active=is_active, resolved_at=resolved_at)
    db.add(incident)
    db.commit()
    db.refresh(incident)

    active_at_location = (
        db.query(Incident)
        .filter(
            Incident.location.ilike(f"%{incident.location}%"),
            Incident.is_active.is_(True),
        )
        .order_by(desc(Incident.reported_at))
        .all()
    )

    return {
        "message": "Incident reported successfully",
        "incident": _incident_out(incident),
        "active_incidents_at_location": [_incident_out(i) for i in active_at_location],
        "total_active_at_location": len(active_at_location),
    }


@router.patch("/incidents/{incident_id}/resolve")
def resolve_incident(
    incident_id: str = Path(
        ...,
        description="Incident numeric `id` **or** `incident_uuid` — both accepted. Get either value from `GET /api/v1/traffic/incidents`.",
        openapi_examples={"default": {"value": "1"}},
    ),
    db: Session = Depends(get_db),
):
    """Mark an incident as resolved. Sets `is_active=false` and stamps `resolved_at` with the current time.

    Accepts either the numeric `id` (e.g. `1`) or the `incident_uuid` string.
    """
    if incident_id.isdigit():
        incident = db.query(Incident).filter(Incident.id == int(incident_id)).first()
    else:
        incident = db.query(Incident).filter(Incident.incident_uuid == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.is_active = False
    incident.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(incident)
    return {
        "message": "Incident marked as resolved",
        "incident": _incident_out(incident),
    }


# ─── Prediction ────────────────────────────────────────────────────────────────

@router.post("/predict", status_code=201)
def predict_congestion(
    payload: PredictionRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Predict traffic congestion for a location N hours ahead using historical patterns.

    Uses the last 30 days of recorded data to find the most common congestion
    level at the target hour of day. Saves the result to prediction_results.
    """
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
    # Use IST hour so it matches the IST timestamps stored in traffic_records
    ist_hour = datetime.now(_IST).hour
    target_hour = (ist_hour + payload.hours_ahead) % 24
    result = predict_traffic_congestion(payload.location, target_hour, db)

    saved = save_prediction(
        location=result["location"],
        predicted_congestion=result["predicted_congestion"],
        confidence_score=result["confidence_score"],
        hours_ahead=payload.hours_ahead,
        db=db,
    )

    return {
        **result,
        "hours_ahead": payload.hours_ahead,
        "prediction_id": saved.id,
        "prediction_uuid": saved.prediction_uuid,
        "prediction_for": saved.prediction_for.isoformat(),
    }


@router.get("/predictions/location")
def get_predictions_by_location(
    location: str = Query(..., description="Location name to filter predictions (e.g. Bangalore, Gachibowli)"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Get the latest saved predictions for a specific location.

    If no predictions exist yet they are auto-generated for the next 6 hours
    from historical data and saved before being returned.
    """
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
    now_ist = datetime.now(_IST)

    _VALID_LEVELS = {"low", "medium", "high"}
    _LEVEL_MAP = {"very_high": "high", "very_low": "low"}

    # Expire stale predictions (prediction_for already passed) and invalid congestion levels
    stale = (
        db.query(PredictionResult)
        .filter(
            PredictionResult.location.ilike(f"%{location}%"),
            PredictionResult.is_active == True,
        )
        .all()
    )
    needs_commit = False
    for p in stale:
        pred_for = p.prediction_for
        if pred_for and pred_for.tzinfo is None:
            from datetime import timezone as _tz
            pred_for = pred_for.replace(tzinfo=_tz.utc)
        if pred_for and pred_for.astimezone(_IST) < now_ist:
            p.is_active = False
            needs_commit = True
        if p.predicted_congestion not in _VALID_LEVELS:
            p.predicted_congestion = _LEVEL_MAP.get(p.predicted_congestion, "medium")
            needs_commit = True
    if needs_commit:
        db.commit()

    predictions = (
        db.query(PredictionResult)
        .filter(
            PredictionResult.location.ilike(f"%{location}%"),
            PredictionResult.is_active == True,
        )
        .order_by(desc(PredictionResult.prediction_for))
        .limit(limit)
        .all()
    )

    auto_generated = False
    if not predictions:
        predictions = _auto_generate_predictions(location, db)
        auto_generated = True

    return {
        "location": location,
        "total": len(predictions),
        "auto_generated": auto_generated,
        "note": "Predictions were auto-generated from historical data" if auto_generated else None,
        "predictions": [
            {
                "id": p.id,
                "prediction_uuid": p.prediction_uuid,
                "predicted_congestion": p.predicted_congestion,
                "confidence_score": p.confidence_score,
                "prediction_for": p.prediction_for.isoformat() if p.prediction_for else None,
                "model_version": p.model_version,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in predictions
        ],
    }


# ─── Location Search ───────────────────────────────────────────────────────────

@router.get("/locations/search")
def search_locations(
    q: str = Query(..., min_length=2, description="Search query for location name"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    """Search monitored locations by name and return live traffic status.

    City names (e.g. 'Bangalore', 'Hyderabad') expand to all tracked neighbourhoods.
    """
    _LEVEL_MAP = {"very_high": "high", "very_low": "low"}
    _VALID = {"low", "medium", "high"}

    location_filter = _location_filter_fn(TrafficRecord.location, q)

    freshness_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    subq = (
        db.query(
            TrafficRecord.location,
            func.max(TrafficRecord.created_at).label("latest"),
        )
        .filter(location_filter)
        .filter(TrafficRecord.created_at >= freshness_cutoff)
        .group_by(TrafficRecord.location)
        .subquery()
    )

    records = (
        db.query(TrafficRecord)
        .join(
            subq,
            (TrafficRecord.location == subq.c.location)
            & (TrafficRecord.created_at == subq.c.latest),
        )
        .limit(limit)
        .all()
    )

    def _normalize(level):
        if level is None:
            return "unknown"
        return level if level in _VALID else _LEVEL_MAP.get(level, "medium")

    return {
        "query": q,
        "total": len(records),
        "results": [
            {
                "location": r.location,
                "congestion_level": _normalize(r.congestion_level),
                "average_speed_kmh": r.average_speed,
                "vehicle_count": r.vehicle_count,
                "last_updated": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }


# ─── CSV Export ────────────────────────────────────────────────────────────────

@router.get("/export")
def export_traffic_csv(
    location: Optional[str] = Query(None, description="Filter by location name or city (e.g. Bangalore, Hyderabad)"),
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD (inclusive)"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD (inclusive)"),
    congestion_level: Optional[str] = Query(None, description="low / medium / high"),
    limit: int = Query(1000, ge=1, le=10000, description="Max rows to export"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Download traffic records as a CSV file with optional filters.

    City names (e.g. 'Bangalore') expand to all tracked neighbourhoods automatically.
    """
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")

    if congestion_level and congestion_level not in ALLOWED_CONGESTION:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="congestion_level must be low, medium, or high")

    query = db.query(TrafficRecord)

    if location:
        query = query.filter(_location_filter_fn(TrafficRecord.location, location))

    if congestion_level:
        query = query.filter(TrafficRecord.congestion_level == congestion_level)

    if date_from:
        try:
            dt = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=_IST)
            query = query.filter(TrafficRecord.created_at >= dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="date_from must be YYYY-MM-DD")

    if date_to:
        try:
            # include the full end day
            dt = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=_IST)
            dt = dt.replace(hour=23, minute=59, second=59)
            query = query.filter(TrafficRecord.created_at <= dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="date_to must be YYYY-MM-DD")

    records = query.order_by(desc(TrafficRecord.created_at)).limit(limit).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "record_uuid", "location", "latitude", "longitude",
        "vehicle_count", "average_speed_kmh", "congestion_level",
        "road_type", "timestamp", "created_at",
    ])
    for r in records:
        writer.writerow([
            r.id, r.record_uuid or "", r.location, r.latitude, r.longitude,
            r.vehicle_count, r.average_speed, r.congestion_level,
            r.road_type,
            r.timestamp.isoformat() if r.timestamp else "",
            r.created_at.isoformat() if r.created_at else "",
        ])

    output.seek(0)
    filename = f"flowcast_traffic_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Summary ───────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Full stats overview of the traffic database."""
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
    now = datetime.now(_IST)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_24h = now - timedelta(hours=24)

    # ── Traffic records ──────────────────────────────────────────────────────
    total_records = db.query(func.count(TrafficRecord.id)).scalar() or 0
    records_today = db.query(func.count(TrafficRecord.id)).filter(TrafficRecord.created_at >= today_start).scalar() or 0
    records_24h   = db.query(func.count(TrafficRecord.id)).filter(TrafficRecord.created_at >= last_24h).scalar() or 0

    congestion_counts = {}
    for level in ("low", "medium", "high"):
        congestion_counts[level] = (
            db.query(func.count(TrafficRecord.id))
            .filter(TrafficRecord.congestion_level == level)
            .scalar() or 0
        )

    locations_tracked = (
        db.query(func.count(func.distinct(TrafficRecord.location))).scalar() or 0
    )

    latest_record = db.query(func.max(TrafficRecord.created_at)).scalar()
    avg_speed = db.query(func.avg(TrafficRecord.average_speed)).filter(
        TrafficRecord.created_at >= last_24h,
        TrafficRecord.average_speed.isnot(None),
    ).scalar()

    # Top 3 most congested locations (last 24 h)
    high_24h = (
        db.query(TrafficRecord.location, func.count(TrafficRecord.id).label("cnt"))
        .filter(TrafficRecord.created_at >= last_24h, TrafficRecord.congestion_level == "high")
        .group_by(TrafficRecord.location)
        .order_by(desc("cnt"))
        .limit(3)
        .all()
    )

    # ── Predictions ──────────────────────────────────────────────────────────
    total_predictions  = db.query(func.count(PredictionResult.id)).scalar() or 0
    active_predictions = db.query(func.count(PredictionResult.id)).filter(PredictionResult.is_active == True).scalar() or 0

    # ── Incidents ────────────────────────────────────────────────────────────
    total_incidents  = db.query(func.count(Incident.id)).scalar() or 0
    active_incidents = db.query(func.count(Incident.id)).filter(Incident.is_active == True).scalar() or 0
    resolved_incidents = total_incidents - active_incidents

    severity_counts = {}
    for sev in ("minor", "moderate", "severe"):
        severity_counts[sev] = (
            db.query(func.count(Incident.id))
            .filter(Incident.severity == sev, Incident.is_active == True)
            .scalar() or 0
        )

    return {
        "generated_at": now.isoformat(),
        "traffic_records": {
            "total": total_records,
            "added_today": records_today,
            "added_last_24h": records_24h,
            "locations_tracked": locations_tracked,
            "average_speed_kmh_24h": round(avg_speed, 1) if avg_speed else None,
            "last_record_at": latest_record.isoformat() if latest_record else None,
            "congestion_breakdown": congestion_counts,
            "top_congested_locations_24h": [
                {"location": loc, "high_congestion_records": cnt}
                for loc, cnt in high_24h
            ],
        },
        "predictions": {
            "total": total_predictions,
            "active": active_predictions,
        },
        "incidents": {
            "total": total_incidents,
            "active": active_incidents,
            "resolved": resolved_incidents,
            "active_by_severity": severity_counts,
        },
    }


# ─── Leaderboard ───────────────────────────────────────────────────────────────

_CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}


@router.get("/leaderboard")
def get_traffic_leaderboard(
    top: int = Query(10, ge=1, le=50, description="Number of locations to return"),
    order: str = Query("worst", description="worst = most congested first, best = least congested"),
    hours: int = Query(1, ge=1, le=6, description="Look-back window in hours"),
    db: Session = Depends(get_db),
) -> dict:
    """Rank Hyderabad locations by current congestion — worst or best first.

    Aggregates the last N hours of traffic records per location and scores
    each by congestion level + vehicle count. Useful for dashboards and alerts.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = (
        db.query(
            TrafficRecord.location,
            func.avg(TrafficRecord.vehicle_count).label("avg_vehicles"),
            func.avg(TrafficRecord.average_speed).label("avg_speed"),
            func.count(TrafficRecord.id).label("record_count"),
        )
        .filter(TrafficRecord.timestamp >= since)
        .group_by(TrafficRecord.location)
        .all()
    )

    results = []
    for row in rows:
        avg_v = float(row.avg_vehicles or 0)
        avg_s = float(row.avg_speed) if row.avg_speed else None
        level = classify_congestion(int(avg_v), avg_s)
        results.append({
            "location": row.location,
            "congestion_level": level,
            "congestion_score": _CONGESTION_SCORE.get(level, 1),
            "avg_vehicle_count": round(avg_v, 1),
            "avg_speed_kmh": round(avg_s, 1) if avg_s else None,
            "record_count": row.record_count,
        })

    reverse = order != "best"
    results.sort(key=lambda x: (x["congestion_score"], x["avg_vehicle_count"]), reverse=reverse)

    return {
        "order": order,
        "period_hours": hours,
        "total_locations_observed": len(results),
        "leaderboard": results[:top],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Speed Anomaly Detection ───────────────────────────────────────────────────

@router.get("/anomalies")
def get_speed_anomalies(
    window_minutes: int = Query(30, ge=10, le=120, description="Size of each comparison window in minutes"),
    threshold_pct: float = Query(30.0, ge=5.0, le=90.0, description="Minimum speed drop % to flag"),
    db: Session = Depends(get_db),
) -> dict:
    """Detect locations where speed dropped sharply — early indicator of accidents or closures.

    Compares average speed in the current window against the previous window of the
    same duration. Locations exceeding the threshold are ranked by severity.
    """
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")

    now = datetime.now(_IST)
    curr_start = now - timedelta(minutes=window_minutes)
    prev_start = curr_start - timedelta(minutes=window_minutes)

    def _avg_by_location(from_dt, to_dt):
        rows = (
            db.query(
                TrafficRecord.location,
                func.avg(TrafficRecord.average_speed).label("avg_speed"),
                func.avg(TrafficRecord.vehicle_count).label("avg_vehicles"),
                func.count(TrafficRecord.id).label("record_count"),
            )
            .filter(
                TrafficRecord.timestamp >= from_dt,
                TrafficRecord.timestamp < to_dt,
                TrafficRecord.average_speed.isnot(None),
            )
            .group_by(TrafficRecord.location)
            .all()
        )
        return {
            r.location: {
                "avg_speed": float(r.avg_speed),
                "avg_vehicles": float(r.avg_vehicles or 0),
                "record_count": int(r.record_count),
            }
            for r in rows
        }

    current  = _avg_by_location(curr_start, now)
    previous = _avg_by_location(prev_start, curr_start)

    curr_record_count = sum(v["record_count"] for v in current.values())
    prev_record_count = sum(v["record_count"] for v in previous.values())
    locations_compared = len(set(current) & set(previous))

    anomalies = []
    for location, curr in current.items():
        if location not in previous:
            continue
        prev_speed = previous[location]["avg_speed"]
        curr_speed = curr["avg_speed"]
        if prev_speed <= 0:
            continue
        drop_pct = (prev_speed - curr_speed) / prev_speed * 100
        if drop_pct < threshold_pct:
            continue
        severity = "critical" if drop_pct >= 60 else "high" if drop_pct >= 45 else "moderate"
        anomalies.append({
            "location": location,
            "speed_drop_pct": round(drop_pct, 1),
            "prev_avg_speed_kmh": round(prev_speed, 1),
            "curr_avg_speed_kmh": round(curr_speed, 1),
            "curr_vehicle_count": int(curr["avg_vehicles"]),
            "severity": severity,
            "possible_cause": "accident or road closure" if drop_pct >= 60 else "sudden congestion buildup",
        })

    anomalies.sort(key=lambda x: x["speed_drop_pct"], reverse=True)

    # Build speed comparison for all matched locations (sorted by biggest change)
    speed_comparison = []
    for location, curr in current.items():
        if location not in previous:
            continue
        prev_speed = previous[location]["avg_speed"]
        curr_speed = curr["avg_speed"]
        if prev_speed <= 0:
            continue
        change_pct = (prev_speed - curr_speed) / prev_speed * 100
        speed_comparison.append({
            "location": location,
            "prev_avg_speed_kmh": round(prev_speed, 1),
            "curr_avg_speed_kmh": round(curr_speed, 1),
            "change_pct": round(change_pct, 1),
            "trend": "drop" if change_pct > 2 else "rise" if change_pct < -2 else "stable",
        })
    speed_comparison.sort(key=lambda x: x["change_pct"], reverse=True)

    diagnosis = None
    if curr_record_count == 0:
        diagnosis = f"No traffic records found in the last {window_minutes} min — real-time collector may not have run yet."
    elif prev_record_count == 0:
        diagnosis = f"No records in the previous {window_minutes}-min window — try increasing window_minutes."
    elif locations_compared == 0:
        diagnosis = "No locations had data in both windows — windows may not overlap the same areas."
    elif len(anomalies) == 0:
        biggest = speed_comparison[0] if speed_comparison else None
        biggest_info = f" Biggest change: {biggest['location']} at {biggest['change_pct']}%." if biggest else ""
        diagnosis = f"All {locations_compared} locations had speed changes below {threshold_pct}%.{biggest_info} Try lowering threshold_pct to see smaller changes."

    return {
        "window_minutes": window_minutes,
        "threshold_pct": threshold_pct,
        "anomalies_detected": len(anomalies),
        "anomalies": anomalies,
        "diagnosis": diagnosis,
        "speed_comparison": speed_comparison,
        "data_quality": {
            "records_in_current_window": curr_record_count,
            "records_in_previous_window": prev_record_count,
            "locations_compared": locations_compared,
            "current_window": f"{curr_start.strftime('%H:%M')} – {now.strftime('%H:%M')} IST",
            "previous_window": f"{prev_start.strftime('%H:%M')} – {curr_start.strftime('%H:%M')} IST",
        },
        "checked_at": now.isoformat(),
    }


# ─── Live Data Sources Status ─────────────────────────────────────────────────

@router.get("/sources")
def get_data_sources() -> dict:
    """Show which external traffic APIs are configured and active.

    Use this to verify real-time data is flowing before querying live endpoints.
    Configure keys in .env to unlock real traffic data:
      - HERE_API_KEY    → 250,000 free calls/month (recommended)
      - TOMTOM_API_KEY  → 2,500 free calls/day
      - GOOGLE_MAPS_DIRECTIONS_API_KEY → district-level data
    """
    import os
    from app.services.here_traffic_service import (
        HERE_API_KEY as _HERE_KEY,
        is_available as _here_ok,
        _key_invalid as _here_invalid,
    )
    from app.services.tomtom_service import (
        TOMTOM_API_KEY as _TT_KEY,
        _key_ok as _tt_ok,
        _key_invalid as _tt_invalid,
    )

    _PLACEHOLDERS = {"", "your_google_maps_key_here", "your_key_here"}
    _google_raw = os.getenv("GOOGLE_MAPS_DIRECTIONS_API_KEY", "")
    _google_configured = _google_raw not in _PLACEHOLDERS

    here_configured  = bool(_HERE_KEY)
    tomtom_configured = bool(_TT_KEY)

    if _here_ok():
        active_source = "here"
    elif _tt_ok():
        active_source = "tomtom"
    else:
        active_source = "simulation"

    return {
        "active_source": active_source,
        "note": (
            "Data is coming from real live traffic APIs."
            if active_source != "simulation"
            else "No real API keys configured — using physics-based simulation. "
                 "Add HERE_API_KEY to .env for free live traffic data."
        ),
        "sources": {
            "here": {
                "name":        "HERE Maps Traffic API v7",
                "configured":  here_configured,
                "active":      _here_ok(),
                "rejected":    _here_invalid,
                "free_tier":   "250,000 calls/month — no credit card",
                "sign_up":     "https://developer.here.com",
                "env_key":     "HERE_API_KEY",
                "covers":      "80 major India locations (flow + incidents)",
            },
            "tomtom": {
                "name":        "TomTom Traffic API",
                "configured":  tomtom_configured,
                "active":      _tt_ok(),
                "rejected":    _tt_invalid,
                "free_tier":   "2,500 calls/day — no credit card",
                "sign_up":     "https://developer.tomtom.com",
                "env_key":     "TOMTOM_API_KEY",
                "covers":      "80 major India locations (flow + incidents, fallback to HERE)",
            },
            "google_maps": {
                "name":        "Google Maps Directions API",
                "configured":  _google_configured,
                "active":      _google_configured,
                "free_tier":   "$200/month credit (~40,000 requests free)",
                "sign_up":     "https://console.cloud.google.com",
                "env_key":     "GOOGLE_MAPS_DIRECTIONS_API_KEY",
                "covers":      "766 Indian districts (congestion ratio via duration_in_traffic)",
            },
            "simulation": {
                "name":        "Physics-based simulation",
                "configured":  True,
                "active":      active_source == "simulation",
                "note":        "Always available as last resort — not real data",
            },
        },
        "collection_interval_minutes": 30,
        "locations_monitored":         80,
        "districts_monitored":         766,
    }


# ─── Incident Statistics ────────────────────────────────────────────────────────

@router.get("/incidents/stats")
def get_incident_stats(
    days: int = Query(7, ge=1, le=90, description="Days of history to analyse"),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated incident statistics: counts by type, severity, hotspot locations,
    and average resolution time.

    Useful for operations dashboards and identifying chronic problem zones.
    """
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")

    now_ist = datetime.now(_IST)
    since = now_ist - timedelta(days=days)

    incidents = db.query(Incident).filter(Incident.reported_at >= since).all()

    if not incidents:
        return {
            "period_days": days,
            "period_start": since.isoformat(),
            "period_end": now_ist.isoformat(),
            "total_incidents": 0,
            "message": "No incidents recorded in this period",
        }

    active   = [i for i in incidents if i.is_active]
    resolved = [i for i in incidents if not i.is_active and i.resolved_at and i.reported_at]

    # Resolution time — normalise both sides to UTC before subtracting
    avg_resolution_minutes = None
    avg_resolution_hours   = None
    if resolved:
        durations = []
        for i in resolved:
            try:
                res = i.resolved_at if i.resolved_at.tzinfo else i.resolved_at.replace(tzinfo=timezone.utc)
                rep = i.reported_at if i.reported_at.tzinfo else i.reported_at.replace(tzinfo=timezone.utc)
                secs = (res - rep).total_seconds()
                if secs > 0:
                    durations.append(secs)
            except Exception:
                pass
        if durations:
            avg_secs = sum(durations) / len(durations)
            avg_resolution_minutes = round(avg_secs / 60, 1)
            avg_resolution_hours   = round(avg_secs / 3600, 2)

    by_type     = Counter(i.incident_type for i in incidents)
    by_severity = Counter(i.severity or "unspecified" for i in incidents)
    by_location = Counter(i.location for i in incidents)

    # Active vs resolved breakdown per type
    active_by_type   = Counter(i.incident_type for i in active)
    resolved_by_type = Counter(i.incident_type for i in resolved)

    # Daily counts in IST date
    daily_counts: Counter = Counter()
    for i in incidents:
        if i.reported_at:
            rep_ist = i.reported_at.astimezone(_IST) if i.reported_at.tzinfo else i.reported_at
            daily_counts[rep_ist.strftime("%Y-%m-%d")] += 1
        else:
            daily_counts["unknown"] += 1

    return {
        "period_days": days,
        "period_start": since.isoformat(),
        "period_end": now_ist.isoformat(),
        "total_incidents": len(incidents),
        "active_incidents": len(active),
        "resolved_incidents": len(resolved),
        "avg_resolution_minutes": avg_resolution_minutes,
        "avg_resolution_hours": avg_resolution_hours,
        "by_type": dict(by_type.most_common()),
        "active_by_type": dict(active_by_type.most_common()),
        "resolved_by_type": dict(resolved_by_type.most_common()),
        "by_severity": dict(by_severity.most_common()),
        "top_5_hotspot_locations": dict(by_location.most_common(5)),
        "daily_counts": dict(sorted(daily_counts.items())),
    }
