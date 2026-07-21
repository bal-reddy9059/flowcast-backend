from typing import List
import uuid

from app.services.crowd_station_service import resolve_station_id
from app.utils.api_response import to_ist_iso


def _as_uuid(station_id: str):
    resolved = resolve_station_id(station_id)
    try:
        return uuid.UUID(resolved)
    except (ValueError, AttributeError, TypeError):
        return resolved


async def get_logs(pool, station_id: str, limit: int = 50) -> List[dict]:
    sid = _as_uuid(station_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, station_id, crowd_score, crowd_level, predicted_at, hour_of_day, day_of_week
            FROM crowd_logs
            WHERE station_id = $1 AND crowd_score > 5
            ORDER BY predicted_at DESC
            LIMIT $2
            """,
            sid, limit,
        )
    return [
        {
            "id": row["id"],
            "station_id": str(row["station_id"]),
            "crowd_score": row["crowd_score"],
            "crowd_level": row["crowd_level"],
            "predicted_at": to_ist_iso(row["predicted_at"]) if row["predicted_at"] else None,
            "hour_of_day": row["hour_of_day"],
            "day_of_week": row["day_of_week"],
        }
        for row in rows
    ]
