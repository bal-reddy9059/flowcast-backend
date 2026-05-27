"""Carbon footprint calculator — environmental impact of your commute."""

import logging

from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter(prefix="/eco", tags=["Carbon Footprint"])
logger = logging.getLogger(__name__)

# CO2 grams emitted per km (IPCC / DEFRA estimates)
_CO2_PER_KM = {
    "driving": 120,   # average petrol/diesel car
    "transit": 40,    # urban bus + metro mix
    "walking": 0,
}

# Approximate calories burned per km of active travel
_CALORIES_PER_KM = {
    "driving": 0,
    "transit": 5,    # walking to/from stop
    "walking": 65,
}

# A mature tree absorbs ~21 kg CO2 per year
_TREE_ABSORPTION_GRAMS_PER_YEAR = 21_000


@router.get("/footprint", status_code=status.HTTP_200_OK)
def calculate_footprint(
    distance_km: float = Query(..., gt=0, le=500, description="One-way trip distance in km"),
    mode: str = Query("driving", description="Travel mode: driving / walking / transit"),
    round_trip: bool = Query(False, description="Double the distance for a return journey"),
) -> dict:
    """Calculate CO₂ footprint for a trip and compare all travel modes.

    Returns grams/kg of CO₂, trees needed to offset the trip, calories burned,
    and a mode comparison so users can see how much they'd save by going green.
    """
    if mode not in _CO2_PER_KM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be driving, walking, or transit",
        )

    total_km = distance_km * 2 if round_trip else distance_km
    co2_grams = _CO2_PER_KM[mode] * total_km
    calories = int(_CALORIES_PER_KM[mode] * total_km)
    trees_to_offset = round(co2_grams / _TREE_ABSORPTION_GRAMS_PER_YEAR, 4)

    driving_co2 = _CO2_PER_KM["driving"] * total_km

    comparison = {}
    for m in ("driving", "transit", "walking"):
        m_co2_g   = round(_CO2_PER_KM[m] * total_km, 1)
        m_co2_kg  = round(m_co2_g / 1000, 2)
        saving_g  = round(max(0.0, driving_co2 - m_co2_g), 1)
        saving_kg = round(saving_g / 1000, 2)
        saving_pct = round(saving_g / driving_co2 * 100) if driving_co2 else 0
        comparison[m] = {
            "co2_grams":              m_co2_g,
            "co2_kg":                 m_co2_kg,
            "calories_burned":        int(_CALORIES_PER_KM[m] * total_km),
            "savings_vs_driving_grams": saving_g,
            "savings_vs_driving_kg":    saving_kg,
            "savings_percentage":       saving_pct,
            "is_selected":            m == mode,
        }

    tip = _build_tip(mode, distance_km, co2_grams, total_km)

    logger.info("Footprint: %.1f km %s → %d g CO2", total_km, mode, co2_grams)
    return {
        "distance_km":  distance_km,
        "total_km":     total_km,
        "round_trip":   round_trip,
        "mode":         mode,
        "co2_emissions": {
            "grams": round(co2_grams, 1),
            "kg":    round(co2_grams / 1000, 2),
        },
        "trees_to_offset_annually": round(trees_to_offset, 2),
        "calories_burned":          calories,
        "mode_comparison":          comparison,
        "tip":                      tip,
    }


def _fmt_co2(grams: float) -> str:
    """Display in kg when ≥ 1000 g, otherwise in grams."""
    if grams >= 1000:
        return f"{round(grams / 1000, 2)} kg"
    return f"{round(grams, 1)} g"


def _build_tip(mode: str, distance_km: float, co2_grams: float, total_km: float) -> str | None:
    if mode == "walking":
        cal = int(_CALORIES_PER_KM["walking"] * total_km)
        return f"Great choice! Walking {distance_km:.1f} km burns ~{cal} calories and emits zero CO₂."
    if mode == "driving" and distance_km <= 2:
        walk_cal = int(_CALORIES_PER_KM["walking"] * total_km)
        return (
            f"Short trip ({distance_km:.1f} km) — walking saves {_fmt_co2(co2_grams)} CO₂ "
            f"and burns ~{walk_cal} calories!"
        )
    if mode == "driving":
        transit_saving = co2_grams - _CO2_PER_KM["transit"] * total_km
        pct = round(transit_saving / co2_grams * 100) if co2_grams else 0
        return f"Switching to transit would save ~{_fmt_co2(transit_saving)} CO₂ ({pct}% less) on this trip."
    if mode == "transit":
        driving_co2 = _CO2_PER_KM["driving"] * total_km
        return (
            f"Good call! Transit emits {_fmt_co2(co2_grams)} CO₂ vs "
            f"{_fmt_co2(driving_co2)} for driving on this trip."
        )
    return None
