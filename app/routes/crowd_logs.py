from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Path, Request

from app.services import crowd_log_service

router = APIRouter(prefix="/api/v1/crowd-logs", tags=["Crowd — Logs"])


def _success(data):
    return {"success": True, "data": data, "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"}


def _error(msg):
    return {"success": False, "error": msg, "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"}


def _pool(request: Request):
    pool = getattr(request.app.state, "crowd_pool", None)
    if not pool:
        raise HTTPException(status_code=503, detail=_error("Crowd DB unavailable"))
    return pool


@router.get(
    "/{station_id}",
    summary="Crowd log history",
    description=(
        "Returns the last 50 crowd log entries for a station, ordered newest first.\n\n"
        "Each entry contains crowd_score, crowd_level, predicted_at, hour_of_day, and day_of_week."
    ),
    responses={500: {"description": "Internal server error."}},
)
async def get_logs(
    request: Request,
    station_id: str = Path(..., description="Station ID.", openapi_examples={
        "KSR Bangalore Railway":     {"value": "341fed3e-210b-5aba-9846-149f991b9a10"},
        "Majestic Bus Terminal":     {"value": "feebf092-004f-5b71-b156-bb26c4533492"},
        "Shivajinagar Bus Stand":    {"value": "5c36b0c7-3c47-59eb-908b-c77f2871d590"},
        "Hyderabad Deccan Railway":  {"value": "2683f4a8-0414-5c84-9f3a-6d876bc7fe01"},
        "Mahatma Gandhi Bus Station":{"value": "b6596665-ae90-53e1-8ef5-7a707f470185"},
        "Secunderabad Junction":     {"value": "86c7d3f0-7889-5d9a-8134-c1ed9ff24147"},
    }),
):
    try:
        return _success(await crowd_log_service.get_logs(_pool(request), station_id))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))
