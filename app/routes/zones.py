"""Smart geofence zone endpoints."""

import json
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.zone import GeofenceZone, ZoneAlert
from app.models.user import User
from app.services.auth_service import get_current_user
from app.utils.api_response import to_ist_iso

router = APIRouter(prefix="/zones", tags=["Geofence Zones"])
logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}
_THRESHOLD_PCT = {"low": 40, "medium": 60, "high": 85}
_HEALTH_BY_LEVEL = {"low": 100, "medium": 65, "high": 30}
_TRAFFIC_LOOKBACK = timedelta(hours=6)
_ALERT_DEBOUNCE = timedelta(minutes=15)

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
        "city": "Bangalore",
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

_CITY_BY_DEMO_NAME = {z["name"]: z["city"] for z in _DEMO_ZONES}


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _ist_today_start() -> datetime:
    """Start of today in Asia/Kolkata, as UTC-aware datetime for DB filters."""
    now_ist = datetime.now(_IST)
    start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_ist.astimezone(timezone.utc)


def _ts(dt: Optional[datetime]) -> Optional[str]:
    return to_ist_iso(dt) if dt else None


def _time_label(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    local = _aware(dt).astimezone(_IST)
    return local.strftime("%I:%M %p").lstrip("0").lower()


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


def _infer_city(zone: GeofenceZone) -> str:
    if zone.name in _CITY_BY_DEMO_NAME:
        return _CITY_BY_DEMO_NAME[zone.name]
    # Rough city from center / bounds midpoint
    lat = zone.center_lat
    lng = zone.center_lng
    if lat is None and zone.lat_min is not None and zone.lat_max is not None:
        lat = (zone.lat_min + zone.lat_max) / 2
        lng = (zone.lng_min + zone.lng_max) / 2
    if lat is None or lng is None:
        return ""
    cities = [
        ("Hyderabad", 17.3850, 78.4867),
        ("Bangalore", 12.9716, 77.5946),
        ("Mumbai", 19.0760, 72.8777),
        ("Delhi", 28.7041, 77.1025),
        ("Chennai", 13.0827, 80.2707),
        ("Kolkata", 22.5726, 88.3639),
        ("Pune", 18.5204, 73.8567),
    ]
    best, best_d = "", 1e9
    for name, clat, clng in cities:
        d = _haversine(lat, lng, clat, clng)
        if d < best_d:
            best_d, best = d, name
    return best if best_d < 80 else ""


class ZoneCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    zone_type: str = Field("rectangle", pattern="^(rectangle|circle)$")
    org_id: Optional[uuid.UUID] = None
    city: Optional[str] = Field(None, max_length=80)
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


def _maybe_fire_breach(
    zone: GeofenceZone,
    dominant: str,
    locations: list,
    avg_speed: Optional[float],
    db: Session,
    now: datetime,
) -> bool:
    """Create a ZoneAlert if threshold is breached and debounce allows. Returns True if fired."""
    if dominant in (None, "unknown", ""):
        return False
    if _CONGESTION_SCORE.get(dominant, 0) < _CONGESTION_SCORE.get(zone.congestion_threshold, 2):
        return False

    last = (
        db.query(ZoneAlert)
        .filter(ZoneAlert.zone_id == zone.id)
        .order_by(ZoneAlert.triggered_at.desc())
        .first()
    )
    if last:
        last_at = _aware(last.triggered_at)
        if last_at and (now - last_at).total_seconds() < _ALERT_DEBOUNCE.total_seconds():
            return False

    db.add(ZoneAlert(
        zone_id=zone.id,
        triggered_at=now.replace(tzinfo=None) if now.tzinfo else now,
        congestion_level=dominant,
        affected_locations=json.dumps([l["name"] for l in locations[:3]]),
        avg_speed_kmh=round(avg_speed, 1) if avg_speed else None,
    ))
    db.commit()
    return True


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
    city = payload.city or _infer_city(zone)
    return _zone_dict(zone, city=city)


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

    today_start = _ist_today_start()
    zone_ids = [z.id for z in zones]

    breaches_today = 0
    if zone_ids:
        breaches_today = (
            db.query(ZoneAlert)
            .filter(ZoneAlert.zone_id.in_(zone_ids), ZoneAlert.triggered_at >= today_start.replace(tzinfo=None))
            .count()
        )

    cities = len({_infer_city(z) or z.name for z in zones})

    return {
        "total_zones":    len(zones),
        "active_zones":   sum(1 for z in zones if z.is_active),
        "breaches_today": breaches_today,
        "cities":         cities,
        "updated_at":     to_ist_iso(),
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

    zone_ids = [z.id for z in zones]
    zone_name = {z.id: z.name for z in zones}

    alerts = []
    if zone_ids:
        alerts = (
            db.query(ZoneAlert)
            .filter(ZoneAlert.zone_id.in_(zone_ids))
            .order_by(ZoneAlert.triggered_at.desc())
            .limit(limit)
            .all()
        )

    def _fmt(a: ZoneAlert) -> dict:
        zname = zone_name.get(a.zone_id, "Unknown Zone")
        try:
            locs = json.loads(a.affected_locations) if a.affected_locations else [zname]
        except Exception:
            locs = [zname]
        loc_str = ", ".join(locs[:2]) if locs else zname
        level_label = {"low": "Low", "medium": "Moderate", "high": "High"}.get(
            a.congestion_level, a.congestion_level or "Moderate"
        )
        action = "approaching threshold" if a.congestion_level != "high" else "threshold breached"
        return {
            "id":               str(a.id),
            "zone_id":          str(a.zone_id),
            "zone_name":        zname,
            "message":          f"{level_label} congestion at {loc_str} — {action}",
            "congestion_level": a.congestion_level,
            "avg_speed_kmh":    a.avg_speed_kmh,
            "triggered_at":     _ts(a.triggered_at),
            "time_label":       _time_label(a.triggered_at),
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

    now = datetime.now(timezone.utc)
    today_start = _ist_today_start().replace(tzinfo=None)

    result = []
    for zone in zones:
        locations, avg_speed, dominant, has_data = _query_zone_traffic(zone, db)

        breaches_today = (
            db.query(ZoneAlert)
            .filter(ZoneAlert.zone_id == zone.id, ZoneAlert.triggered_at >= today_start)
            .count()
        )

        if has_data and _maybe_fire_breach(zone, dominant, locations, avg_speed, db, now):
            breaches_today += 1

        health = _HEALTH_BY_LEVEL.get(dominant) if has_data else None
        d = _zone_dict(zone, city=_infer_city(zone))
        d.update({
            "current_congestion":  dominant,
            "health_score":        health,
            "has_data":            has_data,
            "breaches_today":      breaches_today,
            "avg_speed_kmh":       round(avg_speed, 1) if avg_speed is not None else None,
            "monitored_locations": locations,
            "threshold_breached":  (
                has_data
                and _CONGESTION_SCORE.get(dominant, 0)
                >= _CONGESTION_SCORE.get(zone.congestion_threshold, 2)
            ),
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
    zone = _get_zone_or_404(zone_id, current_user.id, db)
    locations, avg_speed, dominant, has_data = _query_zone_traffic(zone, db)
    health = _HEALTH_BY_LEVEL.get(dominant) if has_data else None
    d = _zone_dict(zone, city=_infer_city(zone))
    d.update({
        "current_congestion":  dominant,
        "health_score":        health,
        "has_data":            has_data,
        "avg_speed_kmh":       round(avg_speed, 1) if avg_speed is not None else None,
        "monitored_locations": locations,
        "threshold_breached":  (
            has_data
            and _CONGESTION_SCORE.get(dominant, 0)
            >= _CONGESTION_SCORE.get(zone.congestion_threshold, 2)
        ),
    })
    return d


@router.delete("/{zone_id}", status_code=status.HTTP_200_OK)
def delete_zone(
    zone_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Soft-delete a geofence zone (removed from active list)."""
    zone = _get_zone_or_404(zone_id, current_user.id, db)
    zone.is_active = False
    db.commit()
    return {"message": f"Zone '{zone.name}' deleted", "id": str(zone.id)}


@router.get("/{zone_id}/status", status_code=status.HTTP_200_OK)
def zone_status(
    zone_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Live congestion status for all monitored locations inside the zone."""
    zone = _get_zone_or_404(zone_id, current_user.id, db)
    locations, avg_speed, dominant, has_data = _query_zone_traffic(zone, db)
    health_score = None
    if has_data:
        health_score = max(0, 100 - _CONGESTION_SCORE.get(dominant, 1) * 35)
    return {
        "zone_id": str(zone.id),
        "zone_name": zone.name,
        "dominant_congestion": dominant,
        "has_data": has_data,
        "avg_speed_kmh": round(avg_speed, 1) if avg_speed is not None else None,
        "health_score": health_score,
        "monitored_locations": locations,
        "location_count": len(locations),
        "threshold": zone.congestion_threshold,
        "threshold_breached": (
            has_data
            and _CONGESTION_SCORE.get(dominant, 0)
            >= _CONGESTION_SCORE.get(zone.congestion_threshold, 2)
        ),
        "evaluated_at": to_ist_iso(),
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
                "triggered_at": _ts(a.triggered_at),
                "time_label": _time_label(a.triggered_at),
                "congestion_level": a.congestion_level,
                "avg_speed_kmh": a.avg_speed_kmh,
                "affected_locations": (
                    json.loads(a.affected_locations) if a.affected_locations else []
                ),
            }
            for a in alerts
        ],
        "total": len(alerts),
    }


# ── Zone traffic query (reused by background monitor) ─────────────────────────

def _congestion_from_speed(speed_kmh: Optional[float]) -> Optional[str]:
    """Map absolute speed to congestion (aligned with realtime.SPEED_THRESHOLDS)."""
    if speed_kmh is None:
        return None
    if speed_kmh <= 25:
        return "high"
    if speed_kmh <= 60:
        return "medium"
    return "low"


def _effective_congestion(stored: Optional[str], speed_kmh: Optional[float]) -> str:
    """Prefer the worse of stored label vs speed — blocks 'low' at 17 km/h."""
    stored_lvl = stored if stored in ("low", "medium", "high") else None
    speed_lvl = _congestion_from_speed(speed_kmh)
    if stored_lvl and speed_lvl:
        if _CONGESTION_SCORE[speed_lvl] > _CONGESTION_SCORE[stored_lvl]:
            return speed_lvl
        return stored_lvl
    return stored_lvl or speed_lvl or "unknown"


def _query_zone_traffic(zone: GeofenceZone, db: Session):
    """Return (locations, avg_speed, dominant_congestion, has_data)."""
    from app.models.predictor import TrafficRecord

    since = datetime.now(timezone.utc) - _TRAFFIC_LOOKBACK
    since_naive = since.replace(tzinfo=None)
    time_filter = or_(
        TrafficRecord.timestamp >= since,
        TrafficRecord.created_at >= since_naive,
        TrafficRecord.created_at >= since,
    )

    if zone.zone_type == "rectangle":
        records = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.latitude.between(zone.lat_min, zone.lat_max),
                TrafficRecord.longitude.between(zone.lng_min, zone.lng_max),
                time_filter,
            )
            .order_by(TrafficRecord.created_at.desc())
            .all()
        )
    else:
        deg_per_km_lat = 1 / 111.0
        cos_lat = math.cos(math.radians(zone.center_lat or 0))
        deg_per_km_lng = 1 / (111.0 * cos_lat) if cos_lat else 1 / 111.0
        lat_delta = zone.radius_km * deg_per_km_lat
        lng_delta = zone.radius_km * deg_per_km_lng
        candidates = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.latitude.between(zone.center_lat - lat_delta, zone.center_lat + lat_delta),
                TrafficRecord.longitude.between(zone.center_lng - lng_delta, zone.center_lng + lng_delta),
                time_filter,
            )
            .all()
        )
        records = [
            r for r in candidates
            if r.latitude is not None and r.longitude is not None
            and _haversine(zone.center_lat, zone.center_lng, r.latitude, r.longitude) <= zone.radius_km
        ]

    # Deduplicate: one record per location (latest)
    seen: dict = {}
    for r in records:
        if r.location not in seen:
            seen[r.location] = r
    unique = list(seen.values())

    if not unique:
        return [], None, "unknown", False

    speeds = [r.average_speed for r in unique if r.average_speed is not None]
    avg_speed = sum(speeds) / len(speeds) if speeds else None

    location_list = []
    levels = []
    for r in unique:
        level = _effective_congestion(r.congestion_level, r.average_speed)
        if level != "unknown":
            levels.append(level)
        location_list.append({
            "name": r.location,
            "congestion": level,
            "stored_congestion": r.congestion_level,
            "speed_kmh": round(r.average_speed, 1) if r.average_speed is not None else None,
        })

    # Zone dominant: worst observed location (safer for alerts) with mode as tie-break
    if not levels:
        return location_list, avg_speed, "unknown", False

    # Worst location wins (safer for threshold alerts)
    dominant = max(levels, key=lambda lvl: _CONGESTION_SCORE.get(lvl, 0))
    zone_from_avg = _congestion_from_speed(avg_speed)
    if zone_from_avg and _CONGESTION_SCORE[zone_from_avg] > _CONGESTION_SCORE[dominant]:
        dominant = zone_from_avg

    return location_list, avg_speed, dominant, True


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def _get_zone_or_404(zone_id, user_id, db) -> GeofenceZone:
    z = db.query(GeofenceZone).filter(
        GeofenceZone.id == zone_id,
        GeofenceZone.user_id == user_id,
        GeofenceZone.is_active == True,
    ).first()
    if not z:
        raise HTTPException(status_code=404, detail="Zone not found")
    return z


