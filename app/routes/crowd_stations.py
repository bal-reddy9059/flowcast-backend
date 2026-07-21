from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Path, Request

from app.services import crowd_station_service

router = APIRouter(prefix="/api/v1/stations", tags=["Crowd — Stations"])


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
    "",
    summary="List all stations",
    description=(
        "Returns all stations with crowd score, level, and estimated occupancy. "
        "Uses live TomTom/HERE when available; otherwise a deterministic IST time-of-day baseline."
    ),
)
async def list_stations(request: Request):
    try:
        return _success(await crowd_station_service.get_all_stations(_pool(request)))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))


@router.get(
    "/city/{city}",
    summary="Stations by city",
    description="Filter stations by city (case-insensitive), e.g. `Bangalore`, `Hyderabad`, `Mumbai`, `Delhi`.",
    responses={404: {"description": "No stations found for that city."}},
)
async def stations_by_city(
    request: Request,
    city: str = Path(..., description="City name.", openapi_examples={
        "Bangalore": {"value": "Bangalore"},
        "Hyderabad": {"value": "Hyderabad"},
        "Mumbai": {"value": "Mumbai"},
        "Delhi": {"value": "Delhi"},
    }),
):
    try:
        stations = await crowd_station_service.get_stations_by_city(_pool(request), city)
        if not stations:
            raise HTTPException(status_code=404, detail=_error(f"No stations found in city: {city}"))
        return _success(stations)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))


@router.get(
    "/type/{station_type}",
    summary="Stations by type",
    description="Filter by transport type — `bus` or `railway`.",
    responses={400: {"description": "Invalid type."}},
)
async def stations_by_type(
    request: Request,
    station_type: str = Path(..., description="`bus` or `railway`.", openapi_examples={"railway": {"value": "railway"}, "bus": {"value": "bus"}}),
):
    if station_type not in ("bus", "railway"):
        raise HTTPException(status_code=400, detail=_error("type must be 'bus' or 'railway'"))
    try:
        return _success(await crowd_station_service.get_stations_by_type(_pool(request), station_type))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))


@router.get(
    "/{station_id}",
    summary="Get station by ID",
    description=(
        "Returns full details for one station with crowd data.\n\n"
        "Accepts UUIDs or aliases (`blr-rail-01`, `blr-bus-01`, `blr-bus-02`, "
        "`hyd-rail-01`, `hyd-bus-01`, `hyd-rail-02`).\n\n"
        "| Station | UUID |\n"
        "|---------|------|\n"
        "| KSR Bangalore City Railway | `341fed3e-210b-5aba-9846-149f991b9a10` |\n"
        "| Majestic Bus Terminal | `feebf092-004f-5b71-b156-bb26c4533492` |\n"
        "| Shivajinagar Bus Stand | `5c36b0c7-3c47-59eb-908b-c77f2871d590` |\n"
        "| Hyderabad Deccan Railway | `2683f4a8-0414-5c84-9f3a-6d876bc7fe01` |\n"
        "| Mahatma Gandhi Bus Station | `b6596665-ae90-53e1-8ef5-7a707f470185` |\n"
        "| Secunderabad Junction | `86c7d3f0-7889-5d9a-8134-c1ed9ff24147` |"
    ),
    responses={404: {"description": "Station not found."}},
)
async def get_station(
    request: Request,
    station_id: str = Path(..., description="Station UUID or alias.", openapi_examples={
        "KSR Bangalore Railway":  {"value": "341fed3e-210b-5aba-9846-149f991b9a10"},
        "blr-rail-01 (alias)":    {"value": "blr-rail-01"},
        "Majestic Bus Terminal":  {"value": "feebf092-004f-5b71-b156-bb26c4533492"},
        "Shivajinagar Bus Stand": {"value": "5c36b0c7-3c47-59eb-908b-c77f2871d590"},
        "Hyderabad Deccan Railway": {"value": "2683f4a8-0414-5c84-9f3a-6d876bc7fe01"},
        "Mahatma Gandhi Bus Station": {"value": "b6596665-ae90-53e1-8ef5-7a707f470185"},
        "Secunderabad Junction": {"value": "86c7d3f0-7889-5d9a-8134-c1ed9ff24147"},
    }),
):
    try:
        station = await crowd_station_service.get_station_by_id(_pool(request), station_id)
        if not station:
            raise HTTPException(status_code=404, detail=_error(f"Station not found: {station_id}"))
        return _success(station)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_error(str(e)))
