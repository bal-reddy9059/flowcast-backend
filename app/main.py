import asyncio
import logging
import os
import traceback
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    test_connection()
    run_column_migrations()
    from app.routes.india_ws import _district_broadcast
    set_broadcast_fn(_district_broadcast)
    congestion_task = asyncio.create_task(_congestion_monitor())
    departure_task  = asyncio.create_task(_departure_alert_monitor())
    india_task      = asyncio.create_task(run_india_traffic_collector())
    district_task   = asyncio.create_task(run_district_collector())
    logger.info("Background monitors started (congestion + departure alerts + India collector + district collector)")
    yield
    congestion_task.cancel()
    departure_task.cancel()
    india_task.cancel()
    district_task.cancel()
    for t in (congestion_task, departure_task, india_task, district_task):
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
        "name": "Admin",
        "description": "System monitoring, user management, and DB maintenance. **Requires admin account.**",
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


# ─── Health ────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "FlowCast API", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
