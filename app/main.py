import asyncio
import logging
import os
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

from app.database import Base, SessionLocal, engine, migrate_users_id_to_uuid, migrate_routes_id_to_uuid, migrate_favorites_id_to_uuid, migrate_notifications_id_to_uuid, migrate_trips_id_to_uuid, migrate_alerts_id_to_uuid, run_column_migrations, seed_admin_user, test_connection
from app.core.rate_limiter import setup_rate_limiter

# ── Explicit model imports so create_all() sees every table ──────────────────
from app.models.user import User                                     # noqa: F401
from app.models.predictor import TrafficRecord, Incident, PredictionResult  # noqa: F401
from app.models.route import SavedRoute                              # noqa: F401
from app.models.notification import Notification                     # noqa: F401
from app.models.favorite import FavoriteLocation                     # noqa: F401
from app.models.preferences import UserPreferences                   # noqa: F401
from app.models.trip import TripHistory                              # noqa: F401
from app.models.alert import DepartureAlert                          # noqa: F401
from app.models.share import RouteShareToken                         # noqa: F401
# ── Enterprise models ──────────────────────────────────────────────────────────
from app.models.org import Organization, OrgMembership               # noqa: F401
from app.models.fleet import FleetVehicle, FleetAssignment           # noqa: F401
from app.models.zone import GeofenceZone, ZoneAlert                  # noqa: F401
from app.models.webhook import Webhook, WebhookDelivery              # noqa: F401
from app.models.rule import AlertRule, RuleEvaluation                # noqa: F401
from app.models.report import ScheduledReport                        # noqa: F401
from app.models.weather import WeatherSnapshot                        # noqa: F401
from app.models.driver_behavior import DriverBehaviorLog, DriverDailyScore  # noqa: F401
from app.models.api_key import ApiKey                                        # noqa: F401

from app.routes.incidents import router as incidents_router
from app.routes.weather import router as weather_router
from app.routes.developer import router as developer_router
from app.routes.ai import router as ai_router
from app.routes.stories import router as stories_router
from app.routes.multimodal import router as multimodal_router
from app.routes.live import router as live_router
from app.routes.org import router as org_router
from app.routes.fleet import router as fleet_router
from app.routes.zones import router as zones_router
from app.routes.webhooks import router as webhooks_router
from app.routes.rules import router as rules_router
from app.routes.reports import router as reports_router
from app.routes.admin import router as admin_router
from app.routes.alerts import router as alerts_router
from app.routes.analytics import router as analytics_router
from app.routes.auth import router as auth_router
from app.routes.commute import router as commute_router
from app.routes.eco import router as eco_router
from app.routes.eta import router as eta_router
from app.routes.favorites import router as favorites_router
from app.routes.heatmap import router as heatmap_router
from app.routes.notification import router as notification_router
from app.routes.preferences import router as preferences_router
from app.routes.route import router as route_router
from app.routes.traffic import router as traffic_router
from app.routes.india import router as india_router
from app.routes.india_ws import router as india_ws_router
from app.routes.prediction import router as prediction_router
from app.routes.trips import router as trips_router
from app.services.alert_service import check_departure_alerts
from app.services.connection_manager import manager as ws_manager
from app.services.notification_service import check_saved_routes_for_congestion
from app.services.realtime_collector import run_india_traffic_collector
from app.services.district_collector import run_district_collector, set_broadcast_fn

logger = logging.getLogger(__name__)

migrate_users_id_to_uuid()
migrate_routes_id_to_uuid()
migrate_favorites_id_to_uuid()
migrate_notifications_id_to_uuid()
migrate_trips_id_to_uuid()
migrate_alerts_id_to_uuid()
Base.metadata.create_all(bind=engine)
seed_admin_user()


async def _congestion_monitor():
    """Check every saved route for high congestion every 60 seconds."""
    while True:
        try:
            db = SessionLocal()
            try:
                await check_saved_routes_for_congestion(db, ws_manager)
            finally:
                db.close()
        except Exception as exc:
            logger.error("Congestion monitor error: %s", exc)
        await asyncio.sleep(60)


async def _departure_alert_monitor():
    """Fire departure alerts 60 s before the notice window, every 60 seconds."""
    while True:
        try:
            await check_departure_alerts(ws_manager)
        except Exception as exc:
            logger.error("Departure alert monitor error: %s", exc)
        await asyncio.sleep(60)


