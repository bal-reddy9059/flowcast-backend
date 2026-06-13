"""Smart geofence zone endpoints."""

import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.zone import GeofenceZone, ZoneAlert
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/zones", tags=["Geofence Zones"])
logger = logging.getLogger(__name__)

_CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}
_THRESHOLD_PCT   = {"low": 40, "medium": 60, "high": 85}
_HEALTH_BY_LEVEL = {"low": 100, "medium": 65, "high": 30}

_DEMO_ZONES = [
    {
        "name": "Hitech City Corridor",
        "city": "Hyderabad",
        "zone_type": "rectangle",
        "lat_min": 17.43, "lat_max": 17.46,
        "lng_min": 78.36, "lng_max": 78.40,
        "congestion_threshold": "high",
    },
    {
        "name": "Bandra Kurla Complex",
        "city": "Mumbai",
        "zone_type": "circle",
        "center_lat": 19.0660, "center_lng": 72.8680,
        "radius_km": 3.0,
        "congestion_threshold": "high",
    },
    {
        "name": "Silk Board Junction",
        "city": "Bengaluru",
        "zone_type": "circle",
        "center_lat": 12.9172, "center_lng": 77.6235,
        "radius_km": 3.0,
        "congestion_threshold": "medium",
    },
    {
        "name": "Connaught Place",
        "city": "Delhi",
        "zone_type": "rectangle",
        "lat_min": 28.62, "lat_max": 28.64,
        "lng_min": 77.20, "lng_max": 77.23,
        "congestion_threshold": "medium",
    },
    {
        "name": "Anna Salai Stretch",
        "city": "Chennai",
        "zone_type": "rectangle",
        "lat_min": 13.04, "lat_max": 13.07,
        "lng_min": 80.24, "lng_max": 80.27,
        "congestion_threshold": "high",
    },
]

# Zones that should have today's breach alerts seeded (name → breach count)
_BREACH_SEED = {"Silk Board Junction": 9, "Connaught Place": 9}


def _seed_demo_zones(user_id: uuid.UUID, db: Session) -> list:
    """Idempotent: create demo geofence zones for a user who has none."""
    for z in _DEMO_ZONES:
        exists = db.query(GeofenceZone).filter(
            GeofenceZone.user_id == user_id,
            GeofenceZone.name == z["name"],
        ).first()
        if exists:
            continue
        zone = GeofenceZone(
            user_id=user_id,
            name=z["name"],
            zone_type=z["zone_type"],
            lat_min=z.get("lat_min"),
            lat_max=z.get("lat_max"),
            lng_min=z.get("lng_min"),
            lng_max=z.get("lng_max"),
            center_lat=z.get("center_lat"),
            center_lng=z.get("center_lng"),
            radius_km=z.get("radius_km"),
            congestion_threshold=z["congestion_threshold"],
        )
        db.add(zone)
    db.commit()
    return db.query(GeofenceZone).filter(
        GeofenceZone.user_id == user_id, GeofenceZone.is_active == True
    ).all()


def _seed_zone_alerts(zone: GeofenceZone, breach_count: int, db: Session) -> None:
    """Idempotent: seed today's breach alerts for a zone if fewer than breach_count exist."""
    import random as _rnd
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    existing = (
        db.query(ZoneAlert)
        .filter(ZoneAlert.zone_id == zone.id, ZoneAlert.triggered_at >= today_start)
        .count()
    )
    needed = breach_count - existing
    if needed <= 0:
        return
    _rnd.seed(str(zone.id))
    for i in range(needed):
        # Space alerts evenly across daytime (07:00–14:00 IST window)
        minutes_offset = _rnd.randint(0, 7 * 60) + i * 20
        triggered_at   = today_start + timedelta(hours=7, minutes=minutes_offset)
        affected        = json.dumps([zone.name])
        avg_speed       = round(_rnd.uniform(14.0, 28.0), 1)
        db.add(ZoneAlert(
            zone_id=zone.id,
            triggered_at=triggered_at,
            congestion_level="medium",
            affected_locations=affected,
            avg_speed_kmh=avg_speed,
        ))
    db.commit()


class ZoneCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    zone_type: str = Field("rectangle", pattern="^(rectangle|circle)$")
    org_id: Optional[uuid.UUID] = None
    # Rectangle
    lat_min: Optional[float] = Field(None, ge=6.0, le=37.5)
    lat_max: Optional[float] = Field(None, ge=6.0, le=37.5)
    lng_min: Optional[float] = Field(None, ge=68.0, le=97.5)
    lng_max: Optional[float] = Field(None, ge=68.0, le=97.5)
    # Circle
    center_lat: Optional[float] = Field(None, ge=6.0, le=37.5)
    center_lng: Optional[float] = Field(None, ge=68.0, le=97.5)
    radius_km: Optional[float] = Field(None, gt=0, le=500)
    congestion_threshold: str = Field("high", pattern="^(low|medium|high)$")

    @model_validator(mode="after")
    def check_geometry(self):
        if self.zone_type == "rectangle":
            if any(v is None for v in [self.lat_min, self.lat_max, self.lng_min, self.lng_max]):
                raise ValueError("Rectangle zones require lat_min, lat_max, lng_min, lng_max")
            if self.lat_min >= self.lat_max:
                raise ValueError("lat_min must be less than lat_max")
            if self.lng_min >= self.lng_max:
                raise ValueError("lng_min must be less than lng_max")
        elif self.zone_type == "circle":
            if any(v is None for v in [self.center_lat, self.center_lng, self.radius_km]):
                raise ValueError("Circle zones require center_lat, center_lng, radius_km")
        return self


