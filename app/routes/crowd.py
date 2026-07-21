from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Path, Request

from app.services import crowd_service

router = APIRouter(prefix="/api/v1/crowd", tags=["Crowd — Live Prediction"])


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
    "/all/now",
    summary="Live crowd — all stations",
    description=(
        "Returns the current live crowd state for all 6 stations. "
        "Served from in-memory cache updated every 30 seconds.\n\n"
        "For real-time streaming use `ws://localhost:8000/ws/crowd`."
    ),
)
async def all_crowd_now(request: Request):
    try:
        return _success(await crowd_service.get_all_crowd_now(_pool(request)))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))


@router.get(
    "/{station_id}/now",
    summary="Live crowd — single station",
    description="Returns the current live crowd state for one station. Served from cache (updated every 30 s).",
    responses={404: {"description": "Station not found."}},
)
async def crowd_now(
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
        data = await crowd_service.get_crowd_now(_pool(request), station_id)
        if not data:
            raise HTTPException(status_code=404, detail=_error(f"Station not found: {station_id}"))
        return _success(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))


@router.get(
    "/{station_id}/hourly",
    summary="24-hour crowd prediction",
    description="Returns a 24-element array (hours 0–23) of predicted crowd scores for today.",
    responses={404: {"description": "Station not found."}},
)
async def hourly_prediction(
    request: Request,
    station_id: str = Path(..., description="Station ID.", openapi_examples={
        "Secunderabad Junction":     {"value": "86c7d3f0-7889-5d9a-8134-c1ed9ff24147"},
        "KSR Bangalore Railway":     {"value": "341fed3e-210b-5aba-9846-149f991b9a10"},
        "Majestic Bus Terminal":     {"value": "feebf092-004f-5b71-b156-bb26c4533492"},
        "Shivajinagar Bus Stand":    {"value": "5c36b0c7-3c47-59eb-908b-c77f2871d590"},
        "Hyderabad Deccan Railway":  {"value": "2683f4a8-0414-5c84-9f3a-6d876bc7fe01"},
        "Mahatma Gandhi Bus Station":{"value": "b6596665-ae90-53e1-8ef5-7a707f470185"},
    }),
):
    try:
        data = await crowd_service.get_hourly_prediction(_pool(request), station_id)
        if data is None:
            raise HTTPException(status_code=404, detail=_error(f"Station not found: {station_id}"))
        return _success(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))


@router.get(
    "/{station_id}/weekly",
    summary="7-day weekly pattern",
    description="Returns a 7-element array (Mon–Sun) of average predicted crowd load per day.",
    responses={404: {"description": "Station not found."}},
)
async def weekly_pattern(
    request: Request,
    station_id: str = Path(..., description="Station ID.", openapi_examples={
        "Majestic Bus Terminal":     {"value": "feebf092-004f-5b71-b156-bb26c4533492"},
        "KSR Bangalore Railway":     {"value": "341fed3e-210b-5aba-9846-149f991b9a10"},
        "Shivajinagar Bus Stand":    {"value": "5c36b0c7-3c47-59eb-908b-c77f2871d590"},
        "Hyderabad Deccan Railway":  {"value": "2683f4a8-0414-5c84-9f3a-6d876bc7fe01"},
        "Mahatma Gandhi Bus Station":{"value": "b6596665-ae90-53e1-8ef5-7a707f470185"},
        "Secunderabad Junction":     {"value": "86c7d3f0-7889-5d9a-8134-c1ed9ff24147"},
    }),
):
    try:
        data = await crowd_service.get_weekly_pattern(_pool(request), station_id)
        if data is None:
            raise HTTPException(status_code=404, detail=_error(f"Station not found: {station_id}"))
        return _success(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))


@router.get(
    "/{station_id}/best-time",
    summary="Best time to visit",
    description="Finds the optimal 3-hour window with the lowest predicted crowd score today.",
    responses={404: {"description": "Station not found."}},
)
async def best_time(
    request: Request,
    station_id: str = Path(..., description="Station ID.", openapi_examples={
        "Hyderabad Deccan Railway":  {"value": "2683f4a8-0414-5c84-9f3a-6d876bc7fe01"},
        "KSR Bangalore Railway":     {"value": "341fed3e-210b-5aba-9846-149f991b9a10"},
        "Majestic Bus Terminal":     {"value": "feebf092-004f-5b71-b156-bb26c4533492"},
        "Shivajinagar Bus Stand":    {"value": "5c36b0c7-3c47-59eb-908b-c77f2871d590"},
        "Mahatma Gandhi Bus Station":{"value": "b6596665-ae90-53e1-8ef5-7a707f470185"},
        "Secunderabad Junction":     {"value": "86c7d3f0-7889-5d9a-8134-c1ed9ff24147"},
    }),
):
    try:
        data = await crowd_service.get_best_time(_pool(request), station_id)
        if data is None:
            raise HTTPException(status_code=404, detail=_error(f"Station not found: {station_id}"))
        return _success(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))
