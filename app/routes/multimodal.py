"""Multi-modal journey planner endpoint."""

import logging
import math
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/routes", tags=["Multi-Modal Planner"])
logger = logging.getLogger(__name__)

# ── Coordinate lookup for common Indian areas ──────────────────────────────────
# Covers major neighbourhoods, stations, and landmarks across metro cities

_LOCATION_COORDS: dict[str, tuple[float, float]] = {
    # Mumbai
    "andheri east": (19.1136, 72.8697), "andheri west": (19.1197, 72.8464),
    "andheri": (19.1136, 72.8697), "bkc": (19.0632, 72.8687),
    "bandra kurla complex": (19.0632, 72.8687), "bandra": (19.0544, 72.8402),
    "ghatkopar": (19.0869, 72.9077), "dadar": (19.0183, 72.8434),
    "lower parel": (18.9955, 72.8272), "powai": (19.1197, 72.9090),
    "kurla": (19.0726, 72.8800), "thane": (19.2183, 72.9781),
    "borivali": (19.2288, 72.8561), "malad": (19.1869, 72.8484),
    "goregaon": (19.1663, 72.8526), "kandivali": (19.2045, 72.8493),
    "churchgate": (18.9351, 72.8274), "csmt": (18.9399, 72.8356),
    "worli": (19.0146, 72.8152), "nariman point": (18.9257, 72.8212),
    "juhu": (19.1017, 72.8262), "versova": (19.1273, 72.8130),
    "santacruz": (19.0815, 72.8465), "vile parle": (19.0969, 72.8497),
    "navi mumbai": (19.0368, 73.0158), "belapur": (19.0175, 73.0382),
    "panvel": (18.9894, 73.1175),
    # Delhi
    "connaught place": (28.6315, 77.2167), "cp": (28.6315, 77.2167),
    "south delhi": (28.5355, 77.2510), "north delhi": (28.7041, 77.1025),
    "gurgaon": (28.4595, 77.0266), "gurugram": (28.4595, 77.0266),
    "noida": (28.5355, 77.3910), "faridabad": (28.4089, 77.3178),
    "dwarka": (28.5823, 77.0502), "rohini": (28.7495, 77.0680),
    "janakpuri": (28.6283, 77.0825), "lajpat nagar": (28.5683, 77.2427),
    "saket": (28.5244, 77.2167), "vasant kunj": (28.5205, 77.1580),
    "nehru place": (28.5491, 77.2530), "hauz khas": (28.5535, 77.2032),
    "new delhi railway station": (28.6429, 77.2193), "airport delhi": (28.5561, 77.1000),
    "gurgaon sector 14": (28.4567, 77.0374),
    # Bangalore
    "whitefield": (12.9698, 77.7500), "koramangala": (12.9352, 77.6245),
    "indiranagar": (12.9784, 77.6408), "silk board": (12.9176, 77.6229),
    "mg road": (12.9716, 77.6071), "electronic city": (12.8458, 77.6606),
    "hsr layout": (12.9116, 77.6370), "btm layout": (12.9166, 77.6101),
    "jayanagar": (12.9299, 77.5827), "marathahalli": (12.9591, 77.6972),
    "hebbal": (13.0353, 77.5970), "yelahanka": (13.1007, 77.5963),
    "jp nagar": (12.9080, 77.5856), "bannerghatta road": (12.8836, 77.5970),
    "kr puram": (13.0073, 77.6934), "yeshwanthpur": (13.0297, 77.5456),
    "bangalore airport": (13.1989, 77.7068), "kempegowda": (12.9779, 77.5716),
    # Hyderabad
    "hitech city": (17.4435, 78.3772), "gachibowli": (17.4401, 78.3489),
    "banjara hills": (17.4138, 78.4486), "jubilee hills": (17.4323, 78.4070),
    "secunderabad": (17.4399, 78.4983), "begumpet": (17.4432, 78.4625),
    "ameerpet": (17.4374, 78.4480), "kukatpally": (17.4849, 78.3984),
    "lb nagar": (17.3488, 78.5514), "uppal": (17.4053, 78.5597),
    # Chennai
    "t nagar": (13.0418, 80.2341), "adyar": (13.0063, 80.2574),
    "anna nagar": (13.0850, 80.2101), "velachery": (12.9751, 80.2200),
    "omr": (12.9000, 80.2200), "porur": (13.0358, 80.1579),
    "tambaram": (12.9249, 80.1000), "guindy": (13.0067, 80.2206),
    # Pune
    "hinjewadi": (18.5912, 73.7389), "kothrud": (18.5074, 73.8077),
    "shivajinagar": (18.5308, 73.8474), "baner": (18.5590, 73.7868),
    "hadapsar": (18.5018, 73.9258), "viman nagar": (18.5679, 73.9143),
    "koregaon park": (18.5362, 73.8938), "pimpri": (18.6208, 73.8014),
    # Kolkata
    "salt lake": (22.5771, 88.4204), "park street": (22.5510, 88.3516),
    "howrah": (22.5958, 88.2636), "dumdum": (22.6440, 88.4218),
    "new town": (22.5962, 88.4685), "tollygunge": (22.4956, 88.3467),
}


