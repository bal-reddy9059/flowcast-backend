import os

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