@router.post("", status_code=status.HTTP_201_CREATED)
def create_zone(
    payload: ZoneCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Create a geofence zone. Alerts fire when congestion inside exceeds the threshold."""
    zone = GeofenceZone(
        user_id=current_user.id,
        org_id=payload.org_id,
        name=payload.name,
        zone_type=payload.zone_type,
        lat_min=payload.lat_min, lat_max=payload.lat_max,
        lng_min=payload.lng_min, lng_max=payload.lng_max,
        center_lat=payload.center_lat, center_lng=payload.center_lng,
        radius_km=payload.radius_km,
        congestion_threshold=payload.congestion_threshold,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    logger.info("Geofence zone '%s' created by user %s", zone.name, current_user.id)
    return _zone_dict(zone)


@router.get("/summary", status_code=status.HTTP_200_OK)
def zones_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Header stats — Total Zones, Active, Breaches Today, Cities."""
    zones = db.query(GeofenceZone).filter(
        GeofenceZone.user_id == current_user.id, GeofenceZone.is_active == True
    ).all()
    if not zones:
        zones = _seed_demo_zones(current_user.id, db)

    now        = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    zone_ids   = [z.id for z in zones]

    breaches_today = (
        db.query(ZoneAlert)
        .filter(ZoneAlert.zone_id.in_(zone_ids), ZoneAlert.triggered_at >= today_start)
        .count()
    )
    # Count unique cities from the demo meta (fall back to zone name)
    _city_map = {z["name"]: z["city"] for z in _DEMO_ZONES}
    cities = len({_city_map.get(z.name, z.name) for z in zones})

    return {
        "total_zones":    len(zones),
        "active_zones":   sum(1 for z in zones if z.is_active),
        "breaches_today": breaches_today,
        "cities":         cities,
        "updated_at":     now.isoformat(),
    }


@router.get("/alerts/recent", status_code=status.HTTP_200_OK)
def recent_alerts(
    limit: int = 20,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """Recent breach alerts across all the user's zones."""
    zones = db.query(GeofenceZone).filter(
        GeofenceZone.user_id == current_user.id, GeofenceZone.is_active == True
    ).all()
    if not zones:
        zones = _seed_demo_zones(current_user.id, db)

    zone_ids  = [z.id for z in zones]
    zone_name = {z.id: z.name for z in zones}

    alerts = (
        db.query(ZoneAlert)
        .filter(ZoneAlert.zone_id.in_(zone_ids))
        .order_by(ZoneAlert.triggered_at.desc())
        .limit(limit)
        .all()
    )

    def _fmt(a: ZoneAlert) -> dict:
        zname = zone_name.get(a.zone_id, "Unknown Zone")
        locs  = json.loads(a.affected_locations) if a.affected_locations else [zname]
        loc_str = ", ".join(locs[:2])
        level_label = {"low": "Low", "medium": "Moderate", "high": "High"}.get(a.congestion_level, "Moderate")
        threshold = next((z.congestion_threshold for z in zones if z.id == a.zone_id), "medium")
        action = "approaching threshold" if a.congestion_level != "high" else "threshold breached"
        return {
            "id":              str(a.id),
            "zone_id":         str(a.zone_id),
            "zone_name":       zname,
            "message":         f"{level_label} congestion at {loc_str} — {action}",
            "congestion_level": a.congestion_level,
            "avg_speed_kmh":   a.avg_speed_kmh,
            "triggered_at":    a.triggered_at.isoformat(),
            "time_label":      a.triggered_at.strftime("%I:%M %p").lstrip("0").lower()
                               if hasattr(a.triggered_at, "strftime") else "",
        }

    return {
        "alerts": [_fmt(a) for a in alerts],
        "total":  len(alerts),
    }


@router.get("", status_code=status.HTTP_200_OK)
def list_zones(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """List all active zones with live congestion status, health score, and breach count."""
    zones = db.query(GeofenceZone).filter(
        GeofenceZone.user_id == current_user.id, GeofenceZone.is_active == True
    ).all()
    if not zones:
        zones = _seed_demo_zones(current_user.id, db)

    # Seed breach alerts for zones that should have them
    _city_map = {z["name"]: z["city"] for z in _DEMO_ZONES}
    for zone in zones:
        if zone.name in _BREACH_SEED:
            _seed_zone_alerts(zone, _BREACH_SEED[zone.name], db)

    now         = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = []
    for zone in zones:
        # Live traffic inside the zone
        locations, avg_speed, dominant = _query_zone_traffic(zone, db)

        # Breaches today
        breaches_today = (
            db.query(ZoneAlert)
            .filter(ZoneAlert.zone_id == zone.id, ZoneAlert.triggered_at >= today_start)
            .count()
        )

        # Auto-trigger alert when threshold is currently breached
        if _CONGESTION_SCORE.get(dominant, 0) >= _CONGESTION_SCORE.get(zone.congestion_threshold, 2):
            last = (
                db.query(ZoneAlert)
                .filter(ZoneAlert.zone_id == zone.id)
                .order_by(ZoneAlert.triggered_at.desc())
                .first()
            )
            # Debounce: only one alert per 15 minutes
            if not last or (now - last.triggered_at.replace(tzinfo=timezone.utc)).seconds > 900:
                db.add(ZoneAlert(
                    zone_id=zone.id,
                    triggered_at=now,
                    congestion_level=dominant,
                    affected_locations=json.dumps([l["name"] for l in locations[:3]]),
                    avg_speed_kmh=round(avg_speed, 1) if avg_speed else None,
                ))
                db.commit()
                breaches_today += 1

        health = _HEALTH_BY_LEVEL.get(dominant, 65)
        d = _zone_dict(zone, city=_city_map.get(zone.name, ""))
        d.update({
            "current_congestion": dominant,
            "health_score":       health,
            "breaches_today":     breaches_today,
            "avg_speed_kmh":      round(avg_speed, 1) if avg_speed else None,
            "monitored_locations": locations,
        })
        result.append(d)

    return {"zones": result, "total": len(result)}


@router.get("/{zone_id}", status_code=status.HTTP_200_OK)
def get_zone(
    zone_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Get zone details with live status."""
    _city_map = {z["name"]: z["city"] for z in _DEMO_ZONES}
    zone = _get_zone_or_404(zone_id, current_user.id, db)
    locations, avg_speed, dominant = _query_zone_traffic(zone, db)
    health = _HEALTH_BY_LEVEL.get(dominant, 65)
    d = _zone_dict(zone, city=_city_map.get(zone.name, ""))
    d.update({
        "current_congestion":  dominant,
        "health_score":        health,
        "avg_speed_kmh":       round(avg_speed, 1) if avg_speed else None,
        "monitored_locations": locations,
        "threshold_breached":  _CONGESTION_SCORE.get(dominant, 0) >= _CONGESTION_SCORE.get(zone.congestion_threshold, 2),
    })
    return d


@router.delete("/{zone_id}", status_code=status.HTTP_200_OK)
def delete_zone(
    zone_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Delete a geofence zone."""
    zone = _get_zone_or_404(zone_id, current_user.id, db)
    zone.is_active = False
    db.commit()
    return {"message": f"Zone '{zone.name}' deleted"}


@router.get("/{zone_id}/status", status_code=status.HTTP_200_OK)
def zone_status(
    zone_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Live congestion status for all monitored locations inside the zone."""
    zone = _get_zone_or_404(zone_id, current_user.id, db)
    locations, avg_speed, dominant = _query_zone_traffic(zone, db)
    health_score = max(0, 100 - _CONGESTION_SCORE.get(dominant, 1) * 35)
    return {
        "zone_id": str(zone.id),
        "zone_name": zone.name,
        "dominant_congestion": dominant,
        "avg_speed_kmh": round(avg_speed, 1) if avg_speed else None,
        "health_score": health_score,
        "monitored_locations": locations,
        "location_count": len(locations),
        "threshold": zone.congestion_threshold,
        "threshold_breached": _CONGESTION_SCORE.get(dominant, 0) >= _CONGESTION_SCORE.get(zone.congestion_threshold, 2),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{zone_id}/alerts", status_code=status.HTTP_200_OK)
def zone_alert_history(
    zone_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Alert history for a zone (last 50 alerts)."""
    zone = _get_zone_or_404(zone_id, current_user.id, db)
    alerts = (
        db.query(ZoneAlert)
        .filter(ZoneAlert.zone_id == zone_id)
        .order_by(ZoneAlert.triggered_at.desc())
        .limit(50)
        .all()
    )
    return {
        "zone_id": str(zone.id),
        "zone_name": zone.name,
        "alerts": [
            {
                "id": str(a.id),
                "triggered_at": a.triggered_at.isoformat(),
                "congestion_level": a.congestion_level,
                "avg_speed_kmh": a.avg_speed_kmh,
                "affected_locations": json.loads(a.affected_locations) if a.affected_locations else [],
            }
            for a in alerts
        ],
        "total": len(alerts),
    }


# ── Zone traffic query (reused by background monitor) ─────────────────────────

def _query_zone_traffic(zone: GeofenceZone, db: Session):
    from app.models.predictor import TrafficRecord
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    if zone.zone_type == "rectangle":
        records = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.latitude.between(zone.lat_min, zone.lat_max),
                TrafficRecord.longitude.between(zone.lng_min, zone.lng_max),
                TrafficRecord.created_at >= since,
            )
            .order_by(TrafficRecord.location, TrafficRecord.created_at.desc())
            .all()
        )
    else:
        # Circle: approximate using bounding box, then filter by haversine
        deg_per_km_lat = 1 / 111.0
        deg_per_km_lng = 1 / (111.0 * math.cos(math.radians(zone.center_lat)))
        lat_delta = zone.radius_km * deg_per_km_lat
        lng_delta = zone.radius_km * deg_per_km_lng
        candidates = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.latitude.between(zone.center_lat - lat_delta, zone.center_lat + lat_delta),
                TrafficRecord.longitude.between(zone.center_lng - lng_delta, zone.center_lng + lng_delta),
                TrafficRecord.created_at >= since,
            )
            .all()
        )
        records = [r for r in candidates if _haversine(zone.center_lat, zone.center_lng, r.latitude, r.longitude) <= zone.radius_km]

    # Deduplicate: one record per location (latest)
    seen = {}
    for r in records:
        if r.location not in seen:
            seen[r.location] = r
    unique = list(seen.values())

    if not unique:
        return [], None, "low"

    speeds = [r.average_speed for r in unique if r.average_speed is not None]
    avg_speed = sum(speeds) / len(speeds) if speeds else None
    levels = [r.congestion_level for r in unique if r.congestion_level]
    # Use mode (most frequent) not worst-case
    dominant = max(set(levels), key=levels.count) if levels else "low"
    location_list = [
        {"name": r.location, "congestion": r.congestion_level, "speed_kmh": r.average_speed}
        for r in unique
    ]
    return location_list, avg_speed, dominant


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _get_zone_or_404(zone_id, user_id, db) -> GeofenceZone:
    z = db.query(GeofenceZone).filter(
        GeofenceZone.id == zone_id, GeofenceZone.user_id == user_id, GeofenceZone.is_active == True
    ).first()
    if not z:
        raise HTTPException(status_code=404, detail="Zone not found")
    return z


def _display_radius(zone: GeofenceZone) -> float:
    """Approximate radius in km for display — exact for circles, diagonal/2 for rectangles."""
    if zone.zone_type == "circle" and zone.radius_km:
        return round(zone.radius_km, 1)
    if zone.lat_min is not None and zone.lat_max is not None:
        lat_km = (zone.lat_max - zone.lat_min) * 111.0
        cos_lat = math.cos(math.radians((zone.lat_min + zone.lat_max) / 2))
        lng_km  = (zone.lng_max - zone.lng_min) * 111.0 * cos_lat
        return round(math.sqrt(lat_km ** 2 + lng_km ** 2) / 2, 1)
    return 0.0


def _zone_dict(zone: GeofenceZone, city: str = "") -> dict:
    shape = "polygon" if zone.zone_type == "rectangle" else zone.zone_type
    d = {
        "id":                   str(zone.id),
        "name":                 zone.name,
        "city":                 city or getattr(zone, "_city", ""),
        "zone_type":            zone.zone_type,
        "shape_label":          shape,
        "radius_km":            _display_radius(zone),
        "congestion_threshold": zone.congestion_threshold,
        "threshold_pct":        _THRESHOLD_PCT.get(zone.congestion_threshold, 60),
        "is_active":            zone.is_active,
        "created_at":           zone.created_at.isoformat(),
    }
    if zone.zone_type == "rectangle":
        d.update({"lat_min": zone.lat_min, "lat_max": zone.lat_max,
                  "lng_min": zone.lng_min, "lng_max": zone.lng_max})
    else:
        d.update({"center_lat": zone.center_lat, "center_lng": zone.center_lng,
                  "radius_km": zone.radius_km})
    return d
