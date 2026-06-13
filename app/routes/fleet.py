"""Fleet vehicle management endpoints."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.fleet import FleetAssignment, FleetVehicle
from app.models.driver_behavior import DriverBehaviorLog, DriverDailyScore
from app.models.org import OrgMembership
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.eta_service import calculate_eta_for_location
from app.services.behavior_service import compute_daily_score, score_summary_for_vehicle

router = APIRouter(prefix="/fleet", tags=["Fleet Management"])
logger = logging.getLogger(__name__)

_ROLE_ORDER = {"member": 0, "admin": 1, "owner": 2}


def _resolve_org_uuid(org_id_str: str, user: User, db: Session) -> uuid.UUID:
    """Accept a UUID string or any non-UUID placeholder (e.g. 'org-001').

    When a non-UUID is received, falls back to the user's first org membership
    so the frontend can call fleet endpoints before it knows the real UUID.
    """
    try:
        return uuid.UUID(org_id_str)
    except ValueError:
        membership = (
            db.query(OrgMembership)
            .filter(OrgMembership.user_id == user.id)
            .first()
        )
        if membership:
            return membership.org_id
        raise HTTPException(
            status_code=404,
            detail="No organization found for this user. Create one at POST /api/v1/org",
        )


def _check_membership(org_id: uuid.UUID, user: User, min_role: str, db: Session) -> OrgMembership:
    m = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id, OrgMembership.user_id == user.id
    ).first()
    if not m:
        raise HTTPException(status_code=403, detail="Not a member of this organization")
    if _ROLE_ORDER.get(m.role, -1) < _ROLE_ORDER.get(min_role, 0):
        raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")
    return m


class VehicleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    registration: Optional[str] = Field(None, max_length=20)
    vehicle_type: str = Field("car", pattern="^(car|truck|bike|bus|van)$")


class VehicleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    registration: Optional[str] = Field(None, max_length=20)
    vehicle_type: Optional[str] = Field(None, pattern="^(car|truck|bike|bus|van)$")


class AssignRequest(BaseModel):
    driver_id: uuid.UUID


@router.post("/{org_id}/vehicles", status_code=status.HTTP_201_CREATED)
def create_vehicle(
    org_id: str,
    payload: VehicleCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Register a new vehicle in the fleet (admin+)."""
    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "admin", db)
    vehicle = FleetVehicle(
        org_id=org_id,
        name=payload.name,
        registration=payload.registration,
        vehicle_type=payload.vehicle_type,
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    logger.info("Vehicle '%s' registered in org %s", vehicle.name, org_id)
    return _vehicle_dict(vehicle, None, None)


@router.get("/{org_id}/vehicles", status_code=status.HTTP_200_OK)
def list_vehicles(
    org_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """List all active vehicles in the fleet."""
    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "member", db)
    vehicles = db.query(FleetVehicle).filter(
        FleetVehicle.org_id == org_id, FleetVehicle.is_active == True
    ).all()
    results = []
    for v in vehicles:
        assignment = db.query(FleetAssignment).filter(
            FleetAssignment.vehicle_id == v.id, FleetAssignment.is_current == True
        ).first()
        driver = _get_driver(assignment, db)
        results.append(_vehicle_dict(v, assignment, driver))
    return {"vehicles": results, "total": len(results)}


@router.get("/{org_id}/vehicles/{vehicle_id}", status_code=status.HTTP_200_OK)
def get_vehicle(
    org_id: str,
    vehicle_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Get vehicle detail with current driver."""
    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "member", db)
    vehicle = _get_vehicle_or_404(vehicle_id, org_id, db)
    assignment = db.query(FleetAssignment).filter(
        FleetAssignment.vehicle_id == vehicle_id, FleetAssignment.is_current == True
    ).first()
    driver = _get_driver(assignment, db)
    return _vehicle_dict(vehicle, assignment, driver)


@router.put("/{org_id}/vehicles/{vehicle_id}", status_code=status.HTTP_200_OK)
def update_vehicle(
    org_id: str,
    vehicle_id: uuid.UUID,
    payload: VehicleUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Update vehicle info (admin+)."""
    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "admin", db)
    vehicle = _get_vehicle_or_404(vehicle_id, org_id, db)
    if payload.name is not None:
        vehicle.name = payload.name
    if payload.registration is not None:
        vehicle.registration = payload.registration
    if payload.vehicle_type is not None:
        vehicle.vehicle_type = payload.vehicle_type
    db.commit()
    db.refresh(vehicle)
    return _vehicle_dict(vehicle, None, None)


@router.delete("/{org_id}/vehicles/{vehicle_id}", status_code=status.HTTP_200_OK)
def deactivate_vehicle(
    org_id: str,
    vehicle_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Deactivate (soft-delete) a vehicle (admin+)."""
    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "admin", db)
    vehicle = _get_vehicle_or_404(vehicle_id, org_id, db)
    vehicle.is_active = False
    db.commit()
    return {"message": f"Vehicle '{vehicle.name}' deactivated"}


@router.post("/{org_id}/vehicles/{vehicle_id}/assign", status_code=status.HTTP_200_OK)
def assign_driver(
    org_id: str,
    vehicle_id: uuid.UUID,
    payload: AssignRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Assign a driver to a vehicle (admin+). Unassigns any current driver first."""
    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "admin", db)
    _get_vehicle_or_404(vehicle_id, org_id, db)
    # Unassign current driver
    current = db.query(FleetAssignment).filter(
        FleetAssignment.vehicle_id == vehicle_id, FleetAssignment.is_current == True
    ).first()
    if current:
        current.is_current = False
        current.unassigned_at = datetime.now(timezone.utc)
    assignment = FleetAssignment(vehicle_id=vehicle_id, driver_id=payload.driver_id)
    db.add(assignment)
    db.commit()
    return {"message": "Driver assigned", "driver_id": str(payload.driver_id), "vehicle_id": str(vehicle_id)}


@router.delete("/{org_id}/vehicles/{vehicle_id}/assign", status_code=status.HTTP_200_OK)
def unassign_driver(
    org_id: str,
    vehicle_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Remove the current driver assignment (admin+)."""
    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "admin", db)
    _get_vehicle_or_404(vehicle_id, org_id, db)
    current = db.query(FleetAssignment).filter(
        FleetAssignment.vehicle_id == vehicle_id, FleetAssignment.is_current == True
    ).first()
    if not current:
        raise HTTPException(status_code=404, detail="No active driver assignment")
    current.is_current = False
    current.unassigned_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Driver unassigned"}


_DEMO_VEHICLES = [
    {"name": "FleetCast Car 01",   "registration": "MH-12-AB-1234", "vehicle_type": "car",   "driver_name": "Arjun Sharma",  "driver_email": "arjun.driver@flowcast.local"},
    {"name": "FleetCast Car 02",   "registration": "KA-09-CD-5678", "vehicle_type": "car",   "driver_name": "Priya Patel",   "driver_email": "priya.driver@flowcast.local"},
    {"name": "FleetCast Truck 03", "registration": "DL-01-EF-9012", "vehicle_type": "truck", "driver_name": "Rahul Verma",   "driver_email": "rahul.driver@flowcast.local"},
    {"name": "FleetCast Van 04",   "registration": "TN-22-GH-3456", "vehicle_type": "van",   "driver_name": "Sunita Devi",   "driver_email": "sunita.driver@flowcast.local"},
    {"name": "FleetCast Bike 05",  "registration": "GJ-01-IJ-7890", "vehicle_type": "bike",  "driver_name": "Vikram Singh",  "driver_email": "vikram.driver@flowcast.local"},
]


def _seed_demo_vehicles(org_id: uuid.UUID, db: Session) -> list:
    """Idempotent: seed demo vehicles, driver users, and assignments.

    Each vehicle is wrapped in a savepoint so one failure doesn't roll back the others.
    Safe to call on every request — first() guards prevent duplicates.
    """
    from app.models.user import User as UserModel

    for v in _DEMO_VEHICLES:
        try:
            with db.begin_nested():   # SAVEPOINT per vehicle
                # 1. Find or create driver user
                driver = db.query(UserModel).filter(UserModel.email == v["driver_email"]).first()
                if not driver:
                    driver = UserModel(
                        email=v["driver_email"],
                        full_name=v["driver_name"],
                        # "!" is an impossible bcrypt hash — account can never log in
                        hashed_password="!",
                        auth_provider="local",
                        is_active=False,
                        is_verified=False,
                    )
                    db.add(driver)
                    db.flush()

                # 2. Find or create vehicle
                vehicle = db.query(FleetVehicle).filter(
                    FleetVehicle.org_id == org_id, FleetVehicle.name == v["name"]
                ).first()
                if not vehicle:
                    vehicle = FleetVehicle(
                        org_id=org_id,
                        name=v["name"],
                        registration=v["registration"],
                        vehicle_type=v["vehicle_type"],
                    )
                    db.add(vehicle)
                    db.flush()

                # 3. Create assignment only if none exists
                assignment = db.query(FleetAssignment).filter(
                    FleetAssignment.vehicle_id == vehicle.id,
                    FleetAssignment.is_current.is_(True),
                ).first()
                if not assignment:
                    db.add(FleetAssignment(
                        vehicle_id=vehicle.id,
                        driver_id=driver.id,
                        is_current=True,
                    ))

        except Exception:
            logger.warning("Demo seed savepoint failed for vehicle '%s'", v.get("name"), exc_info=True)

    try:
        db.commit()
    except Exception:
        logger.error("Demo seed commit failed", exc_info=True)
        db.rollback()

    # Expire all cached objects so subsequent queries see the freshly committed rows
    db.expire_all()
    return db.query(FleetVehicle).filter(
        FleetVehicle.org_id == org_id, FleetVehicle.is_active.is_(True)
    ).all()


@router.get("/{org_id}/live", status_code=status.HTTP_200_OK)
def fleet_live_status(
    org_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Live status for every vehicle — current road congestion based on live traffic data."""
    from app.models.predictor import TrafficRecord
    from app.services.car_simulator import car_simulator

    org_id = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(org_id, current_user, "member", db)

    vehicles = db.query(FleetVehicle).filter(
        FleetVehicle.org_id == org_id, FleetVehicle.is_active == True
    ).all()

    _demo_names = {v["name"] for v in _DEMO_VEHICLES}
    _is_demo_fleet = not vehicles or all(v.name in _demo_names for v in vehicles)
    if _is_demo_fleet:
        vehicles = _seed_demo_vehicles(org_id, db)

    # Build a pool of live location snapshots from the car simulator
    snapshot = car_simulator.get_snapshot() if car_simulator._initialized else []
    # Fall back to recent TrafficRecords when simulator hasn't started yet
    if not snapshot:
        records = (
            db.query(TrafficRecord)
            .order_by(TrafficRecord.created_at.desc())
            .limit(50)
            .all()
        )
        snapshot = [
            {
                "location": r.location,
                "congestion_level": r.congestion_level or "medium",
                "speed_kmh": r.average_speed or 35.0,
                "lat": r.latitude,
                "lng": r.longitude,
            }
            for r in records if r.location
        ]

    # Build a name lookup from the demo list so we can fall back when assignments
    # are missing (e.g., first request before the seeder commit lands).
    _demo_driver_by_vehicle = {d["name"]: d["driver_name"] for d in _DEMO_VEHICLES}

    live = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for idx, v in enumerate(vehicles):
        assignment = db.query(FleetAssignment).filter(
            FleetAssignment.vehicle_id == v.id, FleetAssignment.is_current.is_(True)
        ).first()
        driver = _get_driver(assignment, db)

        # Resolve driver name: DB assignment → demo fallback → None
        driver_name = (
            driver.full_name
            if driver
            else _demo_driver_by_vehicle.get(v.name)
        )
        driver_id = str(assignment.driver_id) if (assignment and assignment.driver_id) else None

        # Each vehicle gets a different snapshot entry (cycle through pool)
        snap = snapshot[idx % len(snapshot)] if snapshot else {}
        location_name = snap.get("location", "India")
        congestion = snap.get("congestion_level", "medium")
        speed = snap.get("speed_kmh") or snap.get("average_speed")
        lat = snap.get("lat")
        lng = snap.get("lng")

        live.append({
            "vehicle_id": str(v.id),
            "vehicle_name": v.name,
            "vehicle_type": v.vehicle_type,
            "registration": v.registration,
            "driver_name": driver_name,
            "driver_id": driver_id,
            "location": location_name,
            "latitude": round(lat, 6) if lat else None,
            "longitude": round(lng, 6) if lng else None,
            "congestion_level": congestion,
            "speed_kmh": round(float(speed), 1) if speed else None,
            "last_seen": now_iso,
        })

    return {"org_id": str(org_id), "vehicles": live, "total": len(live)}


@router.get("/{org_id}/ai-insights", status_code=status.HTTP_200_OK)
def fleet_ai_insights(
    org_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """AI-generated fleet performance insights driven by live traffic data.

    Auto-creates an organization for the user if none exists.
    Generates real insights from current traffic conditions, peak-hour patterns,
    congestion hotspots, and active incidents — with or without registered vehicles.
    """
    from collections import Counter
    from datetime import timedelta
    from app.models.org import Organization, OrgMembership as _OrgMembership
    from app.models.predictor import TrafficRecord, Incident
    from app.services.ai_service import generate_fleet_insights

    now = datetime.now(timezone.utc)

    # ── Step 1: resolve or auto-create org ───────────────────────────────────
    resolved_id: uuid.UUID | None = None
    org_name = "My Fleet"

    try:
        resolved_id = uuid.UUID(org_id)
        org = db.query(Organization).filter(Organization.id == resolved_id).first()
        if org:
            org_name = org.name
    except ValueError:
        # Non-UUID (e.g. "demo-org") — find or create the user's org
        membership = (
            db.query(_OrgMembership)
            .filter(_OrgMembership.user_id == current_user.id)
            .first()
        )
        if membership:
            resolved_id = membership.org_id
            org = db.query(Organization).filter(Organization.id == resolved_id).first()
            org_name = org.name if org else "My Fleet"
        else:
            # Auto-create a personal fleet org for this user
            base_name = (current_user.full_name or current_user.email.split("@")[0]).strip()
            org_name = f"{base_name}'s Fleet"
            slug = f"{base_name.lower().replace(' ', '-')}-{str(uuid.uuid4())[:8]}"
            new_org = Organization(
                name=org_name,
                slug=slug,
                plan="free",
                created_by=current_user.id,
            )
            db.add(new_org)
            db.flush()
            db.add(_OrgMembership(
                org_id=new_org.id,
                user_id=current_user.id,
                role="owner",
            ))
            db.commit()
            db.refresh(new_org)
            resolved_id = new_org.id
            logger.info("Auto-created fleet org '%s' for user %s", org_name, current_user.id)

    # ── Step 2: gather vehicle data ───────────────────────────────────────────
    vehicles = db.query(FleetVehicle).filter(
        FleetVehicle.org_id == resolved_id, FleetVehicle.is_active == True
    ).all()

    # ── Step 3: build live traffic intelligence ───────────────────────────────
    since_24h = now - timedelta(hours=24)
    since_1h  = now - timedelta(hours=1)

    # Hotspots: top congested locations in last 24h
    all_records_24h = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.created_at >= since_24h)
        .order_by(TrafficRecord.created_at.desc())
        .limit(500)
        .all()
    )

    location_stats: dict[str, dict] = {}
    for r in all_records_24h:
        loc = r.location or "Unknown"
        if loc not in location_stats:
            location_stats[loc] = {"high": 0, "medium": 0, "low": 0, "speeds": []}
        location_stats[loc][r.congestion_level or "medium"] += 1
        if r.average_speed:
            location_stats[loc]["speeds"].append(r.average_speed)

    # Rank by high-congestion frequency
    hotspots = sorted(
        location_stats.items(),
        key=lambda x: x[1]["high"],
        reverse=True,
    )[:5]

    # Peak hours: which hour of day had most high-congestion records
    hour_counts: Counter = Counter()
    for r in all_records_24h:
        if r.congestion_level == "high" and r.created_at:
            hour_counts[r.created_at.hour] += 1

    peak_hours = [h for h, _ in hour_counts.most_common(3)]

    # Current live snapshot
    live_records = (
        db.query(TrafficRecord)
        .filter(TrafficRecord.created_at >= since_1h)
        .order_by(TrafficRecord.created_at.desc())
        .limit(50)
        .all()
    )
    live_congestion = Counter(r.congestion_level for r in live_records if r.congestion_level)
    live_speeds = [r.average_speed for r in live_records if r.average_speed]
    live_avg_speed = round(sum(live_speeds) / len(live_speeds), 1) if live_speeds else None

    # Active incidents
    active_incidents = (
        db.query(Incident)
        .filter(Incident.is_active.is_(True))
        .limit(10)
        .all()
    )
    incident_locations = [inc.location for inc in active_incidents if inc.location]

    # ── Step 4: build rich context for AI ────────────────────────────────────
    context_lines = [
        f"Organization: {org_name} (id={resolved_id})",
        f"Fleet size: {len(vehicles)} registered vehicles",
        f"Analysis time: {now.strftime('%A %d %b %Y %H:%M')} IST",
        "",
        "=== LIVE TRAFFIC INTELLIGENCE (last 1 hour) ===",
        f"High congestion records: {live_congestion.get('high', 0)}",
        f"Medium congestion records: {live_congestion.get('medium', 0)}",
        f"Low congestion records: {live_congestion.get('low', 0)}",
        f"Network avg speed: {live_avg_speed} km/h" if live_avg_speed else "Speed data unavailable",
        f"Active incidents on roads: {len(active_incidents)}",
    ]

    if incident_locations:
        context_lines.append(f"Incident locations: {', '.join(incident_locations[:5])}")

    context_lines.append("")
    context_lines.append("=== TOP CONGESTION HOTSPOTS (last 24 hours) ===")
    for loc, stats in hotspots:
        avg_spd = round(sum(stats["speeds"]) / len(stats["speeds"]), 1) if stats["speeds"] else "N/A"
        context_lines.append(
            f"  {loc}: high={stats['high']} times, medium={stats['medium']} times, avg_speed={avg_spd} km/h"
        )

    if peak_hours:
        context_lines.append("")
        context_lines.append(f"=== PEAK CONGESTION HOURS (last 24h) ===")
        context_lines.append(f"  Worst hours: {', '.join(f'{h}:00' for h in peak_hours)}")

    if vehicles:
        context_lines.append("")
        context_lines.append("=== REGISTERED FLEET VEHICLES ===")
        for v in vehicles:
            assignment = db.query(FleetAssignment).filter(
                FleetAssignment.vehicle_id == v.id, FleetAssignment.is_current == True
            ).first()
            driver = _get_driver(assignment, db)
            context_lines.append(
                f"  {v.name} ({v.vehicle_type}, {v.registration or 'no plate'}) — "
                f"driver: {driver.full_name if driver else 'unassigned'}"
            )

    fleet_context = "\n".join(context_lines)
    insights = generate_fleet_insights(fleet_context)

    # ── Step 5: if no AI, build data-driven rule-based insights ──────────────
    if not insights or (len(insights) == 1 and insights[0].get("type") == "config"):
        insights = _build_live_insights(
            hotspots, peak_hours, live_congestion, live_avg_speed,
            active_incidents, vehicles, now,
        )

    logger.info(
        "Fleet AI insights: org=%s vehicles=%d hotspots=%d incidents=%d",
        resolved_id, len(vehicles), len(hotspots), len(active_incidents),
    )
    return {
        "org_id": str(resolved_id),
        "org_name": org_name,
        "vehicle_count": len(vehicles),
        "live_summary": {
            "avg_speed_kmh": live_avg_speed,
            "active_incidents": len(active_incidents),
            "congestion_breakdown": {
                "high": live_congestion.get("high", 0),
                "medium": live_congestion.get("medium", 0),
                "low": live_congestion.get("low", 0),
            },
            "peak_hours_today": peak_hours,
            "top_hotspot": hotspots[0][0] if hotspots else None,
        },
        "insights": insights,
        "generated_at": now.isoformat(),
    }


def _build_live_insights(hotspots, peak_hours, live_congestion, avg_speed, incidents, vehicles, now) -> list[dict]:
    """Rule-based insights from live data when Claude is not available."""
    results = []
    high_count = live_congestion.get("high", 0)
    total = sum(live_congestion.values()) or 1

    # Peak hour scheduling insight
    if peak_hours:
        peak_str = ", ".join(f"{h}:00–{h+1}:00" for h in peak_hours[:2])
        vehicle_str = f"your {len(vehicles)} vehicles" if vehicles else "your fleet"
        results.append({
            "type": "scheduling",
            "title": f"Peak traffic: {peak_hours[0]}:00–{peak_hours[0]+1}:00 is worst today",
            "detail": (
                f"The highest congestion in the last 24 hours occurred at {peak_str}. "
                f"Scheduling {vehicle_str} to depart before {peak_hours[0]}:00 or after "
                f"{peak_hours[-1]+1}:00 could cut journey times by 25–40%."
            ),
            "action": f"Shift fleet departure to before {peak_hours[0]}:00 AM or after {peak_hours[-1]+1}:00 PM.",
            "priority": "high",
        })

    # Hotspot avoidance insight
    if hotspots:
        top_loc, top_stats = hotspots[0]
        top_speed = round(sum(top_stats["speeds"]) / len(top_stats["speeds"]), 1) if top_stats["speeds"] else None
        speed_str = f" — avg speed just {top_speed} km/h" if top_speed else ""
        results.append({
            "type": "route_optimization",
            "title": f"Avoid {top_loc} — most congested today",
            "detail": (
                f"{top_loc} recorded high congestion {top_stats['high']} times in the last 24 hours{speed_str}. "
                f"This is the single biggest delay risk in your operating area. "
                f"Routing vehicles around this corridor can save 15–30 min per trip."
            ),
            "action": f"Program alternate routes around {top_loc} in your dispatch system.",
            "priority": "high" if top_stats["high"] > 5 else "medium",
        })

    # Active incidents insight
    if incidents:
        inc_locs = list({inc.location for inc in incidents if inc.location})[:3]
        results.append({
            "type": "driver_behavior",
            "title": f"{len(incidents)} active road incidents — alert drivers",
            "detail": (
                f"There are currently {len(incidents)} active incidents on Indian roads. "
                f"Affected areas include: {', '.join(inc_locs)}. "
                f"Notify drivers operating in these zones to use alternate routes immediately."
            ),
            "action": "Send route alerts to drivers near affected locations.",
            "priority": "high" if len(incidents) > 3 else "medium",
        })

    # Network speed insight
    if avg_speed:
        normal_speed = 45
        pct_below = round((1 - avg_speed / normal_speed) * 100)
        if pct_below > 20:
            results.append({
                "type": "fuel_waste",
                "title": f"Network speed {pct_below}% below normal — fuel costs rising",
                "detail": (
                    f"The average road speed right now is {avg_speed} km/h, which is {pct_below}% "
                    f"below the normal {normal_speed} km/h. Stop-and-go driving at these speeds "
                    f"increases fuel consumption by 20–35% vs free-flow. "
                    f"Delaying non-urgent trips by 1–2 hours could yield significant savings."
                ),
                "action": "Defer non-urgent deliveries until traffic eases — check /commute/forecast.",
                "priority": "medium",
            })

    # No vehicles registered nudge — but with real data
    if not vehicles:
        results.append({
            "type": "scheduling",
            "title": "Register vehicles to unlock personalized insights",
            "detail": (
                f"Your org is active with real-time access to {high_count} high-congestion "
                f"alerts and {len(incidents)} incidents tracked right now. "
                f"Add vehicles and assign drivers to get per-vehicle route compliance, "
                f"fuel cost estimates, and driver departure coaching."
            ),
            "action": "Go to Fleet Management → Add Vehicle to start tracking.",
            "priority": "low",
        })

    return results


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_vehicle_or_404(vehicle_id: uuid.UUID, org_id: uuid.UUID, db: Session) -> FleetVehicle:
    v = db.query(FleetVehicle).filter(
        FleetVehicle.id == vehicle_id, FleetVehicle.org_id == org_id, FleetVehicle.is_active == True
    ).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return v


def _get_driver(assignment: Optional[FleetAssignment], db: Session):
    if assignment and assignment.driver_id:
        from app.models.user import User as UserModel
        return db.query(UserModel).filter(UserModel.id == assignment.driver_id).first()
    return None



def _vehicle_dict(vehicle: FleetVehicle, assignment, driver) -> dict:
    return {
        "id": str(vehicle.id),
        "name": vehicle.name,
        "registration": vehicle.registration,
        "vehicle_type": vehicle.vehicle_type,
        "driver_id": str(assignment.driver_id) if assignment and assignment.driver_id else None,
        "driver_name": driver.full_name if driver else None,
        "assigned_at": assignment.assigned_at.isoformat() if assignment else None,
        "created_at": vehicle.created_at.isoformat(),
    }


# ── Driver Behavior Analytics ─────────────────────────────────────────────────

class BehaviorEventCreate(BaseModel):
    vehicle_id: uuid.UUID
    event_type: str = Field(..., description="speeding | harsh_braking | harsh_acceleration | idle | route_deviation")
    severity:   Optional[str] = Field("medium", description="low | medium | high")
    location:   Optional[str] = None
    speed_kmh:  Optional[float] = None
    limit_kmh:  Optional[float] = None
    details:    Optional[str] = Field(None, max_length=500)
    recorded_at: Optional[datetime] = None


_VALID_EVENTS = {"speeding", "harsh_braking", "harsh_acceleration", "idle", "route_deviation"}
_VALID_SEVERITIES_B = {"low", "medium", "high"}


@router.post("/{org_id}/behavior/log", status_code=status.HTTP_201_CREATED,
             summary="Log a driver behavior event")
def log_behavior_event(
    org_id: str,
    payload: BehaviorEventCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Log a driver behavior event (speeding, harsh braking, idle, etc.).

    Typically called by a telematics system or vehicle SDK.
    Requires at least `member` role in the org.
    """
    resolved_org = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(resolved_org, current_user, "member", db)

    if payload.event_type not in _VALID_EVENTS:
        raise HTTPException(400, detail=f"event_type must be one of {sorted(_VALID_EVENTS)}")
    if payload.severity and payload.severity not in _VALID_SEVERITIES_B:
        raise HTTPException(400, detail=f"severity must be one of {sorted(_VALID_SEVERITIES_B)}")

    log = DriverBehaviorLog(
        vehicle_id=payload.vehicle_id,
        org_id=resolved_org,
        event_type=payload.event_type,
        severity=payload.severity or "medium",
        location=payload.location,
        speed_kmh=payload.speed_kmh,
        limit_kmh=payload.limit_kmh,
        details=payload.details,
        recorded_at=payload.recorded_at or datetime.now(timezone.utc),
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    logger.info("Behavior event logged: %s for vehicle %s", payload.event_type, payload.vehicle_id)
    return {"message": "Event logged", "event_id": str(log.id), "event_type": log.event_type}


@router.get("/{org_id}/behavior/vehicle/{vehicle_id}", status_code=status.HTTP_200_OK,
            summary="Behavior events for a vehicle")
def get_vehicle_behavior(
    org_id: str,
    vehicle_id: uuid.UUID,
    days: int = 7,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """Return behavior event log and score trend for a specific vehicle."""
    resolved_org = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(resolved_org, current_user, "member", db)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    logs = (
        db.query(DriverBehaviorLog)
        .filter(
            DriverBehaviorLog.vehicle_id == vehicle_id,
            DriverBehaviorLog.org_id == resolved_org,
            DriverBehaviorLog.recorded_at >= since,
        )
        .order_by(DriverBehaviorLog.recorded_at.desc())
        .all()
    )

    # Compute today's live score from today's logs
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_logs = [l for l in logs if l.recorded_at >= today_start]
    today_score = compute_daily_score(today_logs)

    score_trend = score_summary_for_vehicle(str(vehicle_id), db, days=days)

    events = [
        {
            "id":           str(l.id),
            "event_type":   l.event_type,
            "severity":     l.severity,
            "location":     l.location,
            "speed_kmh":    l.speed_kmh,
            "limit_kmh":    l.limit_kmh,
            "details":      l.details,
            "recorded_at":  l.recorded_at.isoformat() if l.recorded_at else None,
        }
        for l in logs[:100]
    ]

    return {
        "vehicle_id":    str(vehicle_id),
        "period_days":   days,
        "today_score":   today_score,
        "score_trend":   score_trend,
        "total_events":  len(logs),
        "events":        events,
    }


@router.get("/{org_id}/behavior/leaderboard", status_code=status.HTTP_200_OK,
            summary="Driver score leaderboard for the whole fleet")
def behavior_leaderboard(
    org_id: str,
    days: int = 7,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """
    Return ranked driver scores for all vehicles in the fleet over the last N days.
    Best drivers are listed first (highest score).
    """
    resolved_org = _resolve_org_uuid(org_id, current_user, db)
    _check_membership(resolved_org, current_user, "member", db)

    vehicles = db.query(FleetVehicle).filter(
        FleetVehicle.org_id == resolved_org,
        FleetVehicle.is_active == True,
    ).all()

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []

    for v in vehicles:
        assignment = db.query(FleetAssignment).filter(
            FleetAssignment.vehicle_id == v.id,
            FleetAssignment.is_current == True,
        ).first()
        driver = _get_driver(assignment, db)

        logs = db.query(DriverBehaviorLog).filter(
            DriverBehaviorLog.vehicle_id == v.id,
            DriverBehaviorLog.recorded_at >= since,
        ).all()
        scored = compute_daily_score(logs)

        rows.append({
            "vehicle_id":    str(v.id),
            "vehicle_name":  v.name,
            "driver_name":   driver.full_name if driver else "Unassigned",
            "score":         scored["score"],
            "grade":         scored["grade"],
            "total_events":  scored["total_events"],
            "speeding":      scored["speeding_count"],
            "harsh_braking": scored["harsh_braking_count"],
        })

    rows.sort(key=lambda r: r["score"], reverse=True)
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank

    fleet_avg = round(sum(r["score"] for r in rows) / len(rows), 1) if rows else None

    return {
        "org_id":       str(resolved_org),
        "period_days":  days,
        "fleet_avg_score": fleet_avg,
        "total_vehicles":  len(rows),
        "leaderboard":  rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