def _resolve_coords(name: str) -> Optional[tuple[float, float]]:
    """Fuzzy-match a location name to (lat, lng). Returns None if not found."""
    key = name.strip().lower()
    if key in _LOCATION_COORDS:
        return _LOCATION_COORDS[key]
    # Prefer longest partial match from local table
    best = None
    best_len = 0
    for k, v in _LOCATION_COORDS.items():
        if k in key or key in k:
            if len(k) > best_len:
                best_len = len(k)
                best = v
    if best:
        return best
    # Fall back to shared India geocoder used by routes/optimize
    try:
        from app.routes.route import _geocode
        loc = _geocode(name)
        if loc:
            return (float(loc["lat"]), float(loc["lng"]))
    except Exception as exc:
        logger.debug("Shared geocode failed for %s: %s", name, exc)
    return None


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return round(R * 2 * math.asin(a ** 0.5), 2)


# ── Request body ──────────────────────────────────────────────────────────────

_PLACEHOLDER_VALUES = {
    "string", "text", "foo", "bar", "example", "test",
    "origin", "destination", "location", "place", "from", "to",
}


class MultimodalRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "origin": "Andheri East",
                "destination": "BKC",
            }
        },
    )

    origin: Optional[str] = Field(None, min_length=2, description="Origin location name")
    destination: Optional[str] = Field(None, min_length=2, description="Destination name")
    # Frontend aliases
    from_: Optional[str] = Field(None, alias="from", min_length=2)
    to: Optional[str] = Field(None, min_length=2)
    origin_lat: Optional[float] = Field(None, ge=6.0, le=37.5)
    origin_lng: Optional[float] = Field(None, ge=68.0, le=97.5)
    dest_lat: Optional[float] = Field(None, ge=6.0, le=37.5)
    dest_lng: Optional[float] = Field(None, ge=68.0, le=97.5)
    destination_lat: Optional[float] = Field(None, ge=6.0, le=37.5)
    destination_lng: Optional[float] = Field(None, ge=68.0, le=97.5)
    depart_at: Optional[str] = Field(None, description="ISO-8601 departure time (optional)")

    @model_validator(mode="after")
    def resolve_names(self):
        origin = (self.origin or self.from_ or "").strip()
        destination = (self.destination or self.to or "").strip()
        if not origin or not destination:
            raise ValueError("origin/from and destination/to are required")
        for label, value in (("origin", origin), ("destination", destination)):
            if value.lower() in _PLACEHOLDER_VALUES:
                raise ValueError(
                    f"'{value}' is not a valid {label}. "
                    "Use a recognised Indian neighbourhood (e.g. 'Andheri East', 'BKC')."
                )
        self.origin = origin
        self.destination = destination
        if self.dest_lat is None and self.destination_lat is not None:
            self.dest_lat = self.destination_lat
        if self.dest_lng is None and self.destination_lng is not None:
            self.dest_lng = self.destination_lng
        return self


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/multimodal", status_code=status.HTTP_200_OK)
def multimodal_plan_post(
    payload: MultimodalRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """AI-powered multi-modal journey planner — accepts origin/destination as names.

    Coordinates are optional; if omitted, the endpoint resolves them from a
    built-in lookup of 100+ Indian city neighbourhoods.

    **Body:**
    ```json
    { "origin": "Andheri East", "destination": "BKC, Mumbai" }
    ```

    Returns journey segments (drive → metro → auto), total time, cost, and CO₂ saved.
    """
    return _run_plan(
        origin=payload.origin,
        destination=payload.destination,
        origin_lat=payload.origin_lat,
        origin_lng=payload.origin_lng,
        dest_lat=payload.dest_lat,
        dest_lng=payload.dest_lng,
        user=current_user,
        db=db,
    )


@router.get("/multimodal", status_code=status.HTTP_200_OK)
def multimodal_plan_get(
    origin: str = Query(..., min_length=2, examples=["Andheri East"]),
    destination: str = Query(..., min_length=2, examples=["BKC"]),
    origin_lat: Optional[float] = Query(None, ge=6.0, le=37.5),
    origin_lng: Optional[float] = Query(None, ge=68.0, le=97.5),
    dest_lat: Optional[float] = Query(None, ge=6.0, le=37.5),
    dest_lng: Optional[float] = Query(None, ge=68.0, le=97.5),
    current_user: Annotated[User, Depends(get_current_user)] = None,
    db: Annotated[Session, Depends(get_db)] = None,
) -> dict:
    """GET variant — same as POST but with query parameters. All lat/lng are optional."""
    for field_name, value in (("origin", origin), ("destination", destination)):
        if value.strip().lower() in _PLACEHOLDER_VALUES:
            from fastapi import HTTPException as _HTTPException
            raise _HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "invalid_location", "field": field_name,
                        "message": f"'{value}' is not a valid location name."},
            )
    return _run_plan(
        origin=origin,
        destination=destination,
        origin_lat=origin_lat,
        origin_lng=origin_lng,
        dest_lat=dest_lat,
        dest_lng=dest_lng,
        user=current_user,
        db=db,
    )