def _display_radius(zone: GeofenceZone) -> float:
    """Approximate radius in km for display — exact for circles, diagonal/2 for rectangles."""
    if zone.zone_type == "circle" and zone.radius_km:
        return round(zone.radius_km, 1)
    if zone.lat_min is not None and zone.lat_max is not None and zone.lng_min is not None and zone.lng_max is not None:
        lat_km = (zone.lat_max - zone.lat_min) * 111.0
        cos_lat = math.cos(math.radians((zone.lat_min + zone.lat_max) / 2))
        lng_km = (zone.lng_max - zone.lng_min) * 111.0 * cos_lat
        return round(math.sqrt(lat_km ** 2 + lng_km ** 2) / 2, 1)
    return 0.0


def _zone_dict(zone: GeofenceZone, city: str = "") -> dict:
    shape = "polygon" if zone.zone_type == "rectangle" else zone.zone_type
    d = {
        "id":                   str(zone.id),
        "name":                 zone.name,
        "city":                 city,
        "zone_type":            zone.zone_type,
        "shape_label":          shape,
        "radius_km":            _display_radius(zone),
        "congestion_threshold": zone.congestion_threshold,
        "threshold_pct":        _THRESHOLD_PCT.get(zone.congestion_threshold, 60),
        "is_active":            zone.is_active,
        "created_at":           _ts(zone.created_at),
    }
    if zone.zone_type == "rectangle":
        d.update({
            "lat_min": zone.lat_min, "lat_max": zone.lat_max,
            "lng_min": zone.lng_min, "lng_max": zone.lng_max,
        })
    else:
        d.update({
            "center_lat": zone.center_lat,
            "center_lng": zone.center_lng,
            "radius_km": zone.radius_km,
        })
    return d
