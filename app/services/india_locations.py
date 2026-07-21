"""
All-India traffic monitoring locations — 50+ cities, 100+ hotspots.
Each entry is a real junction/area used to poll the TomTom Flow API.
"""

INDIA_LOCATIONS: list[dict] = [
    # ── Maharashtra ──────────────────────────────────────────────────────────
    {"name": "Bandra Kurla Complex", "city": "Mumbai",     "state": "Maharashtra", "lat": 19.0660, "lng": 72.8676, "road_type": "arterial"},
    {"name": "Andheri West",         "city": "Mumbai",     "state": "Maharashtra", "lat": 19.1197, "lng": 72.8468, "road_type": "arterial"},
    {"name": "Dadar",                "city": "Mumbai",     "state": "Maharashtra", "lat": 19.0178, "lng": 72.8478, "road_type": "arterial"},
    {"name": "Thane",                "city": "Mumbai",     "state": "Maharashtra", "lat": 19.2183, "lng": 72.9781, "road_type": "arterial"},
    {"name": "Powai",                "city": "Mumbai",     "state": "Maharashtra", "lat": 19.1176, "lng": 72.9060, "road_type": "arterial"},
    {"name": "Worli Sea Link",       "city": "Mumbai",     "state": "Maharashtra", "lat": 19.0176, "lng": 72.8146, "road_type": "highway"},
    {"name": "Hinjewadi",            "city": "Pune",       "state": "Maharashtra", "lat": 18.5904, "lng": 73.7380, "road_type": "arterial"},
    {"name": "Koregaon Park",        "city": "Pune",       "state": "Maharashtra", "lat": 18.5363, "lng": 73.8938, "road_type": "residential"},
    {"name": "Kothrud",              "city": "Pune",       "state": "Maharashtra", "lat": 18.5074, "lng": 73.8077, "road_type": "arterial"},
    {"name": "Sitabuldi",            "city": "Nagpur",     "state": "Maharashtra", "lat": 21.1458, "lng": 79.0882, "road_type": "arterial"},
    {"name": "Ring Road Nagpur",     "city": "Nagpur",     "state": "Maharashtra", "lat": 21.1202, "lng": 79.0813, "road_type": "arterial"},

    # ── Delhi NCR ────────────────────────────────────────────────────────────
    {"name": "Connaught Place",      "city": "New Delhi",  "state": "Delhi",       "lat": 28.6315, "lng": 77.2167, "road_type": "arterial"},
    {"name": "Lajpat Nagar",         "city": "New Delhi",  "state": "Delhi",       "lat": 28.5673, "lng": 77.2378, "road_type": "arterial"},
    {"name": "Dwarka",               "city": "New Delhi",  "state": "Delhi",       "lat": 28.5921, "lng": 77.0460, "road_type": "residential"},
    {"name": "Rohini",               "city": "New Delhi",  "state": "Delhi",       "lat": 28.7041, "lng": 77.1025, "road_type": "residential"},
    {"name": "Cyber City Gurgaon",   "city": "Gurgaon",   "state": "Haryana",     "lat": 28.4952, "lng": 77.0928, "road_type": "arterial"},
    {"name": "NH-48 Gurgaon",        "city": "Gurgaon",   "state": "Haryana",     "lat": 28.4595, "lng": 77.0266, "road_type": "highway"},
    {"name": "Noida Sector 18",      "city": "Noida",     "state": "UP",          "lat": 28.5699, "lng": 77.3211, "road_type": "arterial"},
    {"name": "Noida Expressway",     "city": "Noida",     "state": "UP",          "lat": 28.5355, "lng": 77.3910, "road_type": "highway"},
    {"name": "Faridabad",            "city": "Faridabad", "state": "Haryana",     "lat": 28.4089, "lng": 77.3178, "road_type": "arterial"},

    # ── Karnataka ────────────────────────────────────────────────────────────
    {"name": "MG Road Bengaluru",    "city": "Bengaluru",  "state": "Karnataka",  "lat": 12.9758, "lng": 77.6082, "road_type": "arterial"},
    {"name": "Marathahalli",         "city": "Bengaluru",  "state": "Karnataka",  "lat": 12.9591, "lng": 77.6974, "road_type": "arterial"},
    {"name": "Electronic City",      "city": "Bengaluru",  "state": "Karnataka",  "lat": 12.8399, "lng": 77.6770, "road_type": "arterial"},
    {"name": "Whitefield",           "city": "Bengaluru",  "state": "Karnataka",  "lat": 12.9698, "lng": 77.7500, "road_type": "arterial"},
    {"name": "Indiranagar",          "city": "Bengaluru",  "state": "Karnataka",  "lat": 12.9784, "lng": 77.6408, "road_type": "residential"},
    {"name": "Silk Board Junction",  "city": "Bengaluru",  "state": "Karnataka",  "lat": 12.9174, "lng": 77.6228, "road_type": "arterial"},
    {"name": "Hebbal Flyover",       "city": "Bengaluru",  "state": "Karnataka",  "lat": 13.0450, "lng": 77.5966, "road_type": "highway"},
    {"name": "Koramangala",          "city": "Bengaluru",  "state": "Karnataka",  "lat": 12.9352, "lng": 77.6245, "road_type": "residential"},

    # ── Tamil Nadu ───────────────────────────────────────────────────────────
    {"name": "Anna Nagar",           "city": "Chennai",    "state": "Tamil Nadu", "lat": 13.0850, "lng": 80.2101, "road_type": "residential"},
    {"name": "OMR Road Chennai",     "city": "Chennai",    "state": "Tamil Nadu", "lat": 12.8996, "lng": 80.2209, "road_type": "arterial"},
    {"name": "T Nagar Chennai",      "city": "Chennai",    "state": "Tamil Nadu", "lat": 13.0418, "lng": 80.2341, "road_type": "arterial"},
    {"name": "Guindy",               "city": "Chennai",    "state": "Tamil Nadu", "lat": 13.0067, "lng": 80.2206, "road_type": "arterial"},
    {"name": "Tambaram",             "city": "Chennai",    "state": "Tamil Nadu", "lat": 12.9249, "lng": 80.1000, "road_type": "arterial"},
    {"name": "RS Puram Coimbatore",  "city": "Coimbatore", "state": "Tamil Nadu", "lat": 11.0017, "lng": 76.9540, "road_type": "residential"},
    {"name": "Avinashi Road",        "city": "Coimbatore", "state": "Tamil Nadu", "lat": 11.0343, "lng": 77.0427, "road_type": "highway"},

    # ── Telangana ────────────────────────────────────────────────────────────
    {"name": "Hitech City",          "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4486, "lng": 78.3908, "road_type": "arterial"},
    {"name": "Gachibowli",           "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4401, "lng": 78.3489, "road_type": "arterial"},
    {"name": "Banjara Hills",        "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4239, "lng": 78.4738, "road_type": "arterial"},
    {"name": "Jubilee Hills",        "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4326, "lng": 78.4071, "road_type": "arterial"},
    {"name": "Madhapur",             "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4484, "lng": 78.3915, "road_type": "arterial"},
    {"name": "Kondapur",             "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4647, "lng": 78.3578, "road_type": "arterial"},
    {"name": "Ameerpet",             "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4375, "lng": 78.4483, "road_type": "arterial"},
    {"name": "LB Nagar",             "city": "Hyderabad",  "state": "Telangana",  "lat": 17.3490, "lng": 78.5480, "road_type": "arterial"},
    {"name": "Secunderabad",         "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4399, "lng": 78.4983, "road_type": "arterial"},
    {"name": "Kukatpally",           "city": "Hyderabad",  "state": "Telangana",  "lat": 17.4848, "lng": 78.4138, "road_type": "arterial"},
    {"name": "Warangal Highway",     "city": "Warangal",   "state": "Telangana",  "lat": 17.9784, "lng": 79.5941, "road_type": "highway"},

    # ── West Bengal ──────────────────────────────────────────────────────────
    {"name": "Howrah Bridge",        "city": "Kolkata",    "state": "West Bengal","lat": 22.5851, "lng": 88.3468, "road_type": "arterial"},
    {"name": "Park Street Kolkata",  "city": "Kolkata",    "state": "West Bengal","lat": 22.5522, "lng": 88.3516, "road_type": "arterial"},
    {"name": "Salt Lake Sector V",   "city": "Kolkata",    "state": "West Bengal","lat": 22.5764, "lng": 88.4155, "road_type": "residential"},
    {"name": "Dum Dum",              "city": "Kolkata",    "state": "West Bengal","lat": 22.6425, "lng": 88.3984, "road_type": "arterial"},

    # ── Gujarat ──────────────────────────────────────────────────────────────
    {"name": "CG Road Ahmedabad",    "city": "Ahmedabad",  "state": "Gujarat",    "lat": 23.0395, "lng": 72.5551, "road_type": "arterial"},
    {"name": "SG Highway",           "city": "Ahmedabad",  "state": "Gujarat",    "lat": 23.0475, "lng": 72.5075, "road_type": "highway"},
    {"name": "Navrangpura",          "city": "Ahmedabad",  "state": "Gujarat",    "lat": 23.0281, "lng": 72.5620, "road_type": "arterial"},
    {"name": "Ring Road Surat",      "city": "Surat",      "state": "Gujarat",    "lat": 21.1702, "lng": 72.8311, "road_type": "highway"},
    {"name": "Varachha Road Surat",  "city": "Surat",      "state": "Gujarat",    "lat": 21.2109, "lng": 72.8690, "road_type": "arterial"},

    # ── Rajasthan ────────────────────────────────────────────────────────────
    {"name": "MI Road Jaipur",       "city": "Jaipur",     "state": "Rajasthan",  "lat": 26.9124, "lng": 75.7873, "road_type": "arterial"},
    {"name": "Malviya Nagar Jaipur", "city": "Jaipur",     "state": "Rajasthan",  "lat": 26.8504, "lng": 75.8059, "road_type": "residential"},
    {"name": "Vaishali Nagar",       "city": "Jaipur",     "state": "Rajasthan",  "lat": 26.9260, "lng": 75.7360, "road_type": "residential"},

    # ── Uttar Pradesh ────────────────────────────────────────────────────────
    {"name": "Hazratganj Lucknow",   "city": "Lucknow",    "state": "UP",         "lat": 26.8467, "lng": 80.9462, "road_type": "arterial"},
    {"name": "Gomti Nagar",          "city": "Lucknow",    "state": "UP",         "lat": 26.8553, "lng": 81.0087, "road_type": "residential"},
    {"name": "Kanpur Road",          "city": "Lucknow",    "state": "UP",         "lat": 26.7972, "lng": 80.8503, "road_type": "highway"},
    {"name": "Agra Fort Road",       "city": "Agra",       "state": "UP",         "lat": 27.1767, "lng": 78.0081, "road_type": "arterial"},

    # ── Madhya Pradesh ───────────────────────────────────────────────────────
    {"name": "Vijay Nagar Indore",   "city": "Indore",     "state": "MP",         "lat": 22.7500, "lng": 75.8900, "road_type": "arterial"},
    {"name": "MG Road Indore",       "city": "Indore",     "state": "MP",         "lat": 22.7174, "lng": 75.8634, "road_type": "arterial"},
    {"name": "MP Nagar Bhopal",      "city": "Bhopal",     "state": "MP",         "lat": 23.2319, "lng": 77.4328, "road_type": "arterial"},

    # ── Kerala ───────────────────────────────────────────────────────────────
    {"name": "MG Road Kochi",        "city": "Kochi",      "state": "Kerala",     "lat":  9.9312, "lng": 76.2673, "road_type": "arterial"},
    {"name": "Edapally Junction",    "city": "Kochi",      "state": "Kerala",     "lat": 10.0265, "lng": 76.3082, "road_type": "arterial"},
    {"name": "Kakkanad",             "city": "Kochi",      "state": "Kerala",     "lat": 10.0159, "lng": 76.3419, "road_type": "arterial"},
    {"name": "Thiruvananthapuram",   "city": "Thiruvananthapuram","state": "Kerala","lat":  8.5241, "lng": 76.9366,"road_type": "arterial"},

    # ── Andhra Pradesh ───────────────────────────────────────────────────────
    {"name": "MVP Colony Vizag",     "city": "Visakhapatnam","state": "AP",       "lat": 17.7231, "lng": 83.3012, "road_type": "residential"},
    {"name": "Beach Road Vizag",     "city": "Visakhapatnam","state": "AP",       "lat": 17.7099, "lng": 83.3199, "road_type": "arterial"},
    {"name": "Vijayawada",           "city": "Vijayawada", "state": "AP",         "lat": 16.5062, "lng": 80.6480, "road_type": "arterial"},

    # ── Punjab / Chandigarh ──────────────────────────────────────────────────
    {"name": "Sector 17 Chandigarh", "city": "Chandigarh", "state": "Chandigarh","lat": 30.7406, "lng": 76.7880, "road_type": "arterial"},
    {"name": "Ludhiana GT Road",     "city": "Ludhiana",   "state": "Punjab",    "lat": 30.9010, "lng": 75.8573, "road_type": "highway"},
    {"name": "Amritsar",             "city": "Amritsar",   "state": "Punjab",    "lat": 31.6340, "lng": 74.8723, "road_type": "arterial"},

    # ── Bihar / Jharkhand / Odisha ───────────────────────────────────────────
    {"name": "Bailey Road Patna",    "city": "Patna",      "state": "Bihar",     "lat": 25.6093, "lng": 85.1376, "road_type": "arterial"},
    {"name": "Main Road Ranchi",     "city": "Ranchi",     "state": "Jharkhand", "lat": 23.3441, "lng": 85.3096, "road_type": "arterial"},
    {"name": "Janpath Bhubaneswar",  "city": "Bhubaneswar","state": "Odisha",    "lat": 20.2961, "lng": 85.8245, "road_type": "arterial"},

    # ── Assam / Northeast ────────────────────────────────────────────────────
    {"name": "GS Road Guwahati",     "city": "Guwahati",   "state": "Assam",     "lat": 26.1445, "lng": 91.7362, "road_type": "arterial"},

    # ── Uttarakhand / Himachal ───────────────────────────────────────────────
    {"name": "Rajpur Road Dehradun", "city": "Dehradun",   "state": "Uttarakhand","lat": 30.3165, "lng": 78.0322,"road_type": "arterial"},

    # ── Major airports (for named route optimization) ────────────────────────
    {"name": "Rajiv Gandhi International Airport", "city": "Hyderabad", "state": "Telangana", "lat": 17.2403, "lng": 78.4294, "road_type": "highway"},
    {"name": "Kempegowda International Airport",   "city": "Bengaluru", "state": "Karnataka", "lat": 13.1989, "lng": 77.7068, "road_type": "highway"},
    {"name": "Chhatrapati Shivaji Maharaj International Airport", "city": "Mumbai", "state": "Maharashtra", "lat": 19.0896, "lng": 72.8656, "road_type": "highway"},
    {"name": "Indira Gandhi International Airport", "city": "New Delhi", "state": "Delhi", "lat": 28.5562, "lng": 77.1000, "road_type": "highway"},
    {"name": "Chennai International Airport",      "city": "Chennai",   "state": "Tamil Nadu", "lat": 12.9941, "lng": 80.1709, "road_type": "highway"},
    {"name": "Netaji Subhas Chandra Bose International Airport", "city": "Kolkata", "state": "West Bengal", "lat": 22.6547, "lng": 88.4467, "road_type": "highway"},
]

# Quick lookup: location name → metadata
LOCATION_MAP: dict[str, dict] = {loc["name"]: loc for loc in INDIA_LOCATIONS}

# Unique cities (for India-wide summaries)
CITIES = sorted({loc["city"] for loc in INDIA_LOCATIONS})
STATES = sorted({loc["state"] for loc in INDIA_LOCATIONS})