# ── Shared logic ──────────────────────────────────────────────────────────────

def _run_plan(
    origin: str,
    destination: str,
    origin_lat: Optional[float],
    origin_lng: Optional[float],
    dest_lat: Optional[float],
    dest_lng: Optional[float],
    user,
    db,
) -> dict:
    from app.services.multimodal_planner import get_multimodal_plan

    # Resolve coordinates if not provided
    o_coords = (origin_lat, origin_lng) if (origin_lat and origin_lng) else _resolve_coords(origin)
    d_coords = (dest_lat, dest_lng) if (dest_lat and dest_lng) else _resolve_coords(destination)

    missing = []
    if o_coords is None:
        missing.append(f"origin '{origin}'")
    if d_coords is None:
        missing.append(f"destination '{destination}'")
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "location_not_found",
                "message": (
                    "Could not resolve " + " and ".join(missing) + ". "
                    "Try a known neighbourhood (Andheri East, BKC, Gachibowli, Connaught Place)."
                ),
            },
        )

    o_lat, o_lng = o_coords
    d_lat, d_lng = d_coords
    distance_km = _haversine(o_lat, o_lng, d_lat, d_lng)

    result = get_multimodal_plan(
        origin=origin,
        destination=destination,
        origin_lat=o_lat,
        origin_lng=o_lng,
        dest_lat=d_lat,
        dest_lng=d_lng,
        distance_km=distance_km,
        db=db,
    )

    logger.info(
        "Multimodal plan: %s → %s (%.1f km, segments=%d, source=%s, user=%s)",
        origin, destination, distance_km,
        len(result.get("segments") or []),
        result.get("source"),
        getattr(user, "id", "anon"),
    )
    return result
