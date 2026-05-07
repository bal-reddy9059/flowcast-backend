import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.database import Base, engine, test_connection
from app.routes.traffic import router as traffic_router

load_dotenv()

# ─── Create tables ─────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ─── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="FlowCast API",
    description="Real-time traffic prediction and monitoring backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ───────────────────────────────────────────────────────────────────
app.include_router(traffic_router, prefix="/api/v1")


# ─── Lifecycle ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    test_connection()


# ─── Health check ──────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "app": "FlowCast API", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
