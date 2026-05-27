"""
Shared city → neighbourhood alias map for FlowCast.

When a user queries a city name (e.g. "Hyderabad") the corresponding list of
neighbourhood names is used to build an OR-filter across all traffic records
for that city, giving a city-wide aggregated result.

Only cities whose neighbourhood records do NOT contain the city name in their
own location string need entries here (e.g. "Gachibowli" never contains
"Hyderabad"). Cities like "Surat" already work via ilike because their records
are named "Ring Road Surat" etc., but are included anyway for explicitness.
"""

from sqlalchemy import or_

CITY_ALIASES: dict[str, list[str]] = {
    # ── Telangana ──────────────────────────────────────────────────────────────
    "hyderabad": [
        "Hitech City", "Gachibowli", "Madhapur", "Banjara Hills", "Jubilee Hills",
        "Kondapur", "Kukatpally", "LB Nagar", "Secunderabad", "Ameerpet",
        "Begumpet", "Dilsukhnagar", "Miyapur", "KPHB Colony", "Mehdipatnam",
        "Warangal Highway",
    ],

    # ── Karnataka ─────────────────────────────────────────────────────────────
    "bangalore": [
        "MG Road, Bangalore", "MG Road Bengaluru", "Koramangala", "Indiranagar",
        "Whitefield", "Electronic City", "Hebbal Flyover", "Silk Board Junction",
    ],
    "bengaluru": [
        "MG Road, Bangalore", "MG Road Bengaluru", "Koramangala", "Indiranagar",
        "Whitefield", "Electronic City", "Hebbal Flyover", "Silk Board Junction",
    ],

    # ── Maharashtra ───────────────────────────────────────────────────────────
    "mumbai": [
        "Dadar", "Worli Sea Link", "Powai", "Thane",
        "Bandra Kurla Complex", "Andheri West", "Marine Drive, Mumbai",
    ],
    "pune": [
        "Koregaon Park", "Hinjewadi", "Kothrud",
    ],

    # ── NCR / Delhi ───────────────────────────────────────────────────────────
    "delhi": [
        "Connaught Place", "Lajpat Nagar", "Rohini", "Dwarka",
        "Noida Expressway", "Noida Sector 18",
        "Cyber City Gurgaon", "NH-48 Gurgaon", "Faridabad", "Gomti Nagar",
    ],
    "ncr": [
        "Connaught Place", "Lajpat Nagar", "Rohini", "Dwarka",
        "Noida Expressway", "Noida Sector 18",
        "Cyber City Gurgaon", "NH-48 Gurgaon", "Faridabad",
    ],
    "gurgaon": [
        "Cyber City Gurgaon", "NH-48 Gurgaon",
    ],
    "noida": [
        "Noida Expressway", "Noida Sector 18",
    ],

    # ── Tamil Nadu ────────────────────────────────────────────────────────────
    "chennai": [
        "Anna Nagar", "Anna Salai, Chennai", "T Nagar Chennai",
        "Tambaram", "Guindy", "OMR Road Chennai",
    ],
    "coimbatore": [
        "RS Puram Coimbatore", "Avinashi Road",
    ],

    # ── West Bengal ───────────────────────────────────────────────────────────
    "kolkata": [
        "Park Street Kolkata", "Park Street, Kolkata",
        "Salt Lake Sector V", "Howrah Bridge", "Dum Dum",
    ],

    # ── Gujarat ───────────────────────────────────────────────────────────────
    "ahmedabad": [
        "SG Highway", "CG Road Ahmedabad", "Navrangpura",
    ],
    "surat": [
        "Ring Road Surat", "Varachha Road Surat",
    ],

    # ── Uttar Pradesh ─────────────────────────────────────────────────────────
    "lucknow": [
        "Hazratganj Lucknow", "Gomti Nagar", "Kanpur Road",
    ],

    # ── Kerala ────────────────────────────────────────────────────────────────
    "kochi": [
        "Kakkanad", "Edapally Junction", "MG Road Kochi",
    ],
    "cochin": [
        "Kakkanad", "Edapally Junction", "MG Road Kochi",
    ],
    "thiruvananthapuram": [
        "Thiruvananthapuram", "Thiruvananthapuram, Kerala",
    ],
    "trivandrum": [
        "Thiruvananthapuram", "Thiruvananthapuram, Kerala",
    ],

    # ── Andhra Pradesh ────────────────────────────────────────────────────────
    "visakhapatnam": [
        "Beach Road Vizag", "MVP Colony Vizag",
    ],
    "vizag": [
        "Beach Road Vizag", "MVP Colony Vizag",
    ],

    # ── Madhya Pradesh ────────────────────────────────────────────────────────
    "indore": [
        "MG Road Indore", "Vijay Nagar Indore",
    ],
    "bhopal": [
        "MP Nagar Bhopal",
    ],

    # ── Rajasthan ─────────────────────────────────────────────────────────────
    "jaipur": [
        "MI Road Jaipur", "Malviya Nagar Jaipur", "Vaishali Nagar",
    ],

    # ── Maharashtra ───────────────────────────────────────────────────────────
    "nagpur": [
        "Ring Road Nagpur", "Sitabuldi",
    ],

    # ── Punjab ────────────────────────────────────────────────────────────────
    "amritsar": [
        "Amritsar", "Amritsar, Punjab",
    ],
    "ludhiana": [
        "Ludhiana GT Road", "Ludhiana, Punjab",
    ],
    "chandigarh": [
        "Sector 17 Chandigarh",
    ],

    # ── Assam ─────────────────────────────────────────────────────────────────
    "guwahati": [
        "GS Road Guwahati",
    ],

    # ── Bihar ─────────────────────────────────────────────────────────────────
    "patna": [
        "Bailey Road Patna",
    ],

    # ── Jharkhand ─────────────────────────────────────────────────────────────
    "ranchi": [
        "Main Road Ranchi",
    ],

    # ── Uttarakhand ───────────────────────────────────────────────────────────
    "dehradun": [
        "Rajpur Road Dehradun",
    ],
}


def location_filter(model_col, location: str):
    """Return a SQLAlchemy filter clause, expanding city aliases where defined.

    Falls back to a plain ilike when the location is not a known city shortcut.
    """
    aliases = CITY_ALIASES.get(location.lower())
    if aliases:
        return or_(*[model_col.ilike(f"%{a}%") for a in aliases])
    return model_col.ilike(f"%{location}%")