async def _car_tick_broadcaster():
    """Advance simulated car positions every 2 s and broadcast to connected WS clients."""
    from app.routes.live import _broadcast_cars, _car_sockets
    from app.services.car_simulator import car_simulator
    tick_count = 0
    if not car_simulator._initialized:
        car_simulator.initialize_from_locations()
    while True:
        try:
            car_simulator.tick()
            if _car_sockets:
                snapshot = car_simulator.get_snapshot()
                await _broadcast_cars({
                    "type": "cars_update",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total": len(snapshot),
                    "cars": snapshot,
                })
            tick_count += 1
            if tick_count >= 900:  # 30 min at 2-s intervals
                car_simulator.refresh_from_db()
                tick_count = 0
        except Exception as exc:
            logger.error("Car tick broadcaster error: %s", exc)
        await asyncio.sleep(2)


async def _live_trip_updater():
    """Recalculate ETA for every active live trip every 15 s and push via WebSocket."""
    from app.routes.live import _live_sessions
    from app.services.eta_service import calculate_eta_for_location as _calc_eta
    while True:
        await asyncio.sleep(15)
        if not _live_sessions:
            continue
        for session_id, session in list(_live_sessions.items()):
            ws = session.get("websocket")
            if ws is None:
                continue
            db = SessionLocal()
            try:
                eta = _calc_eta(session["origin"], session["distance_km"], session["mode"], db)
                prev_eta = session.get("last_eta") or eta.eta_minutes
                if eta.eta_minutes < prev_eta * 0.95:
                    trend = "improving"
                elif eta.eta_minutes > prev_eta * 1.05:
                    trend = "worsening"
                else:
                    trend = "stable"
                session["last_eta"] = eta.eta_minutes
                session["last_congestion"] = eta.congestion_level
                session["last_speed"] = eta.average_speed_kmh
                try:
                    await ws.send_json({
                        "type": "eta_update",
                        "eta_minutes": round(eta.eta_minutes, 1),
                        "congestion_level": eta.congestion_level,
                        "speed_kmh": round(eta.average_speed_kmh, 1),
                        "trend": trend,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    pass
            except Exception as exc:
                logger.error("Live trip updater error for session %s: %s", session_id, exc)
            finally:
                db.close()


async def _traffic_pulse_monitor():
    """Compare traffic vs previous cycle every 60 s; broadcast change events to pulse clients."""
    from app.routes.live import _broadcast_pulse, _pulse_prev_state
    from app.models.predictor import TrafficRecord
    from app.services.india_locations import INDIA_LOCATIONS
    _LEVELS = {"low": 0, "medium": 1, "high": 2}
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            for loc in INDIA_LOCATIONS:
                name = loc["name"]
                record = (
                    db.query(TrafficRecord)
                    .filter(TrafficRecord.location == name)
                    .order_by(TrafficRecord.created_at.desc())
                    .first()
                )
                if record is None:
                    continue
                cur_congestion = record.congestion_level or "medium"
                cur_speed = float(record.average_speed or 0)
                prev = _pulse_prev_state.get(name)
                _pulse_prev_state[name] = {"congestion_level": cur_congestion, "average_speed": cur_speed}
                if prev is None:
                    continue
                prev_congestion = prev["congestion_level"]
                prev_speed = prev["average_speed"]
                event = None
                if cur_congestion == "high" and prev_congestion in ("medium", "low"):
                    event = "congestion_spike"
                elif _LEVELS.get(cur_congestion, 1) < _LEVELS.get(prev_congestion, 1):
                    event = "congestion_clearing"
                elif prev_speed > 0 and cur_speed < prev_speed * 0.8:
                    event = "speed_drop"
                elif prev_speed > 0 and cur_speed > prev_speed * 1.2:
                    event = "speed_recovery"
                if event:
                    await _broadcast_pulse({
                        "type": "pulse_event",
                        "event": event,
                        "location": name,
                        "city": loc.get("city", ""),
                        "from_level": prev_congestion,
                        "to_level": cur_congestion,
                        "speed_kmh": round(cur_speed, 1),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
        except Exception as exc:
            logger.error("Traffic pulse monitor error: %s", exc)
        finally:
            db.close()


async def _zone_alert_monitor():
    """Check all active geofence zones every 60 s; fire alerts when congestion threshold is breached."""
    from app.models.zone import GeofenceZone, ZoneAlert
    from app.routes.zones import _query_zone_traffic
    from app.services.notification_service import create_notification, send_websocket_notification
    _CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}
    _last_alerted: dict[str, datetime] = {}
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            zones = db.query(GeofenceZone).filter(GeofenceZone.is_active == True).all()
            for zone in zones:
                locations, avg_speed, dominant = _query_zone_traffic(zone, db)
                if _CONGESTION_SCORE.get(dominant, 0) < _CONGESTION_SCORE.get(zone.congestion_threshold, 2):
                    continue
                last = _last_alerted.get(str(zone.id))
                if last and (datetime.now(timezone.utc) - last).total_seconds() < 1800:
                    continue
                import json as _json
                alert = ZoneAlert(
                    zone_id=zone.id,
                    congestion_level=dominant,
                    affected_locations=_json.dumps([l["name"] for l in locations]),
                    avg_speed_kmh=avg_speed,
                )
                db.add(alert)
                db.commit()
                _last_alerted[str(zone.id)] = datetime.now(timezone.utc)
                notification = await create_notification(
                    user_id=zone.user_id,
                    route_id=None,
                    title=f"Zone Alert: {zone.name}",
                    message=f"Congestion reached '{dominant}' inside zone '{zone.name}'. {len(locations)} locations affected.",
                    notification_type="congestion_alert",
                    severity="high" if dominant == "high" else "medium",
                    location=zone.name,
                    db=db,
                )
                await send_websocket_notification(str(zone.user_id), notification, ws_manager, db)
        except Exception as exc:
            logger.error("Zone alert monitor error: %s", exc)
        finally:
            db.close()


async def _rule_engine_monitor():
    """Evaluate all active custom alert rules every 60 s."""
    from app.models.rule import AlertRule, RuleEvaluation
    from app.models.predictor import TrafficRecord
    from app.routes.rules import eval_condition
    from app.services.notification_service import create_notification, send_websocket_notification
    from app.services.webhook_service import dispatch_event_to_webhooks
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            rules = db.query(AlertRule).filter(AlertRule.is_active == True).all()
            now = datetime.now(timezone.utc)
            for rule in rules:
                # Cooldown check
                if rule.last_triggered_at:
                    elapsed = (now - rule.last_triggered_at).total_seconds() / 60
                    if elapsed < rule.cooldown_minutes:
                        continue
                # Get records in the duration window
                since = now - __import__("datetime").timedelta(minutes=rule.duration_minutes)
                records = (
                    db.query(TrafficRecord)
                    .filter(
                        TrafficRecord.location.ilike(f"%{rule.location}%"),
                        TrafficRecord.created_at >= since,
                    )
                    .order_by(TrafficRecord.created_at.desc())
                    .limit(20)
                    .all()
                )
                if not records:
                    continue
                # Check condition on ALL records in window
                field_map = {"congestion_level": "congestion_level", "average_speed": "average_speed", "vehicle_count": "vehicle_count"}
                attr = field_map.get(rule.condition_metric, "congestion_level")
                all_match = all(eval_condition(rule.condition_metric, rule.condition_operator, rule.condition_value, getattr(r, attr)) for r in records)
                if not all_match:
                    continue
                # Trigger
                actual_val = getattr(records[0], attr, None)
                db.add(RuleEvaluation(rule_id=rule.id, metric_value=str(actual_val), location=rule.location))
                rule.last_triggered_at = now
                db.commit()
                if rule.action_type in ("notify", "both"):
                    notification = await create_notification(
                        user_id=rule.user_id,
                        route_id=None,
                        title=f"Rule Triggered: {rule.name}",
                        message=f"'{rule.location}': {rule.condition_metric} {rule.condition_operator} {rule.condition_value} for {rule.duration_minutes} min. Current: {actual_val}",
                        notification_type="congestion_alert",
                        severity="high",
                        location=rule.location,
                        db=db,
                    )
                    await send_websocket_notification(str(rule.user_id), notification, ws_manager, db)
                if rule.action_type in ("webhook", "both"):
                    payload = {"event": "rule_triggered", "rule_name": rule.name, "location": rule.location,
                               "metric": rule.condition_metric, "value": str(actual_val), "timestamp": now.isoformat()}
                    await dispatch_event_to_webhooks(str(rule.user_id), "rule_triggered", payload, db)
        except Exception as exc:
            logger.error("Rule engine monitor error: %s", exc)
        finally:
            db.close()


async def _webhook_dispatcher():
    """Drain the webhook_queue and deliver each event."""
    from app.services.webhook_service import webhook_queue, dispatch_event_to_webhooks
    while True:
        try:
            event_type, payload, user_id = await asyncio.wait_for(webhook_queue.get(), timeout=5.0)
            db = SessionLocal()
            try:
                await dispatch_event_to_webhooks(user_id, event_type, payload, db)
            finally:
                db.close()
                webhook_queue.task_done()
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Webhook dispatcher error: %s", exc)


async def _driver_daily_scorer():
    """Run driver behavior daily scoring once per day at 00:05 UTC."""
    from app.services.behavior_service import run_daily_scoring
    while True:
        await asyncio.sleep(86400)  # 24 hours
        db = SessionLocal()
        try:
            scored = await run_daily_scoring(db)
            logger.info("Driver daily scoring: %d vehicles scored", scored)
        except Exception as exc:
            logger.error("Driver daily scorer error: %s", exc)
        finally:
            db.close()


async def _weather_collector():
    """Fetch weather for all monitored cities every 30 minutes."""
    from app.services.weather_service import refresh_all_cities
    await refresh_all_cities()   # immediate first run on startup
    while True:
        await asyncio.sleep(1800)  # 30 minutes
        try:
            await refresh_all_cities()
        except Exception as exc:
            logger.error("Weather collector error: %s", exc)


async def _ml_model_trainer():
    """Train the ML traffic model on startup, then retrain every 6 hours."""
    from app.services.ml_prediction_service import ml_model
    db = SessionLocal()
    try:
        trained = ml_model.train(db)
        if trained:
            logger.info("ML model initial training complete")
        else:
            logger.warning("ML model: not enough data for initial training — rule-based fallback active")
    except Exception as exc:
        logger.error("ML model initial training error: %s", exc)
    finally:
        db.close()

    while True:
        await asyncio.sleep(6 * 3600)   # retrain every 6 hours
        if not ml_model.needs_retrain():
            continue
        db = SessionLocal()
        try:
            ml_model.train(db)
            logger.info("ML model retrained")
        except Exception as exc:
            logger.error("ML model retrain error: %s", exc)
        finally:
            db.close()


async def _incident_expiry_monitor():
    """Auto-resolve incidents that have passed their expires_at time, every 5 minutes."""
    from app.models.predictor import Incident
    while True:
        await asyncio.sleep(300)   # 5 minutes
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            expired = (
                db.query(Incident)
                .filter(
                    Incident.is_active == True,
                    Incident.expires_at.isnot(None),
                    Incident.expires_at < now,
                )
                .all()
            )
            if expired:
                for inc in expired:
                    inc.is_active = False
                    inc.resolved_at = now
                db.commit()
                logger.info("Incident expiry monitor: auto-resolved %d incidents", len(expired))
        except Exception as exc:
            logger.error("Incident expiry monitor error: %s", exc)
        finally:
            db.close()


async def _story_refresher():
    """Regenerate AI traffic story cards every 5 minutes and broadcast to WS clients."""
    from app.routes.stories import broadcast_stories
    from app.services.story_generator import refresh_stories
    while True:
        await asyncio.sleep(300)
        db = SessionLocal()
        try:
            stories = await refresh_stories(db)
            if stories:
                await broadcast_stories(stories)
        except Exception as exc:
            logger.error("Story refresher error: %s", exc)
        finally:
            db.close()


async def _alert_tuner():
    """Run smart alert tuning once per day (86400s = 24h)."""
    await asyncio.sleep(3600)  # first run after 1h
    while True:
        db = SessionLocal()
        try:
            from app.services.alert_tuner import tune_user_alerts
            tuned = await tune_user_alerts(db)
            if tuned:
                logger.info("Alert tuner: adjusted thresholds for %d users", tuned)
        except Exception as exc:
            logger.error("Alert tuner error: %s", exc)
        finally:
            db.close()
        await asyncio.sleep(86400)


async def _report_scheduler():
    """Run scheduled daily/weekly reports once per hour; fires for due schedules."""
    from app.models.report import ScheduledReport
    while True:
        await asyncio.sleep(3600)
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            reports = db.query(ScheduledReport).filter(ScheduledReport.is_active == True).all()
            for r in reports:
                if r.schedule == "manual":
                    continue
                if r.schedule == "daily":
                    if r.last_run_at and (now - r.last_run_at).total_seconds() < 82800:
                        continue
                elif r.schedule == "weekly":
                    if r.day_of_week is not None and now.weekday() != r.day_of_week:
                        continue
                    if r.last_run_at and (now - r.last_run_at).total_seconds() < 6 * 86400:
                        continue
                r.last_run_at = now
                db.commit()
                from app.services.notification_service import create_notification, send_websocket_notification
                notification = await create_notification(
                    user_id=r.user_id,
                    route_id=None,
                    title=f"Report Ready: {r.name}",
                    message=f"Your {r.schedule} '{r.report_type}' report is ready. Fetch it from GET /api/v1/reports/{r.report_type.replace('_', '-')}.",
                    notification_type="system",
                    severity="low",
                    location=r.location or "",
                    db=db,
                )
                await send_websocket_notification(str(r.user_id), notification, ws_manager, db)
                logger.info("Scheduled report '%s' run for user %s", r.name, r.user_id)
        except Exception as exc:
            logger.error("Report scheduler error: %s", exc)
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    test_connection()
    run_column_migrations()

    # Seed incidents for all cities on first boot (idempotent)
    from app.services.incident_seeder import auto_seed_incidents, seed_week_incidents, patch_seeded_incidents, _INCIDENT_SEEDS
    _seed_db = SessionLocal()
    try:
        from app.models.predictor import Incident as _Inc
        if not _seed_db.query(_Inc).filter(_Inc.is_active == True).first():
            for _city in _INCIDENT_SEEDS:
                auto_seed_incidents(_city, _seed_db)
                seed_week_incidents(_city, _seed_db)
            logger.info("Incidents seeded for %d cities", len(_INCIDENT_SEEDS))
        patched = patch_seeded_incidents(_seed_db)
        if patched:
            logger.info("Patched %d existing seeded incidents (reported_by/upvotes/expires_at)", patched)
    except Exception as _exc:
        logger.warning("Incident seed skipped: %s", _exc)
    finally:
        _seed_db.close()

    from app.routes.india_ws import _district_broadcast
    set_broadcast_fn(_district_broadcast)
    congestion_task  = asyncio.create_task(_congestion_monitor())
    departure_task   = asyncio.create_task(_departure_alert_monitor())
    india_task       = asyncio.create_task(run_india_traffic_collector())
    district_task    = asyncio.create_task(run_district_collector())
    car_task         = asyncio.create_task(_car_tick_broadcaster())
    trip_task        = asyncio.create_task(_live_trip_updater())
    pulse_task       = asyncio.create_task(_traffic_pulse_monitor())
    zone_task        = asyncio.create_task(_zone_alert_monitor())
    rule_task        = asyncio.create_task(_rule_engine_monitor())
    webhook_task     = asyncio.create_task(_webhook_dispatcher())
    report_task      = asyncio.create_task(_report_scheduler())
    story_task       = asyncio.create_task(_story_refresher())
    tuner_task       = asyncio.create_task(_alert_tuner())
    ml_trainer_task  = asyncio.create_task(_ml_model_trainer())
    incident_task    = asyncio.create_task(_incident_expiry_monitor())
    weather_task     = asyncio.create_task(_weather_collector())
    behavior_task    = asyncio.create_task(_driver_daily_scorer())
    logger.info(
        "Background monitors started: congestion, departure, India, district, cars, trips, "
        "pulse, zones, rules, webhooks, reports, stories, alert-tuner, ML-trainer, incident-expiry"
    )
    yield
    all_tasks = (
        congestion_task, departure_task, india_task, district_task,
        car_task, trip_task, pulse_task, zone_task, rule_task, webhook_task, report_task,
        story_task, tuner_task, ml_trainer_task, incident_task, weather_task, behavior_task,
    )
    for t in all_tasks:
        t.cancel()
    for t in all_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    logger.info("Background monitors stopped")


_TAGS_METADATA = [
    {
        "name": "Health",
        "description": "Liveness checks — confirm the API is up and running.",
    },
    {
        "name": "Authentication",
        "description": (
            "Register, login, refresh tokens, and manage your profile. "
            "Click **Authorize** (top-right) and paste the `access_token` "
            "returned by `/auth/login` or `/auth/register` to unlock all protected endpoints."
        ),
    },
    {
        "name": "Traffic",
        "description": (
            "Submit raw traffic observations and query historical records. "
            "Supports single-record, bulk insert, prediction, anomaly detection, and CSV export."
        ),
    },
    {
        "name": "ETA Calculation",
        "description": "Real-time ETA for Hyderabad locations using live congestion data.",
    },
    {
        "name": "Analytics",
        "description": "Congestion trends, network snapshots, city health score, and heatmap-ready timelapse data.",
    },
    {
        "name": "Heatmap",
        "description": "Coordinate-level congestion data formatted for map overlay rendering.",
    },
    {
        "name": "Notifications",
        "description": "Retrieve and dismiss user notifications (congestion alerts, departure reminders).",
    },
    {
        "name": "Route Optimization",
        "description": (
            "Optimize India-wide routes via Google Maps + live traffic. "
            "Save, share, and delete favourite routes. "
            "**Coordinates must be within India (lat 6.0–37.5, lng 68.0–97.5).**"
        ),
    },
    {
        "name": "Commute Planner",
        "description": "24-hour rush-hour forecast, best departure window, and commute friendliness score.",
    },
    {
        "name": "Favorite Locations",
        "description": "Bookmark Hyderabad locations for quick live traffic status checks.",
    },
    {
        "name": "User Preferences",
        "description": "Notification settings, preferred travel mode, and quiet-hours configuration.",
    },
    {
        "name": "Trip History",
        "description": "Log journeys and view personal commute statistics.",
    },
    {
        "name": "Departure Alerts",
        "description": "Schedule departure reminders — get a WebSocket push N minutes before you need to leave.",
    },
    {
        "name": "Carbon Footprint",
        "description": "CO₂ emissions calculator and mode comparison for any trip distance.",
    },
    {
        "name": "India Traffic",
        "description": "All-India real-time monitoring — city health, state heatmap, hotspots, and national overview.",
    },
    {
        "name": "India Districts",
        "description": (
            "District-level traffic for all 766 Indian districts. "
            "Connect to `ws://<host>/api/v1/india/ws/districts` for live updates."
        ),
    },
    {
        "name": "Area Prediction",
        "description": "Hyperlocal 12-hour traffic forecast, hourly patterns, and multi-area comparison.",
    },
    {
        "name": "AI Traffic Copilot",
        "description": (
            "Natural language traffic intelligence powered by Claude AI.\n\n"
            "- **Chat** — `POST /ai/chat` — ask any traffic question in plain English\n"
            "- **Departure Coach** — `GET /ai/departure-coach` — personalized departure window from your trip history\n"
            "- **Commute Insight** — `GET /ai/commute-insight` — weekly AI summary of your commute patterns\n\n"
            "Requires `ANTHROPIC_API_KEY` in your environment. Falls back gracefully if key is absent."
        ),
    },
    {
        "name": "Live Traffic Stories",
        "description": (
            "AI-generated human-friendly news feed of what's happening on roads right now.\n\n"
            "- `GET /traffic/stories` — current story cards (refreshed every 5 min)\n"
            "- `WS /traffic/ws/stories` — live WebSocket stream of new story cards"
        ),
    },
    {
        "name": "Multi-Modal Planner",
        "description": (
            "AI-powered journey planning across driving, Metro, auto-rickshaw, bus, cycling, and walking.\n\n"
            "- `GET /routes/multimodal` — get optimal mode combination for your journey\n\n"
            "City Metro coverage: Delhi, Mumbai, Bangalore, Hyderabad, Chennai, Kolkata, Pune, Ahmedabad, Kochi."
        ),
    },
    {
        "name": "Developer Portal",
        "description": (
            "API key management for programmatic access to FlowCast data.\n\n"
            "- `POST /developer/keys` — create a key (raw shown ONCE — copy immediately)\n"
            "- `GET /developer/keys` — list all your keys\n"
            "- `DELETE /developer/keys/{id}` — revoke a key\n"
            "- `POST /developer/keys/{id}/rotate` — rotate key (old revoked, new issued)\n"
            "- `GET /developer/status` — validate key via `X-API-Key` header\n"
            "- `GET /developer/scopes` — list available permission scopes\n\n"
            "**Tiers:** free (1 000 req/day) · pro (50 000 req/day) · enterprise (unlimited)\n\n"
            "**Usage:** Pass key in `X-API-Key: fc_xxxx` header as alternative to JWT Bearer token."
        ),
    },
    {
        "name": "Weather & Traffic Impact",
        "description": (
            "Live weather data for 20 major Indian cities correlated with traffic conditions.\n\n"
            "- `GET /weather/cities` — weather snapshot for all monitored cities with congestion modifiers\n"
            "- `GET /weather/city/{city}` — detailed weather + travel tips for one city\n"
            "- `GET /weather/impact?location=X` — congestion impact for any traffic location\n"
            "- `GET /weather/status` — cache freshness and OWM configuration status\n\n"
            "Refreshed every 30 minutes. Set `OPENWEATHERMAP_API_KEY` for live data; "
            "falls back to deterministic simulation if key is absent."
        ),
    },
    {
        "name": "Incident Reporting",
        "description": (
            "Crowdsourced road incident reporting — report, verify, and resolve incidents in real time.\n\n"
            "- `POST /incidents` — report a new incident (accident, roadwork, closure, flooding, police)\n"
            "- `GET /incidents` — browse active incidents with location and type filters\n"
            "- `POST /incidents/{id}/upvote` — confirm the incident is still valid\n"
            "- `POST /incidents/{id}/downvote` — mark it as inaccurate (auto-resolves at −3 score)\n"
            "- `DELETE /incidents/{id}` — reporter or admin resolves the incident\n\n"
            "Incidents auto-expire after a configurable number of hours (default 4 h)."
        ),
    },
    {
        "name": "Admin",
        "description": "System monitoring, user management, and DB maintenance. **Requires admin account.**",
    },
    {
        "name": "Organizations",
        "description": "Multi-user organization accounts with owner/admin/member roles. Create orgs, invite team members, manage access.",
    },
    {
        "name": "Fleet Management",
        "description": "Register vehicles, assign drivers, and get live congestion status for every vehicle in your fleet.",
    },
    {
        "name": "Geofence Zones",
        "description": "Draw rectangle or circle zones on India roads. Receive alerts when congestion inside the zone breaches your threshold.",
    },
    {
        "name": "Webhook Integrations",
        "description": (
            "Push real-time traffic events to any HTTPS endpoint (Slack, Teams, custom). "
            "Payloads are HMAC-SHA256 signed for security.\n\n"
            "- `GET /webhooks/event-types` — **live stats** per event type: count last 1h/24h, top locations, last fired\n"
            "- `WS /webhooks/ws/live-events` — real-time event stream (10-second ticks) with activity score and firing types\n"
            "- Auto-seeds 3 demo webhooks on first `GET /webhooks` call"
        ),
    },
    {
        "name": "Alert Rules Engine",
        "description": "Define custom rules: 'if congestion_level >= high at Silk Board for 10 min → notify + webhook'. Fully configurable conditions, thresholds, and actions.",
    },
    {
        "name": "Traffic Reports",
        "description": "On-demand and scheduled reports: daily summary, weekly trend, hotspot analysis, fleet performance, zone health.",
    },
    {
        "name": "Live Traffic",
        "description": (
            "Real-time WebSocket feeds powered by live DB data and ML predictions.\n\n"
            "- **Car stream** — `ws://<host>/api/v1/traffic/ws/live` — 200 simulated cars moving across India roads, updated every 2 s\n"
            "- **Pulse feed** — `ws://<host>/api/v1/traffic/ws/pulse` — instant event alerts when congestion spikes or clears\n"
            "- **ML live feed** — `ws://<host>/api/v1/traffic/ws/ml-live` — live DB readings + RandomForest predictions for next 1/2/3 h + active incidents, every 5 s\n"
            "- **Live trip tracker** — `POST /trips/live/start` then `ws://<host>/api/v1/trips/ws/{session_id}` — real-time ETA updates every 15 s with trend indicator\n"
            "- **Should I Leave?** — `GET /commute/should-i-leave` — smart departure advisor comparing current vs forecast conditions\n"
            "- **ML model info** — `GET /traffic/ml/model-info` — RandomForest training status and feature list\n"
            "- **ML predict** — `GET /traffic/ml/predict` — test ML predictions for any hour/day without WebSocket"
        ),
    },
]

app = FastAPI(
    title="FlowCast API",
    description=(
        "Real-time traffic prediction and monitoring backend for India.\n\n"
        "## Quick start\n"
        "1. **Register** → `POST /api/v1/auth/register`\n"
        "2. **Login** → `POST /api/v1/auth/login` — copy the `access_token`\n"
        "3. Click **Authorize** (🔒 top-right) → paste the token → **Authorize**\n"
        "4. All protected endpoints are now unlocked — hit **Try it out** on any route.\n\n"
        "## Notes\n"
        "- Route optimization requires India coordinates (lat 6.0–37.5, lng 68.0–97.5).\n"
        "- India district WebSocket: `ws://<host>/api/v1/india/ws/districts`\n"
        "- Rate limit: 100 requests / minute per IP."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=_TAGS_METADATA,
    swagger_ui_parameters={
        "defaultModelsExpandDepth": -1,   # collapse schema panel by default
        "persistAuthorization": True,     # keep the JWT token after page refresh
        "tryItOutEnabled": True,          # open "Try it out" automatically on every endpoint
        "displayRequestDuration": True,   # show response time in ms
        "filter": True,                   # show the endpoint search/filter bar
        "syntaxHighlight.theme": "monokai",
    },
)

setup_rate_limiter(app)


@app.exception_handler(Exception)
async def _dev_exception_handler(request: Request, exc: Exception):
    """Return full traceback in development so errors are visible in the response."""
    tb = traceback.format_exc()
    logger.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, tb)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__, "traceback": tb},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ───────────────────────────────────────────────────────────────────
app.include_router(incidents_router,    prefix="/api/v1")
app.include_router(weather_router,      prefix="/api/v1")
app.include_router(developer_router,    prefix="/api/v1")
app.include_router(auth_router,         prefix="/api/v1")
app.include_router(traffic_router,      prefix="/api/v1")
app.include_router(eta_router,          prefix="/api/v1")
app.include_router(analytics_router,    prefix="/api/v1")
app.include_router(heatmap_router,      prefix="/api/v1")
app.include_router(notification_router, prefix="/api/v1")
app.include_router(route_router,        prefix="/api/v1")
app.include_router(commute_router,      prefix="/api/v1")
app.include_router(favorites_router,    prefix="/api/v1")
app.include_router(admin_router,        prefix="/api/v1")
# ─── Tier-1 user features ──────────────────────────────────────────────────────
app.include_router(preferences_router,  prefix="/api/v1")
app.include_router(trips_router,        prefix="/api/v1")
app.include_router(alerts_router,       prefix="/api/v1")
app.include_router(eco_router,          prefix="/api/v1")
app.include_router(india_router,        prefix="/api/v1")
app.include_router(india_ws_router,     prefix="/api/v1")
app.include_router(prediction_router,   prefix="/api/v1")
app.include_router(live_router,         prefix="/api/v1")
# ── AI features ────────────────────────────────────────────────────────────────
app.include_router(ai_router,           prefix="/api/v1")
app.include_router(stories_router,      prefix="/api/v1")
app.include_router(multimodal_router,   prefix="/api/v1")
# ── Enterprise routers ─────────────────────────────────────────────────────────
app.include_router(org_router,          prefix="/api/v1")
app.include_router(fleet_router,        prefix="/api/v1")
app.include_router(zones_router,        prefix="/api/v1")
app.include_router(webhooks_router,     prefix="/api/v1")
app.include_router(rules_router,        prefix="/api/v1")
app.include_router(reports_router,      prefix="/api/v1")


# ─── Health ────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "FlowCast API", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
