import os
import runpy
from pathlib import Path

import asyncpg


async def create_crowd_pool():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        dsn = database_url.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
        return await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5, timeout=3, command_timeout=5)

    return await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "traffic-data"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
        min_size=1,
        max_size=5,
        timeout=3,
        command_timeout=5,
    )


def _seed_stations() -> list[dict]:
    """Load the canonical station seed without running the destructive CLI migration."""
    seed_file = Path(__file__).parent / "database" / "crowd_seed.py"
    return runpy.run_path(str(seed_file))["STATIONS"]


async def ensure_crowd_schema(pool) -> None:
    """Idempotently create and seed crowd tables for a new deployment database."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                type VARCHAR(50) NOT NULL CHECK (type IN ('bus', 'railway')),
                city VARCHAR(100) NOT NULL,
                state VARCHAR(100) NOT NULL,
                capacity INTEGER NOT NULL,
                peak_hours VARCHAR(100),
                lat DECIMAL(9,6),
                lng DECIMAL(9,6),
                amenities TEXT[],
                created_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crowd_logs (
                id SERIAL PRIMARY KEY,
                station_id UUID NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
                crowd_score INTEGER NOT NULL,
                crowd_level VARCHAR(20) NOT NULL,
                predicted_at TIMESTAMP DEFAULT NOW(),
                hour_of_day INTEGER,
                day_of_week INTEGER
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_crowd_logs_station_predicted "
            "ON crowd_logs (station_id, predicted_at DESC)"
        )
        await conn.executemany(
            """
            INSERT INTO stations
                (id, name, type, city, state, capacity, peak_hours, lat, lng, amenities)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                city = EXCLUDED.city,
                state = EXCLUDED.state,
                capacity = EXCLUDED.capacity,
                peak_hours = EXCLUDED.peak_hours,
                lat = EXCLUDED.lat,
                lng = EXCLUDED.lng,
                amenities = EXCLUDED.amenities
            """,
            [
                (
                    station["id"],
                    station["name"],
                    station["type"],
                    station["city"],
                    station["state"],
                    station["capacity"],
                    station["peak_hours"],
                    station["lat"],
                    station["lng"],
                    station["amenities"],
                )
                for station in _seed_stations()
            ],
        )
