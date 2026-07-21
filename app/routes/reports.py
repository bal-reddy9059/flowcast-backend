"""Traffic reports — on-demand and scheduled."""

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.report import ScheduledReport
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/reports", tags=["Traffic Reports"])
logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")


# ── On-demand reports ─────────────────────────────────────────────────────────

@router.get("/daily-summary", status_code=status.HTTP_200_OK)
def daily_summary(
    location: str = Query(..., min_length=2, description="Location name"),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """24-hour congestion report — hourly breakdown, peak hour, incident count, city health score."""
    from app.models.predictor import TrafficRecord, Incident
    from app.services.city_aliases import location_filter

    now = datetime.now(_IST)
    since = now - timedelta(hours=24)
    records = (
        db.query(TrafficRecord)
        .filter(location_filter(TrafficRecord.location, location), TrafficRecord.created_at >= since)
        .all()
    )
    incidents = (
        db.query(Incident)
        .filter(location_filter(Incident.location, location), Incident.is_active == True)
        .count()
    )

    hourly: dict[int, list] = defaultdict(list)
    for r in records:
        created = r.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        h = created.astimezone(_IST).hour if created else 0
        hourly[h].append(r)

    hourly_breakdown = []
    for h in range(24):
        recs = hourly.get(h, [])
        speeds = [r.average_speed for r in recs if r.average_speed is not None]
        counts = [r.vehicle_count for r in recs if r.vehicle_count is not None]
        levels = [r.congestion_level for r in recs if r.congestion_level]
        dominant = max(set(levels), key=levels.count) if levels else None
        hourly_breakdown.append({
            "hour": h,
            "time_label": f"{h:02d}:00",
            "congestion": dominant,
            "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else None,
            "avg_vehicles": round(sum(counts) / len(counts)) if counts else None,
            "data_points": len(recs),
        })

    _severity = {"low": 0, "medium": 1, "high": 2}
    peak = max(
        (h for h in hourly_breakdown if h["congestion"]),
        key=lambda x: (_severity.get(x["congestion"], 0), x["data_points"]),
        default=None,
    )
    all_speeds = [r.average_speed for r in records if r.average_speed is not None]
    n_records = len(records) or 1
    high_count = sum(1 for r in records if r.congestion_level == "high")
    medium_count = sum(1 for r in records if r.congestion_level == "medium")
    high_pct = high_count / n_records * 100
    medium_pct = medium_count / n_records * 100
    health_score = round(max(0, 100 - high_pct * 0.7 - medium_pct * 0.25), 1)

    return {
        "location": location,
        "report_type": "daily_summary",
        "period": f"{since.strftime('%Y-%m-%d %H:%M')} – {now.strftime('%Y-%m-%d %H:%M')} IST",
        "total_records": len(records),
        "active_incidents": incidents,
        "avg_speed_kmh": round(sum(all_speeds) / len(all_speeds), 1) if all_speeds else None,
        "health_score": health_score,
        "peak_congestion_hour": peak,
        "hourly_breakdown": hourly_breakdown,
        "generated_at": now.isoformat(),
    }


@router.get("/weekly-trend", status_code=status.HTTP_200_OK)
def weekly_trend(
    location: str = Query(..., min_length=2),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """7-day congestion trend — daily averages with incidents, peak hour, and comparison stats."""
    from collections import defaultdict
    from app.models.predictor import TrafficRecord, Incident
    from app.services.city_aliases import location_filter
    _sev = {"low": 0, "medium": 1, "high": 2}

    def _day_stats(recs: list, incidents_count: int) -> dict:
        speeds = [r.average_speed for r in recs if r.average_speed is not None]
        n = len(recs) or 1
        high_count = sum(1 for r in recs if r.congestion_level == "high")
        med_count  = sum(1 for r in recs if r.congestion_level == "medium")
        # avg_congestion_pct: fraction of records at medium-or-high severity
        avg_cong_pct = round((high_count + med_count) / n * 100, 1)
        high_pct     = round(high_count / n * 100, 1)

        # peak hour = hour whose records have the worst average severity score
        hourly_sev: dict[int, list[int]] = defaultdict(list)
        hourly_veh: dict[int, list[int]] = defaultdict(list)
        for r in recs:
            if r.created_at and r.congestion_level:
                created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
                hourly_sev[created.astimezone(_IST).hour].append(_sev.get(r.congestion_level, 0))
            if r.created_at and r.vehicle_count is not None:
                created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
                hourly_veh[created.astimezone(_IST).hour].append(r.vehicle_count)

        peak_hour: Optional[str] = None
        if hourly_sev:
            best_h = max(hourly_sev, key=lambda h: (
                sum(hourly_sev[h]) / len(hourly_sev[h]),
                sum(hourly_veh.get(h, [0])) / max(len(hourly_veh.get(h, [1])), 1),
            ))
            peak_hour = f"{best_h:02d}:00"

        return {
            "avg_speed_kmh": round(sum(speeds) / len(speeds), 1) if speeds else None,
            "avg_congestion_pct": avg_cong_pct,
            "high_congestion_pct": high_pct,
            "incidents": incidents_count,
            "peak_hour": peak_hour,
            "data_points": len(recs),
        }

    now = datetime.now(_IST)
    week_start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    week_records = db.query(TrafficRecord).filter(
        location_filter(TrafficRecord.location, location),
        TrafficRecord.created_at >= week_start,
    ).all()
    week_incidents = db.query(Incident.reported_at).filter(
        location_filter(Incident.location, location),
        Incident.reported_at >= week_start,
    ).all()

    records_by_day: dict = defaultdict(list)
    for record in week_records:
        created = record.created_at
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            records_by_day[created.astimezone(_IST).date()].append(record)
    incidents_by_day: dict = defaultdict(int)
    for row in week_incidents:
        reported = row.reported_at
        if reported:
            if reported.tzinfo is None:
                reported = reported.replace(tzinfo=timezone.utc)
            incidents_by_day[reported.astimezone(_IST).date()] += 1

    days = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        stats = _day_stats(
            records_by_day.get(day_start.date(), []),
            incidents_by_day.get(day_start.date(), 0),
        )
        days.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "day_label": day_start.strftime("%a"),
            **stats,
        })

    # ── Today's detailed stats ────────────────────────────────────────────────
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_recs = records_by_day.get(today_start.date(), [])

    # peak congestion % in any single hour today
    hourly_high: dict[int, list] = defaultdict(list)
    hourly_vol:  dict[int, list] = defaultdict(list)
    for r in today_recs:
        if r.created_at:
            created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
            hour = created.astimezone(_IST).hour
            hourly_high[hour].append(r.congestion_level)
            if r.vehicle_count is not None:
                hourly_vol[hour].append(r.vehicle_count)

    peak_today_pct = 0.0
    if hourly_high:
        peak_today_pct = max(
            sum(1 for l in lvls if l == "high") / max(len(lvls), 1) * 100
            for lvls in hourly_high.values()
        )

    n_today = len(today_recs) or 1
    avg_today_pct = round(
        sum(1 for r in today_recs if r.congestion_level in ("medium", "high")) / n_today * 100, 1
    )
    total_volume  = sum(r.vehicle_count for r in today_recs if r.vehicle_count is not None)
    incidents_today = incidents_by_day.get(today_start.date(), 0)

    # ── Week average congestion % ─────────────────────────────────────────────
    week_avg_pct = round(
        sum(d["avg_congestion_pct"] for d in days) / max(len(days), 1), 1
    )

    # ── Comparison stats ──────────────────────────────────────────────────────
    today_pct     = days[-1]["avg_congestion_pct"] if days else 0.0
    yesterday_pct = days[-2]["avg_congestion_pct"] if len(days) >= 2 else 0.0
    vs_yesterday  = round(today_pct - yesterday_pct, 1)

    # same weekday last week = 7 days ago
    last_week_start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    last_week_end   = last_week_start + timedelta(days=1)
    lw_recs = db.query(TrafficRecord).filter(
        location_filter(TrafficRecord.location, location),
        TrafficRecord.created_at >= last_week_start,
        TrafficRecord.created_at < last_week_end,
    ).all()
    lw_n = len(lw_recs) or 1
    lw_pct = sum(1 for r in lw_recs if r.congestion_level in ("medium", "high")) / lw_n * 100
    vs_last_week = round(today_pct - lw_pct, 1)

    # ── Peak shift — hour with worst congestion across the full week ──────────
    week_recs = week_records
    week_hourly_sev: dict[int, list] = defaultdict(list)
    for r in week_recs:
        if r.created_at and r.congestion_level:
            created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
            week_hourly_sev[created.astimezone(_IST).hour].append(_sev.get(r.congestion_level, 0))
    peak_shift: Optional[str] = None
    if week_hourly_sev:
        ph = max(week_hourly_sev, key=lambda h: sum(week_hourly_sev[h]) / len(week_hourly_sev[h]))
        peak_shift = f"{ph:02d}:00"

    days_with_data = [day for day in days if day["data_points"] > 0]
    worst_day = max(
        days_with_data,
        key=lambda d: (d["high_congestion_pct"], d["avg_congestion_pct"]),
        default=None,
    )
    best_day = min(
        days_with_data,
        key=lambda d: (d["high_congestion_pct"], d["avg_congestion_pct"]),
        default=None,
    )

    return {
        "location": location,
        "report_type": "weekly_trend",
        "summary": {
            "peak_today_pct": round(peak_today_pct, 1),
            "avg_today_pct": avg_today_pct,
            "total_volume": total_volume,
            "week_avg_pct": week_avg_pct,
        },
        "days": days,
        "worst_day": worst_day,
        "best_day": best_day,
        "vs_yesterday": vs_yesterday,
        "vs_last_week": vs_last_week,
        "peak_shift": peak_shift,
        "incidents_today": incidents_today,
        "generated_at": now.isoformat(),
    }


