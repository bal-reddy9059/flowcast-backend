"""
Crowdsourced incident reporting endpoints.

POST   /incidents            — report a new incident (auth required)
GET    /incidents            — list active incidents (public)
GET    /incidents/{id}       — single incident detail (public)
POST   /incidents/{id}/upvote   — confirm the report is still valid
POST   /incidents/{id}/downvote — mark the report as inaccurate
DELETE /incidents/{id}       — reporter or admin resolves the incident

Auto-resolution rules (enforced by background monitor in main.py):
  - expires_at < now                              → resolved automatically
  - downvotes >= upvotes + 3 (and upvotes < 5)   → auto-resolved as inaccurate
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictor import Incident
from app.services.auth_service import get_current_user
from app.models.user import User

router = APIRouter(prefix="/incidents", tags=["Incident Reporting"])
logger = logging.getLogger(__name__)

_VALID_TYPES = {"accident", "roadwork", "closure", "event", "flooding", "police", "other"}
_VALID_SEVERITIES = {"minor", "moderate", "severe"}


# ── Request / Response schemas ────────────────────────────────────────────────

class IncidentCreate(BaseModel):
    location: str = Field(..., min_length=2, max_length=255,
                          description="Location name, e.g. 'Silk Board Junction'")
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    incident_type: str = Field(...,
                               description="accident | roadwork | closure | event | flooding | police | other")
    severity: Optional[str] = Field("moderate",
                                    description="minor | moderate | severe")
    description: Optional[str] = Field(None, max_length=500)
    expires_hours: Optional[int] = Field(4, ge=1, le=48,
                                         description="Auto-resolve after N hours (1–48, default 4)")


def _serialize(inc: Incident) -> dict:
    return {
        "id":             inc.id,
        "incident_uuid":  inc.incident_uuid,
        "location":       inc.location,
        "latitude":       inc.latitude,
        "longitude":      inc.longitude,
        "incident_type":  inc.incident_type,
        "severity":       inc.severity,
        "description":    inc.description,
        "reported_by":    inc.reported_by,
        "upvotes":        inc.upvotes or 0,
        "downvotes":      inc.downvotes or 0,
        "is_active":      inc.is_active,
        "reported_at":    inc.reported_at.isoformat() if inc.reported_at else None,
        "expires_at":     inc.expires_at.isoformat() if inc.expires_at else None,
        "resolved_at":    inc.resolved_at.isoformat() if inc.resolved_at else None,
        "community_score": (inc.upvotes or 0) - (inc.downvotes or 0),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Report a new road incident",
)
def report_incident(
    body: IncidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Report a road incident at a given location.

    Requires authentication. The incident stays active until:
    - `expires_hours` elapses (default 4 h)
    - Community downvotes it out of existence
    - The reporter or an admin resolves it via DELETE
    """
    if body.incident_type not in _VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"incident_type must be one of: {sorted(_VALID_TYPES)}",
        )
    if body.severity and body.severity not in _VALID_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=f"severity must be one of: {sorted(_VALID_SEVERITIES)}",
        )

    now = datetime.now(timezone.utc)
    incident = Incident(
        location=body.location.strip(),
        latitude=body.latitude,
        longitude=body.longitude,
        incident_type=body.incident_type,
        severity=body.severity or "moderate",
        description=body.description,
        reported_by=str(current_user.id),
        upvotes=1,           # reporter's own implicit vote
        downvotes=0,
        is_active=True,
        reported_at=now,
        expires_at=now + timedelta(hours=body.expires_hours or 4),
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)

    logger.info(
        "Incident reported by %s: %s at %s (expires %s)",
        current_user.email, body.incident_type, body.location,
        incident.expires_at.isoformat() if incident.expires_at else "never",
    )
    return {"message": "Incident reported successfully", "incident": _serialize(incident)}


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="List active road incidents",
)
def list_incidents(
    location: Optional[str] = Query(None, description="Filter by location name (partial match)"),
    incident_type: Optional[str] = Query(None, description="Filter by type"),
    severity: Optional[str] = Query(None, description="Filter by severity"),
    include_resolved: bool = Query(False, description="Include resolved incidents"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Return all active (or optionally resolved) incidents, newest first."""
    query = db.query(Incident)

    if not include_resolved:
        query = query.filter(Incident.is_active == True)

    if location:
        query = query.filter(Incident.location.ilike(f"%{location}%"))
    if incident_type:
        query = query.filter(Incident.incident_type == incident_type)
    if severity:
        query = query.filter(Incident.severity == severity)

    incidents = query.order_by(Incident.reported_at.desc()).limit(limit).all()

    return {
        "total": len(incidents),
        "incidents": [_serialize(i) for i in incidents],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/{incident_id}",
    status_code=status.HTTP_200_OK,
    summary="Get a single incident by ID",
)
def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Fetch details for a specific incident."""
    inc = db.query(Incident).filter(Incident.id == incident_id).first()
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _serialize(inc)


@router.post(
    "/{incident_id}/upvote",
    status_code=status.HTTP_200_OK,
    summary="Upvote: confirm the incident is still valid",
)
def upvote_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Increment the upvote count — confirms the incident is real and ongoing."""
    inc = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.is_active == True,
    ).first()
    if inc is None:
        raise HTTPException(status_code=404, detail="Active incident not found")

    inc.upvotes = (inc.upvotes or 0) + 1
    db.commit()
    db.refresh(inc)

    logger.info("Incident %d upvoted by %s (total=%d)", incident_id, current_user.email, inc.upvotes)
    return {
        "message": "Upvoted — thanks for confirming!",
        "upvotes": inc.upvotes,
        "downvotes": inc.downvotes or 0,
        "community_score": (inc.upvotes or 0) - (inc.downvotes or 0),
    }


@router.post(
    "/{incident_id}/downvote",
    status_code=status.HTTP_200_OK,
    summary="Downvote: mark the incident as inaccurate or cleared",
)
def downvote_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Increment the downvote count.

    When downvotes exceed upvotes by 3 or more (and upvotes < 5),
    the incident is auto-resolved by the background monitor.
    """
    inc = db.query(Incident).filter(
        Incident.id == incident_id,
        Incident.is_active == True,
    ).first()
    if inc is None:
        raise HTTPException(status_code=404, detail="Active incident not found")

    inc.downvotes = (inc.downvotes or 0) + 1

    # Immediate auto-resolve if community clearly rejects it
    upvotes = inc.upvotes or 0
    downvotes = inc.downvotes
    if downvotes >= upvotes + 3 and upvotes < 5:
        inc.is_active = False
        inc.resolved_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Incident %d auto-resolved by community downvotes (%d vs %d)",
                    incident_id, upvotes, downvotes)
        return {
            "message": "Incident resolved — community marked it as inaccurate.",
            "upvotes": upvotes,
            "downvotes": downvotes,
            "community_score": upvotes - downvotes,
            "resolved": True,
        }

    db.commit()
    db.refresh(inc)

    logger.info("Incident %d downvoted by %s (total=%d)", incident_id, current_user.email, inc.downvotes)
    return {
        "message": "Downvoted — noted.",
        "upvotes": inc.upvotes or 0,
        "downvotes": inc.downvotes,
        "community_score": (inc.upvotes or 0) - inc.downvotes,
        "resolved": False,
    }


@router.delete(
    "/{incident_id}",
    status_code=status.HTTP_200_OK,
    summary="Resolve / remove an incident",
)
def resolve_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Mark an incident as resolved.

    Only the original reporter or an admin may resolve an incident.
    """
    inc = db.query(Incident).filter(Incident.id == incident_id).first()
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    is_owner = inc.reported_by == str(current_user.id)
    if not is_owner and not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only the reporter or an admin can resolve this incident.",
        )

    inc.is_active = False
    inc.resolved_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        "Incident %d resolved by %s (%s)",
        incident_id,
        current_user.email,
        "admin" if current_user.is_admin else "reporter",
    )
    return {
        "message": "Incident resolved and removed from active feed.",
        "incident_id": incident_id,
        "resolved_at": inc.resolved_at.isoformat(),
    }
