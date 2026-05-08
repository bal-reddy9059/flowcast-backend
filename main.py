from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from database import engine
import models
from routers import traffic
from app.routes.heatmap import router as heatmap_router
from app.routes.route import router as route_router
from app.routes.eta import router as eta_router
from app.models.route import SavedRoute

import os
print("API KEY:", os.getenv("GOOGLE_MAPS_API_KEY"))
print("DISTANCE MATRIX API KEY:", os.getenv("GOOGLE_MAPS_DISTANCE_MATRIX_API_KEY"))
print("DIRECTIONS API KEY:", os.getenv("GOOGLE_MAPS_DIRECTIONS_API_KEY"))



def ensure_traffic_schema() -> None:
    models.Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        inspector = inspect(conn)
        if "traffic_records" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("traffic_records")]
            if "speed_kmh" not in columns:
                conn.execute(text("ALTER TABLE traffic_records ADD COLUMN speed_kmh FLOAT"))
            if "travel_time_mins" not in columns:
                conn.execute(text("ALTER TABLE traffic_records ADD COLUMN travel_time_mins FLOAT"))
            if "vehicle_count" in columns:
                conn.execute(
                    text(
                        "ALTER TABLE traffic_records ALTER COLUMN vehicle_count DROP NOT NULL"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE traffic_records ALTER COLUMN vehicle_count SET DEFAULT 0"
                    )
                )


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_traffic_schema()
    yield


app = FastAPI(
    title="Flowcast Traffic API",
    description="Real-time traffic monitoring with Google Maps integration, WebSocket push, and PostgreSQL history.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(traffic.router)
app.include_router(heatmap_router)
app.include_router(route_router)
app.include_router(eta_router)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Flowcast Traffic API is running",
        "docs": "/docs",
        "endpoints": {
            "traffic_snapshot": "GET /traffic",
            "traffic_history": "GET /traffic/history",
            "dummy_data": "GET /traffic/dummy",
            "websocket": "WS /traffic/ws",
        },
    }