@router.get("/hotspot-analysis", status_code=status.HTTP_200_OK)
def hotspot_analysis(
    hours: int = Query(168, ge=1, le=720),
    top_n: int = Query(10, ge=1, le=50),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """Worst congestion hotspots across all cities — ranked by combined medium+high congestion %."""
    from app.models.predictor import TrafficRecord, Incident
    from app.services.india_locations import INDIA_LOCATIONS

    # Build location → {city, state} lookup from the master locations list
    _loc_meta: dict[str, dict] = {
        loc["name"]: {"city": loc["city"], "state": loc.get("state", "")}
        for loc in INDIA_LOCATIONS
    }

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = (
        db.query(
            TrafficRecord.location,
            func.count(TrafficRecord.id).label("total_records"),
            func.sum(case((TrafficRecord.congestion_level == "high",   1), else_=0)).label("high_count"),
            func.sum(case((TrafficRecord.congestion_level == "medium", 1), else_=0)).label("med_count"),
            func.avg(TrafficRecord.average_speed).label("avg_speed"),
        )
        .filter(TrafficRecord.created_at >= since, TrafficRecord.congestion_level.isnot(None))
        .group_by(TrafficRecord.location)
        # Sparse collectors may only have a few samples per location. Requiring
        # 24 made this report empty even when a full week contained valid data.
        .having(func.count(TrafficRecord.id) >= 2)
        .order_by(
            # Weight: high=2pts, medium=1pt — gives severity-aware ranking
            (func.sum(case((TrafficRecord.congestion_level == "high",   2), else_=0)) +
             func.sum(case((TrafficRecord.congestion_level == "medium", 1), else_=0))).desc()
        )
        .limit(top_n)
        .all()
    )

    # Batch-fetch incident counts for all returned locations in one query
    locations = [r.location for r in rows]
    inc_rows = (
        db.query(Incident.location, func.count(Incident.id).label("inc_count"))
        .filter(Incident.location.in_(locations), Incident.reported_at >= since)
        .group_by(Incident.location)
        .all()
    ) if locations else []
    inc_map: dict[str, int] = {r.location: int(r.inc_count) for r in inc_rows}

    hotspots = []
    for i, r in enumerate(rows):
        total   = int(r.total_records)
        high    = int(r.high_count or 0)
        med     = int(r.med_count  or 0)
        meta    = _loc_meta.get(r.location, {"city": r.location, "state": ""})
        congestion_pct = round((high + med) / total * 100, 1) if total else 0.0
        hotspots.append({
            "rank": i + 1,
            "location": r.location,
            "city": meta["city"],
            "state": meta["state"],
            "congestion_pct": congestion_pct,
            "high_congestion_pct": round(high / total * 100, 1) if total else 0.0,
            "avg_speed_kmh": round(float(r.avg_speed), 1) if r.avg_speed else None,
            "incidents": high,          # high-congestion records = "congestion incidents"
            "total_records": total,
        })

    return {
        "report_type": "hotspot_analysis",
        "period_hours": hours,
        "hotspots": hotspots,
        "total": len(hotspots),
        "generated_at": datetime.now(_IST).isoformat(),
    }


@router.get("/fleet-overview", status_code=status.HTTP_200_OK)
def fleet_overview(
    days: int = Query(7, ge=1, le=30),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """Fleet performance report — per-vehicle metrics with fuel, efficiency, and comparison stats.

    Auto-resolves (or creates) the user's org and seeds demo vehicles on first call.
    Metrics are computed deterministically from the vehicle id + live traffic speed so
    values are stable across refreshes but reflect real road conditions.
    """
    import hashlib
    import random as _rnd
    from app.models.fleet import FleetVehicle, FleetAssignment
    from app.models.org import Organization, OrgMembership
    from app.models.predictor import TrafficRecord, Incident
    from app.routes.fleet import _seed_demo_vehicles, _DEMO_VEHICLES

    now = datetime.now(_IST)

    # ── Resolve or auto-create the user's org ─────────────────────────────────
    membership = db.query(OrgMembership).filter(OrgMembership.user_id == current_user.id).first()
    if not membership:
        org_name = (current_user.full_name or current_user.email.split("@")[0]).strip()
        new_org = Organization(
            name=f"{org_name}'s Fleet",
            slug=f"{org_name.lower().replace(' ', '-')}-{str(uuid.uuid4())[:8]}",
            plan="free",
            created_by=current_user.id,
        )
        db.add(new_org)
        db.flush()
        db.add(OrgMembership(org_id=new_org.id, user_id=current_user.id, role="owner"))
        db.commit()
        org_id = new_org.id
    else:
        org_id = membership.org_id

    # ── Seed demo vehicles if the org has none ────────────────────────────────
    vehicles = db.query(FleetVehicle).filter(
        FleetVehicle.org_id == org_id, FleetVehicle.is_active == True
    ).all()
    _demo_names = {v["name"] for v in _DEMO_VEHICLES}
    if not vehicles or all(v.name in _demo_names for v in vehicles):
        vehicles = _seed_demo_vehicles(org_id, db)

    # ── Real traffic speed for the period ─────────────────────────────────────
    since = now - timedelta(days=days)
    network_avg_speed = (
        db.query(func.avg(TrafficRecord.average_speed))
        .filter(TrafficRecord.created_at >= since, TrafficRecord.average_speed.isnot(None))
        .scalar()
    )
    network_avg_speed = float(network_avg_speed or 32.0)

    # Today's traffic stats (for header summary)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_stats = (
        db.query(
            func.count(TrafficRecord.id).label("total"),
            func.sum(case((TrafficRecord.congestion_level == "high", 1), else_=0)).label("high"),
            func.sum(case((TrafficRecord.congestion_level == "medium", 1), else_=0)).label("medium"),
            func.sum(TrafficRecord.vehicle_count).label("volume"),
        )
        .filter(TrafficRecord.created_at >= today_start)
        .one()
    )
    n_today = int(today_stats.total or 0) or 1
    high_today = int(today_stats.high or 0)
    med_today = int(today_stats.medium or 0)
    peak_today_pct = round(high_today / n_today * 100, 1)
    avg_today_pct  = round((high_today + med_today) / n_today * 100, 1)
    total_volume = int(today_stats.volume or 0)

    # Week congestion avg (for header)
    week_start = today_start - timedelta(days=6)
    week_stats = (
        db.query(
            func.count(TrafficRecord.id).label("total"),
            func.sum(case((TrafficRecord.congestion_level == "high", 1), else_=0)).label("high"),
            func.sum(case((TrafficRecord.congestion_level == "medium", 1), else_=0)).label("medium"),
        )
        .filter(TrafficRecord.created_at >= week_start, TrafficRecord.congestion_level.isnot(None))
        .one()
    )
    all_n = int(week_stats.total or 0) or 1
    week_high = int(week_stats.high or 0)
    week_med = int(week_stats.medium or 0)
    week_avg_pct = round((week_high + week_med) / all_n * 100, 1)

    # ── Per-vehicle metric computation ────────────────────────────────────────
    _FUEL_BASE = {"car": 0.08, "truck": 0.15, "van": 0.10, "bike": 0.03, "bus": 0.25}
    _TRIPS_DAY = {"car": (4, 7), "truck": (2, 4), "van": (3, 5), "bike": (6, 10), "bus": (8, 12)}
    _KM_TRIP   = {"car": (10, 20), "truck": (30, 50), "van": (15, 25), "bike": (5, 15), "bus": (20, 40)}

    def _compute(v: FleetVehicle, period: int, avg_spd: float) -> dict:
        seed = int(hashlib.md5(str(v.id).encode()).hexdigest()[:8], 16)
        rng  = _rnd.Random(seed)
        vt   = v.vehicle_type or "car"

        t_min, t_max = _TRIPS_DAY.get(vt, (3, 5))
        k_min, k_max = _KM_TRIP.get(vt, (10, 20))
        base_fuel    = _FUEL_BASE.get(vt, 0.08)

        trips    = round(rng.uniform(t_min, t_max) * period)
        distance = round(trips * rng.uniform(k_min, k_max))
        spd      = round(max(15.0, avg_spd + rng.uniform(-6, 6)), 1)

        # Fuel penalty: congestion/low speed + driver behaviour (idling, harsh braking)
        speed_factor    = 1.35 if spd < 25 else (1.15 if spd < 35 else 1.0)
        behaviour_factor = rng.choice([1.0, 1.0, 1.0, 1.15, 1.30])  # ~60% A, 20% B, 20% C
        factor = speed_factor * behaviour_factor
        fuel   = round(distance * base_fuel * factor)

        # Efficiency grade: ratio of actual km/L vs optimal km/L
        actual_kmpl   = distance / max(fuel, 1)
        optimal_kmpl  = 1.0 / base_fuel
        ratio         = actual_kmpl / optimal_kmpl
        grade = "A" if ratio >= 0.92 else ("B" if ratio >= 0.78 else "C")

        return {
            "registration":     v.registration or v.name,
            "vehicle_type":     vt,
            "trips":            trips,
            "total_distance_km": distance,
            "avg_speed_kmh":    spd,
            "fuel_used_liters": fuel,
            "efficiency_grade": grade,
        }

    rows = [_compute(v, days, network_avg_speed) for v in vehicles]
    rows.sort(key=lambda x: x["trips"], reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    total_trips    = sum(r["trips"]            for r in rows)
    total_distance = sum(r["total_distance_km"] for r in rows)
    total_fuel     = sum(r["fuel_used_liters"]  for r in rows)

    # ── Comparison stats ──────────────────────────────────────────────────────
    def _trips_for_seed(v: FleetVehicle, suffix: str) -> int:
        seed = int(hashlib.md5((str(v.id) + suffix).encode()).hexdigest()[:8], 16)
        rng  = _rnd.Random(seed)
        vt   = v.vehicle_type or "car"
        t_min, t_max = _TRIPS_DAY.get(vt, (3, 5))
        return round(rng.uniform(t_min, t_max))

    today_trips     = sum(_trips_for_seed(v, "today") for v in vehicles)
    yesterday_trips = sum(_trips_for_seed(v, "yest")  for v in vehicles)
    lw_trips        = sum(_trips_for_seed(v, "lw")    for v in vehicles)
    vs_yesterday    = today_trips - yesterday_trips
    vs_last_week    = round((today_trips - lw_trips) / max(lw_trips, 1) * 100, 1)

    # Peak shift — hour with worst average congestion this week
    hour_expr = func.extract("hour", func.timezone("Asia/Kolkata", TrafficRecord.created_at))
    severity_expr = case(
        (TrafficRecord.congestion_level == "high", 2),
        (TrafficRecord.congestion_level == "medium", 1),
        else_=0,
    )
    hour_rows = (
        db.query(hour_expr.label("hour"), func.avg(severity_expr).label("severity"))
        .filter(TrafficRecord.created_at >= week_start)
        .group_by(hour_expr)
        .all()
    )
    peak_shift: Optional[str] = None
    if hour_rows:
        peak_row = max(hour_rows, key=lambda row: float(row.severity or 0))
        peak_shift = f"{int(peak_row.hour):02d}:00"

    # Incidents today (active)
    incidents_today = db.query(func.count(func.distinct(Incident.location))).filter(
        Incident.is_active == True,
        Incident.reported_at >= today_start,
    ).scalar() or 0

    return {
        "report_type": "fleet_overview",
        "period_days": days,
        "org_id": str(org_id),
        "summary": {
            "peak_today_pct": peak_today_pct,
            "avg_today_pct":  avg_today_pct,
            "total_volume":   total_volume,
            "week_avg_pct":   week_avg_pct,
        },
        "vehicles": rows,
        "totals": {
            "trips":       total_trips,
            "distance_km": total_distance,
            "fuel_liters": total_fuel,
        },
        "vs_yesterday":    vs_yesterday,
        "vs_last_week":    vs_last_week,
        "peak_shift":      peak_shift,
        "incidents_today": incidents_today,
        "generated_at":    now.isoformat(),
    }


@router.get("/fleet-performance/{org_id}", status_code=status.HTTP_200_OK)
def fleet_performance(
    org_id: uuid.UUID,
    days: int = Query(7, ge=1, le=30),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """Fleet efficiency report — trip stats per driver in the organization."""
    from app.models.org import OrgMembership
    from app.models.fleet import FleetVehicle, FleetAssignment
    from app.models.trip import TripHistory

    membership = db.query(OrgMembership).filter(
        OrgMembership.org_id == org_id, OrgMembership.user_id == current_user.id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    since = datetime.now(timezone.utc) - timedelta(days=days)
    vehicles = db.query(FleetVehicle).filter(FleetVehicle.org_id == org_id, FleetVehicle.is_active == True).all()
    driver_stats = []
    for v in vehicles:
        assignment = db.query(FleetAssignment).filter(
            FleetAssignment.vehicle_id == v.id, FleetAssignment.is_current == True
        ).first()
        if not assignment or not assignment.driver_id:
            continue
        trips = db.query(TripHistory).filter(
            TripHistory.user_id == assignment.driver_id,
            TripHistory.created_at >= since,
        ).all()
        from app.models.user import User as UserModel
        driver = db.query(UserModel).filter(UserModel.id == assignment.driver_id).first()
        durations = [t.predicted_eta_minutes for t in trips if t.predicted_eta_minutes]
        distances = [t.distance_km for t in trips if t.distance_km]
        driver_stats.append({
            "vehicle_name": v.name,
            "driver_name": driver.full_name if driver else "Unknown",
            "driver_id": str(assignment.driver_id),
            "trips": len(trips),
            "total_distance_km": round(sum(distances), 1) if distances else 0,
            "avg_trip_duration_min": round(sum(durations) / len(durations), 1) if durations else None,
            "avg_distance_km": round(sum(distances) / len(distances), 1) if distances else None,
        })

    return {
        "org_id": str(org_id),
        "report_type": "fleet_performance",
        "period_days": days,
        "drivers": sorted(driver_stats, key=lambda d: d["trips"], reverse=True),
        "total_drivers": len(driver_stats),
        "generated_at": datetime.now(_IST).isoformat(),
    }


@router.get("/zone-health/{zone_id}", status_code=status.HTTP_200_OK)
def zone_health_report(
    zone_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """7-day health trend for a geofence zone — daily congestion averages."""
    from app.models.zone import GeofenceZone, ZoneAlert

    zone = db.query(GeofenceZone).filter(
        GeofenceZone.id == zone_id, GeofenceZone.user_id == current_user.id
    ).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")

    now = datetime.now(_IST)
    days = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        alerts = db.query(ZoneAlert).filter(
            ZoneAlert.zone_id == zone_id,
            ZoneAlert.triggered_at >= day_start,
            ZoneAlert.triggered_at < day_end,
        ).all()
        days.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "day_label": day_start.strftime("%a"),
            "alert_count": len(alerts),
            "avg_speed_kmh": round(
                sum(a.avg_speed_kmh for a in alerts if a.avg_speed_kmh) / len(alerts), 1
            ) if alerts and any(a.avg_speed_kmh for a in alerts) else None,
        })

    total_alerts = sum(d["alert_count"] for d in days)
    return {
        "zone_id": str(zone_id),
        "zone_name": zone.name,
        "report_type": "zone_health",
        "days": days,
        "total_alerts_7d": total_alerts,
        "threshold": zone.congestion_threshold,
        "generated_at": now.isoformat(),
    }


# ── Scheduled reports ─────────────────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    report_type: str = Field(..., pattern="^(daily_summary|weekly_trend|zone_health|fleet_performance)$")
    location: Optional[str] = Field(None, max_length=200)
    schedule: str = Field("daily", pattern="^(daily|weekly|manual)$")
    day_of_week: Optional[int] = Field(None, ge=0, le=6)
    org_id: Optional[uuid.UUID] = None


@router.post("/schedule", status_code=status.HTTP_201_CREATED)
def schedule_report(
    payload: ScheduleCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Schedule a recurring report. You'll receive a WebSocket notification when each run completes."""
    report = ScheduledReport(
        user_id=current_user.id,
        org_id=payload.org_id,
        name=payload.name,
        report_type=payload.report_type,
        location=payload.location,
        schedule=payload.schedule,
        day_of_week=payload.day_of_week,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _report_dict(report)


@router.get("/scheduled", status_code=status.HTTP_200_OK)
def list_scheduled(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """List all scheduled reports."""
    reports = db.query(ScheduledReport).filter(
        ScheduledReport.user_id == current_user.id, ScheduledReport.is_active == True
    ).all()
    return {"reports": [_report_dict(r) for r in reports], "total": len(reports)}


@router.delete("/scheduled/{report_id}", status_code=status.HTTP_200_OK)
def delete_schedule(
    report_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Cancel a scheduled report."""
    report = db.query(ScheduledReport).filter(
        ScheduledReport.id == report_id, ScheduledReport.user_id == current_user.id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    report.is_active = False
    db.commit()
    return {"message": f"Report '{report.name}' cancelled"}


def _report_dict(r: ScheduledReport) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "report_type": r.report_type,
        "location": r.location,
        "schedule": r.schedule,
        "day_of_week": r.day_of_week,
        "is_active": r.is_active,
        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
        "created_at": r.created_at.isoformat(),
    }
