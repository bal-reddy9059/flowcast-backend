from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine
import models
from routers import traffic
from app.routes.heatmap import router as heatmap_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=engine)
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
