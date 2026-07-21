import asyncio
import logging
import os
import traceback
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

# Suppress utcnow() deprecation warnings that come from third-party packages
# (SQLAlchemy, Pydantic) — we have no control over those call sites.
warnings.filterwarnings(
    "ignore",
    message="datetime.datetime.utcnow\\(\\) is deprecated",
    category=DeprecationWarning,
    module=r"(sqlalchemy|pydantic).*",
)

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from starlette.middleware.gzip import GZipMiddleware

load_dotenv()

from app.database import Base, SessionLocal, engine, run_startup_migrations, cleanup_stale_db_backends, seed_admin_user, test_connection
from app.core.rate_limiter import setup_rate_limiter

# ── Explicit model imports so create_all() sees every table ──────────────────
from app.models.user import User                                     # noqa: F401
from app.models.predictor import TrafficRecord, Incident, IncidentVote, PredictionResult  # noqa: F401
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
from app.routes.crowd_stations import router as crowd_stations_router
from app.routes.crowd import router as crowd_router
from app.routes.crowd_logs import router as crowd_logs_router
from app.routes.crowd_ws import router as crowd_ws_router
from app.crowd_db import create_crowd_pool
from app.services.crowd_service import update_all_crowd as _update_crowd
from app.utils.crowd_ws_manager import crowd_ws_manager as _crowd_ws_manager
from app.services.alert_service import check_departure_alerts
from app.services.connection_manager import manager as ws_manager
from app.services.notification_service import check_saved_routes_for_congestion
from app.services.realtime_collector import run_india_traffic_collector
from app.services.district_collector import run_district_collector, set_broadcast_fn

logger = logging.getLogger(__name__)

def _ensure_schema() -> None:
    """Create tables + seed admin. Safe to call from a worker thread."""
    Base.metadata.create_all(bind=engine)
    seed_admin_user()


async def _congestion_monitor():
    """Check every saved route for high congestion every 60 seconds."""
    while True:
        try:
            def _open_db():
                return SessionLocal()

            db = await asyncio.to_thread(_open_db)
            try:
                await check_saved_routes_for_congestion(db, ws_manager)
            finally:
                await asyncio.to_thread(db.close)
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
        try:
            await asyncio.to_thread(car_simulator.initialize_from_locations)
        except Exception as exc:
            logger.warning("Car simulator init deferred: %s", type(exc).__name__)
            # Seed empty so tick loop never crashes; refresh will retry later
            car_simulator._initialized = True
            car_simulator._cars = {}
    while True:
        try:
            await asyncio.to_thread(car_simulator.tick)
            if _car_sockets:
                snapshot = await asyncio.to_thread(car_simulator.get_snapshot)
                await _broadcast_cars({
                    "type": "cars_update",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "total": len(snapshot),
                    "cars": snapshot,
                })
            tick_count += 1
            if tick_count >= 900:  # 30 min at 2-s intervals
                await asyncio.to_thread(car_simulator.refresh_from_db)
                tick_count = 0
        except Exception as exc:
            logger.error("Car tick broadcaster error: %s", exc)
        await asyncio.sleep(2)


async def _live_trip_updater():
    """Recalculate ETA for every active live trip every 15 s and push via WebSocket."""
    from app.routes.live import _live_sessions
    from app.services.eta_service import calculate_eta_for_location as _calc_eta
    from app.utils.api_response import api_success

    def _tick(session: dict) -> dict | None:
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
            return {
                "eta_minutes": round(eta.eta_minutes, 1),
                "congestion_level": eta.congestion_level,
                "speed_kmh": round(float(eta.average_speed_kmh or 0), 1),
                "trend": trend,
            }
        finally:
            db.close()

    while True:
        await asyncio.sleep(15)
        if not _live_sessions:
            continue
        for session_id, session in list(_live_sessions.items()):
            ws = session.get("websocket")
            if ws is None:
                continue
            try:
                update = await asyncio.to_thread(_tick, session)
                if update is None:
                    continue
                await ws.send_json(api_success(
                    data={"session_id": session_id, **update},
                    type="eta_update",
                ))
            except Exception as exc:
                logger.error("Live trip updater error for session %s: %s", session_id, exc)


async def _traffic_pulse_monitor():
    """Compare traffic vs previous cycle every 60 s; broadcast change events to pulse clients."""
    from app.routes.live import _broadcast_pulse, _pulse_prev_state
    from app.models.predictor import TrafficRecord
    from app.services.india_locations import INDIA_LOCATIONS

    _LEVELS = {"low": 0, "medium": 1, "high": 2}

    def _scan() -> list[dict]:
        events: list[dict] = []
        db = SessionLocal()
        try:
            names = [loc["name"] for loc in INDIA_LOCATIONS]
            cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
            rows = (
                db.query(TrafficRecord)
                .filter(
                    TrafficRecord.location.in_(names),
                    TrafficRecord.timestamp >= cutoff,
                )
                # PostgreSQL DISTINCT ON: one latest row per location instead
                # of loading the entire traffic history every minute.
                .distinct(TrafficRecord.location)
                .order_by(TrafficRecord.location, TrafficRecord.timestamp.desc())
                .all()
            )
            latest = {row.location: row for row in rows}

            for loc in INDIA_LOCATIONS:
                name = loc["name"]
                record = latest.get(name)
                if record is None:
                    continue
                cur_congestion = record.congestion_level or "medium"
                cur_speed = float(record.average_speed or 0)
                prev = _pulse_prev_state.get(name)
                _pulse_prev_state[name] = {
                    "congestion_level": cur_congestion,
                    "average_speed": cur_speed,
                }
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
                    events.append({
                        "type": "pulse_event",
                        "event": event,
                        "location": name,
                        "city": loc.get("city", ""),
                        "from_level": prev_congestion,
                        "to_level": cur_congestion,
                        "speed_kmh": round(cur_speed, 1),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
        finally:
            db.close()
        return events

    while True:
        await asyncio.sleep(60)
        try:
            for msg in await asyncio.to_thread(_scan):
                await _broadcast_pulse(msg)
        except Exception as exc:
            logger.error("Traffic pulse monitor error: %s", exc)


async def _zone_alert_monitor():
    """Check all active geofence zones every 60 s; fire alerts when congestion threshold is breached."""
    from app.models.zone import GeofenceZone, ZoneAlert
    from app.routes.zones import _query_zone_traffic
    from app.services.notification_service import create_notification, send_websocket_notification
    import json as _json
    _CONGESTION_SCORE = {"low": 0, "medium": 1, "high": 2}
    _COOLDOWN_SECONDS = 1800  # 30 minutes
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            zones = db.query(GeofenceZone).filter(GeofenceZone.is_active == True).all()
            now = datetime.now(timezone.utc)
            for zone in zones:
                locations, avg_speed, dominant, has_data = _query_zone_traffic(zone, db)
                if not has_data or dominant in ("unknown", None, ""):
                    continue
                if _CONGESTION_SCORE.get(dominant, 0) < _CONGESTION_SCORE.get(zone.congestion_threshold, 2):
                    continue

                # DB-based cooldown — survives server restarts unlike an in-memory dict
                cooldown_cutoff = now - timedelta(seconds=_COOLDOWN_SECONDS)
                recent = (
                    db.query(ZoneAlert)
                    .filter(
                        ZoneAlert.zone_id == zone.id,
                        ZoneAlert.triggered_at >= cooldown_cutoff.replace(tzinfo=None),
                    )
                    .first()
                )
                if recent:
                    continue

                alert = ZoneAlert(
                    zone_id=zone.id,
                    triggered_at=now.replace(tzinfo=None),
                    congestion_level=dominant,
                    affected_locations=_json.dumps([l["name"] for l in locations]),
                    avg_speed_kmh=round(avg_speed, 1) if avg_speed is not None else None,
                )
                db.add(alert)
                db.commit()

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
                logger.info("Zone alert fired for zone '%s' (user %s, level=%s)", zone.name, zone.user_id, dominant)
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
                since = now - timedelta(minutes=rule.duration_minutes)
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
                if rule.action_type in ("notify", "send_notification", "notification", "both"):
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
    from app.services.weather_service import ensure_weather_cache, refresh_all_cities

    # Instant sync seed so /weather/* never waits on boot timing
    ensure_weather_cache()
    await asyncio.sleep(2)
    try:
        await refresh_all_cities()
    except Exception as exc:
        logger.error("Weather collector initial error: %s", exc)
    while True:
        await asyncio.sleep(1800)  # 30 minutes
        try:
            await refresh_all_cities()
        except Exception as exc:
            logger.error("Weather collector error: %s", exc)


async def _ml_model_trainer():
    """Train the ML traffic model on startup, then retrain every 6 hours.

    Training runs in a worker thread so the asyncio event loop stays responsive.
    """
    from app.services.ml_prediction_service import ml_model

    def _train_once() -> bool:
        db = SessionLocal()
        try:
            return ml_model.train(db)
        finally:
            db.close()

    # Defer heavy train so early API calls stay fast
    await asyncio.sleep(45)
    try:
        trained = await asyncio.to_thread(_train_once)
        if trained:
            logger.info("ML model initial training complete")
        else:
            logger.warning("ML model: not enough data for initial training — rule-based fallback active")
    except Exception as exc:
        logger.error("ML model initial training error: %s", exc)

    while True:
        await asyncio.sleep(6 * 3600)   # retrain every 6 hours
        if not ml_model.needs_retrain():
            continue
        try:
            await asyncio.to_thread(_train_once)
            logger.info("ML model retrained")
        except Exception as exc:
            logger.error("ML model retrain error: %s", exc)


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


async def _crowd_updater(pool):
    """Recalculate and broadcast crowd scores. Slows down when live APIs are offline."""
    from app.services.traffic_flow_service import is_live_available

    while True:
        if pool:
            try:
                all_data = await _update_crowd(pool)
                ts = datetime.now(timezone.utc).isoformat() + "Z"
                await _crowd_ws_manager.broadcast_all({"type": "crowd_update", "stations": all_data, "timestamp": ts})
                for data in all_data:
                    await _crowd_ws_manager.broadcast_station(
                        data["station_id"],
                        {"type": "crowd_update", "station": data, "timestamp": ts},
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Crowd updater error: %s", exc)
        # Live APIs: refresh every 60s. Offline: every 3 min (baseline only).
        interval = 60 if is_live_available() else 180
        await asyncio.sleep(interval)


async def _story_refresher():
    """Regenerate traffic story cards periodically (rule-based if AI unavailable)."""
    from app.routes.stories import broadcast_stories
    from app.services.story_generator import refresh_stories
    from app.services.ai_service import is_ai_available

    if not is_ai_available():
        logger.info("Story refresher: no Anthropic key — using rule-based stories only")

    await asyncio.sleep(90)
    while True:
        db = SessionLocal()
        try:
            stories = await refresh_stories(db)
            if stories:
                await broadcast_stories(stories)
        except Exception as exc:
            logger.error("Story refresher error: %s", exc)
        finally:
            db.close()
        await asyncio.sleep(600 if not is_ai_available() else 300)


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
    """Fast boot: accept HTTP immediately; heavy work runs in threads / after yield."""
    app.state.crowd_pool = None
    app.state._bg_tasks = []

    try:
        await asyncio.wait_for(asyncio.to_thread(test_connection), timeout=3.0)
        # Clear abandoned idle-in-transaction sessions that lock traffic_records
        await asyncio.to_thread(cleanup_stale_db_backends, 20)
    except Exception as exc:
        logger.warning("DB connectivity check failed: %s", type(exc).__name__)

    # Schema + light seed in a worker so import/reload never blocks the event loop
    try:
        await asyncio.wait_for(asyncio.to_thread(_ensure_schema), timeout=8.0)
    except Exception as exc:
        logger.warning("Schema ensure skipped: %s", type(exc).__name__)

    try:
        from app.core.redis import init_redis
        await asyncio.wait_for(init_redis(), timeout=0.5)
    except Exception as exc:
        logger.warning("Redis init skipped: %s", type(exc).__name__)

    from app.routes.india_ws import _district_broadcast
    set_broadcast_fn(_district_broadcast)

    async def _deferred_boot() -> None:
        # Migrations first (in a thread). Monitors must not start while ALTER holds locks,
        # or sync DB calls on the event loop freeze every HTTP request.
        try:
            await asyncio.wait_for(asyncio.to_thread(run_startup_migrations), timeout=25.0)
            logger.info("Startup migrations finished")
        except Exception as exc:
            logger.warning("Startup migrations skipped: %s", type(exc).__name__)

        await asyncio.sleep(2)
        try:
            app.state.crowd_pool = await asyncio.wait_for(create_crowd_pool(), timeout=3.0)
            logger.info("Crowd prediction pool ready")
        except Exception as exc:
            logger.warning("Crowd pool unavailable: %s", type(exc).__name__)
            app.state.crowd_pool = None

        light = [
            asyncio.create_task(_crowd_updater(getattr(app.state, "crowd_pool", None))),
            asyncio.create_task(_congestion_monitor()),
            asyncio.create_task(_departure_alert_monitor()),
            asyncio.create_task(_car_tick_broadcaster()),
            asyncio.create_task(_live_trip_updater()),
            asyncio.create_task(_traffic_pulse_monitor()),
            asyncio.create_task(_incident_expiry_monitor()),
            asyncio.create_task(_weather_collector()),  # seed early — endpoints also self-seed
        ]
        app.state._bg_tasks = light
        logger.info("Light background monitors started (%d tasks)", len(light))

        await asyncio.sleep(30)
        heavy = [
            asyncio.create_task(run_india_traffic_collector()),
            asyncio.create_task(run_district_collector()),
            asyncio.create_task(_zone_alert_monitor()),
            asyncio.create_task(_rule_engine_monitor()),
            asyncio.create_task(_webhook_dispatcher()),
            asyncio.create_task(_report_scheduler()),
            asyncio.create_task(_story_refresher()),
            asyncio.create_task(_alert_tuner()),
            asyncio.create_task(_ml_model_trainer()),
            asyncio.create_task(_driver_daily_scorer()),
        ]
        app.state._bg_tasks.extend(heavy)
        logger.info("Heavy background collectors started (%d tasks)", len(heavy))

    boot_task = asyncio.create_task(_deferred_boot())
    logger.info("API ready — deferred boot running in background")
    yield

    boot_task.cancel()
    try:
        await boot_task
    except asyncio.CancelledError:
        pass
    for t in getattr(app.state, "_bg_tasks", []):
        t.cancel()
    for t in getattr(app.state, "_bg_tasks", []):
        try:
            await t
        except asyncio.CancelledError:
            pass
    crowd_pool = getattr(app.state, "crowd_pool", None)
    if crowd_pool:
        try:
            await crowd_pool.close()
        except Exception:
            pass
    try:
        from app.utils.http_client import close_http_client
        await close_http_client()
    except Exception:
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
        "name": "Crowd — Stations",
        "description": (
            "Bus and railway stations across major Indian cities with crowd data "
            "(live TomTom/HERE when available, otherwise IST time-of-day baseline).\n\n"
            "**Core UUIDs:** KSR `341fed3e-…`, Majestic `feebf092-…`, Shivajinagar `5c36b0c7-…`, "
            "Hyd Deccan `2683f4a8-…`, MGBS `b6596665-…`, Secunderabad `86c7d3f0-…`\n\n"
            "**Aliases:** `blr-rail-01`, `blr-bus-01`, `blr-bus-02`, `hyd-rail-01`, `hyd-bus-01`, `hyd-rail-02`"
        ),
    },
    {
        "name": "Crowd — Live Prediction",
        "description": (
            "Real-time crowd estimates for bus and railway stations, derived from "
            "live HERE/TomTom road traffic near each station (updated every 30 s), "
            "with deterministic baseline fallback when APIs are offline.\n\n"
            "- `GET /api/v1/crowd/all/now` — live crowd for all stations\n"
            "- `GET /api/v1/crowd/{id}/now` — live crowd for one station\n"
            "- `GET /api/v1/crowd/{id}/hourly` — 24-hour forecast from historical logs\n"
            "- `GET /api/v1/crowd/{id}/weekly` — Mon–Sun pattern\n"
            "- `GET /api/v1/crowd/{id}/best-time` — optimal 3-hour visit window\n\n"
            "**Crowd levels:** 0–25 Low · 26–50 Moderate · 51–75 High · 76–100 Overcrowded\n\n"
            "**Live WebSocket:** `ws://localhost:8000/ws/crowd` (all) · `ws://localhost:8000/ws/crowd/{id}` (one station)"
        ),
    },
    {
        "name": "Crowd — Logs",
        "description": "Last 50 crowd log entries per station, newest first.",
    },
    {
        "name": "Crowd — WebSocket",
        "description": (
            "WebSocket endpoints that push crowd updates every 30 seconds.\n\n"
            "- `ws://localhost:8000/ws/crowd` — live stream for all stations\n"
            "- `ws://localhost:8000/ws/crowd/{station_id}` — live stream for one station\n\n"
            "On connect, the current cached snapshot is sent immediately. "
            "Station IDs may be UUIDs or aliases (`blr-rail-01`, etc.)."
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


from app.utils.api_response import ApiResponseEnvelopeMiddleware, api_error  # noqa: E402


@app.exception_handler(SQLAlchemyTimeoutError)
@app.exception_handler(OperationalError)
async def _database_timeout_handler(request: Request, exc: Exception):
    """Return quickly when the database is saturated or cancels a slow query."""
    logger.warning("Database deadline on %s %s: %s", request.method, request.url.path, type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content=api_error(
            "Database is busy; retry shortly.",
            code="DATABASE_TIMEOUT",
        ),
        headers={"Retry-After": "1"},
    )


@app.middleware("http")
async def request_deadline(request: Request, call_next):
    """Guarantee an HTTP response instead of leaving clients queued for minutes."""
    try:
        configured_timeout = float(os.getenv("API_REQUEST_TIMEOUT_SECONDS", "3.5"))
    except (TypeError, ValueError):
        configured_timeout = 3.5
    # Environment configuration may tighten the deadline, never weaken the
    # public under-four-second response guarantee.
    timeout_seconds = min(3.5, max(0.5, configured_timeout))
    started = asyncio.get_running_loop().time()
    try:
        response = await asyncio.wait_for(call_next(request), timeout=timeout_seconds)
        elapsed = asyncio.get_running_loop().time() - started
        response.headers["X-Process-Time"] = f"{elapsed:.3f}"
        response.headers["Server-Timing"] = f"app;dur={elapsed * 1000:.1f}"
        if elapsed >= 1.0:
            logger.warning(
                "Slow request %.3fs: %s %s",
                elapsed,
                request.method,
                request.url.path,
            )
        return response
    except asyncio.TimeoutError:
        logger.error(
            "Request deadline exceeded after %.1fs: %s %s",
            timeout_seconds,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=504,
            content=api_error(
                "The request exceeded the server processing deadline.",
                code="REQUEST_TIMEOUT",
                details={"timeout_seconds": timeout_seconds},
            ),
            headers={"X-Process-Time": f"{timeout_seconds:.3f}"},
        )


@app.exception_handler(Exception)
async def _dev_exception_handler(request: Request, exc: Exception):
    """Return enveloped error with traceback in development."""
    tb = traceback.format_exc()
    logger.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, tb)
    return JSONResponse(
        status_code=500,
        content=api_error(
            str(exc),
            code=type(exc).__name__,
            details={"traceback": tb},
        ),
    )


# Envelope first (inner), CORS last (outer) so CORS headers apply to wrapped JSON.
app.add_middleware(ApiResponseEnvelopeMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

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
# ── Crowd Prediction ────────────────────────────────────────────────────────────
app.include_router(crowd_stations_router)
app.include_router(crowd_router)
app.include_router(crowd_logs_router)
app.include_router(crowd_ws_router)


# ─── Health ────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "FlowCast API", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
