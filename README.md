# FlowCast Backend

Real-time traffic prediction and monitoring API for India — built with FastAPI, PostgreSQL, Redis, and WebSockets.

![FlowCast Architecture](flowcast_architecture.png)

---

## Table of Contents

- [Overview](#overview)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
  - [Health](#health)
  - [Authentication](#authentication)
  - [Traffic Data](#traffic-data)
  - [ETA Calculation](#eta-calculation)
  - [Analytics](#analytics)
  - [Heatmap](#heatmap)
  - [Notifications](#notifications)
  - [Route Optimization](#route-optimization)
  - [Commute Planner](#commute-planner)
  - [Favorite Locations](#favorite-locations)
  - [User Preferences](#user-preferences)
  - [Trip History](#trip-history)
  - [Departure Alerts](#departure-alerts)
  - [Carbon Footprint](#carbon-footprint)
  - [India Traffic](#india-traffic)
  - [India Districts (WebSocket)](#india-districts-websocket)
  - [Area Prediction](#area-prediction)
  - [Admin](#admin)
- [WebSocket Endpoints](#websocket-endpoints)
- [Background Services](#background-services)
- [Database Schema](#database-schema)
- [Rate Limiting](#rate-limiting)
- [Google OAuth Setup](#google-oauth-setup)
- [Running Tests](#running-tests)
- [Project Structure](#project-structure)

---

## Overview

FlowCast is a production-grade backend API that provides:

- **Real-time traffic monitoring** across India (766 districts, major cities)
- **ML-powered congestion prediction** using scikit-learn
- **Live ETA calculation** with congestion-aware speed adjustment
- **Route optimization** via Google Maps Directions API
- **Departure alert system** with WebSocket push notifications
- **Carbon footprint calculator** for mode comparison
- **Admin dashboard** with system health monitoring

---

## Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI 0.110+ with uvicorn |
| Database | PostgreSQL 14+ via SQLAlchemy ORM |
| Cache / Rate Limiting | Redis (fallback: in-memory) |
| Auth | JWT (HS256) + Google OAuth 2.0 |
| ML / Prediction | scikit-learn (RandomForest / LinearRegression) |
| Real-time | WebSockets (FastAPI native) |
| External APIs | Google Maps Directions, TomTom Traffic, OpenRouteService |
| Python | 3.12 |

---

## Architecture

```
CLIENT LAYER
  Web Browser · React SPA · Mobile App · WebSocket Clients · Admin Dashboard
        │
        ▼
API GATEWAY (FastAPI / uvicorn)
  Rate Limiter · CORS · JWT Auth · Google OAuth · WebSocket Manager
        │
        ▼
ROUTE MODULES  /api/v1/...
  /auth  /traffic  /eta  /analytics  /heatmap  /notifications
  /routes  /commute  /favorites  /preferences  /trips  /alerts
  /eco  /india  /prediction  /admin
        │
        ▼
SERVICE LAYER
  AuthService · ETAService · RouteService · NotificationService
  HeatmapService · AlertService · PredictionService · RealtimeCollector
  DistrictCollector · ConnectionManager · CityAliases
        │
        ▼
DATA LAYER
  PostgreSQL (traffic_data DB)  ·  Redis Cache  ·  SQLAlchemy ORM
        │
        ▼
EXTERNAL SERVICES
  Google Maps API · Google OAuth 2.0 · TomTom Traffic · OpenRouteService
```

The architecture diagram (`flowcast_architecture.png`) provides a full visual breakdown of all layers and components.

---

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 14+ running locally
- Redis (optional — falls back to in-memory rate limiting)
- Google Maps API key (for route optimization)

### 1. Clone and install

```bash
git clone https://github.com/your-org/flowcast-backend.git
cd flowcast-backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

### 2. Configure environment

Copy and edit the environment file:

```bash
cp .env.example .env
# Edit .env with your database credentials and API keys
```

### 3. Create the database

```sql
-- In psql:
CREATE DATABASE "traffic-data";
```

### 4. Run the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On first startup FlowCast will:
- Run schema migrations (UUID column upgrades)
- Create all tables via `create_all()`
- Seed the default admin account (`admin@flowcast.in` / `Admin@1234`)
- Start background monitors (congestion, departure alerts, India traffic, district collector)

### 5. Open the docs

- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)

**Quick auth flow in Swagger:**
1. `POST /api/v1/auth/register` — create an account
2. `POST /api/v1/auth/login` — copy `access_token`
3. Click **Authorize** (lock icon, top-right) → paste token → **Authorize**
4. All protected endpoints are now unlocked

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | JWT signing key (32+ random bytes) |
| `GOOGLE_MAPS_DIRECTIONS_API_KEY` | Yes | Google Maps Directions API key |
| `GOOGLE_CLIENT_ID` | No | Google OAuth 2.0 Client ID |
| `GOOGLE_CLIENT_SECRET` | No | Google OAuth 2.0 Client Secret |
| `GOOGLE_REDIRECT_URI` | No | OAuth callback URL (must match Google Console) |
| `TOMTOM_API_KEY` | No | TomTom Traffic API key (2500 req/day free) |
| `ORS_API_KEY` | No | OpenRouteService API key (free) |
| `REDIS_URL` | No | Redis connection string (default: `redis://localhost:6379`) |
| `ADMIN_EMAIL` | No | Admin account email (default: `admin@flowcast.in`) |
| `ADMIN_PASSWORD` | No | Admin account password (default: `Admin@1234`) |
| `APP_ENV` | No | `development` or `production` |
| `DEBUG` | No | `True` / `False` |

---

## API Reference

All endpoints are prefixed with `/api/v1`. All timestamps are returned in **IST (Asia/Kolkata, UTC+5:30)**.

### Health

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | No | Liveness check |
| GET | `/health` | No | Health status |

---

### Authentication

Base path: `/api/v1/auth`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | No | Register a new account |
| POST | `/auth/login` | No | Email/password login — returns JWT |
| GET | `/auth/me` | JWT | Get current user profile |
| PUT | `/auth/me` | JWT | Update full name |
| POST | `/auth/change-password` | JWT | Change password |
| DELETE | `/auth/me` | JWT | Delete account |
| GET | `/auth/google/login` | No | Google OAuth sign-in page |
| GET | `/auth/google/callback` | No | OAuth callback — displays token |
| POST | `/auth/google/token` | No | Verify Google ID token from frontend |

**Register request:**
```json
{
  "email": "user@example.com",
  "full_name": "Ravi Kumar",
  "password": "SecurePass1"
}
```

**Login response:**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": {
    "id": "<uuid>",
    "email": "user@example.com",
    "full_name": "Ravi Kumar",
    "is_admin": false,
    "auth_provider": "local"
  }
}
```

Password rules: minimum 8 characters, at least 1 uppercase letter and 1 digit.

---

### Traffic Data

Base path: `/api/v1/traffic`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/traffic/records` | JWT | List traffic records (paginated, filterable) |
| POST | `/traffic/records` | JWT | Submit a single traffic record |
| POST | `/traffic/records/bulk` | JWT | Bulk insert up to 50 records |
| GET | `/traffic/records/{id}` | JWT | Get a specific record |
| DELETE | `/traffic/records/{id}` | JWT | Delete a record |
| POST | `/traffic/predict` | JWT | Predict congestion for a location |
| GET | `/traffic/anomalies` | JWT | Detect anomalous traffic patterns |
| GET | `/traffic/export/csv` | JWT | Export records as CSV |
| GET | `/traffic/incidents` | No | Active traffic incidents |
| POST | `/traffic/incidents` | JWT | Report a new incident |

**Submit record request:**
```json
{
  "location": "Hitech City",
  "latitude": 17.4486,
  "longitude": 78.3908,
  "vehicle_count": 245,
  "average_speed": 32.5,
  "congestion_level": "medium",
  "road_type": "arterial"
}
```

**Query parameters for `/traffic/records`:**
- `location` — filter by location name (partial match)
- `congestion_level` — `low`, `medium`, or `high`
- `skip`, `limit` — pagination

---

### ETA Calculation

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/traffic/eta` | No | Single-location real-time ETA |
| POST | `/traffic/eta/batch` | No | Batch ETA for multiple locations |
| GET | `/traffic/eta/locations` | No | List all monitored Hyderabad locations |

**Query parameters for single ETA:**
- `location` — Hyderabad area name (e.g. `Hitech City`, `Banjara Hills`)
- `distance_km` — trip distance in km (max 500)
- `mode` — `driving` (default), `walking`, `transit`

**ETA response:**
```json
{
  "location": "Hitech City",
  "distance_km": 10,
  "mode": "driving",
  "congestion_level": "medium",
  "average_speed_kmh": 28.4,
  "eta_minutes": 21,
  "eta_range": { "min": 19, "max": 24 },
  "confidence": "high",
  "last_updated": "2026-05-27T14:32:10+05:30"
}
```

---

### Analytics

Base path: `/api/v1/analytics`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/analytics/trends` | JWT | Congestion trend data (24 h or custom range) |
| GET | `/analytics/snapshot` | JWT | Current network-wide traffic snapshot |
| GET | `/analytics/city-health` | JWT | City health score (0–100) |
| GET | `/analytics/timelapse` | JWT | Hourly heatmap frames for animation |

---

### Heatmap

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/heatmap` | JWT | Coordinate-level congestion intensity grid |

Returns coordinate points with `lat`, `lng`, and `intensity` (0.0–1.0) — suitable for overlaying on Leaflet, Google Maps, or Mapbox.

---

### Notifications

Base path: `/api/v1/notifications`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/notifications` | JWT | List notifications (paginated) |
| PUT | `/notifications/{id}/read` | JWT | Mark one notification as read |
| PUT | `/notifications/read-all` | JWT | Mark all notifications as read |
| DELETE | `/notifications/{id}` | JWT | Delete a notification |
| GET | `/notifications/stats` | JWT | Notification statistics |

**Stats response:**
```json
{
  "total": 12,
  "unread": 4,
  "read_count": 8,
  "unread_critical": 1,
  "severity_breakdown": { "low": 3, "medium": 7, "high": 1, "critical": 1 },
  "type_breakdown": { "congestion_alert": 5, "system": 7 }
}
```

---

### Route Optimization

Base path: `/api/v1/routes`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/routes/optimize` | JWT | Optimize a route via Google Maps + live traffic |
| GET | `/routes/saved` | JWT | List saved routes |
| POST | `/routes/saved` | JWT | Save a route |
| DELETE | `/routes/saved/{id}` | JWT | Delete a saved route |
| POST | `/routes/saved/{id}/share` | JWT | Generate a shareable link |
| GET | `/routes/share/{token}` | No | View a shared route |

Coordinates must be within India: **lat 6.0–37.5, lng 68.0–97.5**.

**Optimize request:**
```json
{
  "origin": { "lat": 17.4486, "lng": 78.3908 },
  "destination": { "lat": 17.3850, "lng": 78.4867 },
  "mode": "driving",
  "alternatives": true
}
```

---

### Commute Planner

Base path: `/api/v1/commute`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/commute/forecast` | JWT | 24-hour rush-hour forecast for a location |
| GET | `/commute/best-departure` | JWT | Optimal departure window to avoid congestion |
| GET | `/commute/score` | JWT | Commute friendliness score (0–100) |

---

### Favorite Locations

Base path: `/api/v1/favorites`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/favorites` | JWT | List bookmarked locations |
| POST | `/favorites` | JWT | Add a favorite location |
| DELETE | `/favorites/{id}` | JWT | Remove a favorite |
| GET | `/favorites/{id}/status` | JWT | Live traffic status for a favorite |

---

### User Preferences

Base path: `/api/v1/user/preferences`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/user/preferences` | JWT | Get current preferences |
| PUT | `/user/preferences` | JWT | Update preferences |

**Preferences schema:**
```json
{
  "notifications_enabled": true,
  "preferred_mode": "driving",
  "quiet_hours_start": "22:00",
  "quiet_hours_end": "07:00",
  "congestion_threshold": "high",
  "language": "en"
}
```

---

### Trip History

Base path: `/api/v1/trips`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/trips` | JWT | Log a completed trip |
| GET | `/trips` | JWT | List trip history (paginated) |
| GET | `/trips/stats` | JWT | Personal commute statistics |
| DELETE | `/trips/{id}` | JWT | Delete a trip record |

**Log trip request:**
```json
{
  "origin": "Hitech City",
  "destination": "Banjara Hills",
  "distance_km": 12.5,
  "duration_minutes": 28,
  "mode": "driving",
  "congestion_level": "medium",
  "notes": "Usual morning route"
}
```

**Stats response includes:**
- Total trips, total distance, average duration
- Trips in the last 7 and 30 days
- ETA accuracy stats
- Distance breakdown (min, max, average)
- Most-used travel mode
- First and last trip timestamps

**Pagination fields:**
```json
{
  "trips": [...],
  "total": 45,
  "returned": 10,
  "has_more": true
}
```

---

### Departure Alerts

Base path: `/api/v1/alerts`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/alerts/departure` | JWT | List all departure alerts |
| POST | `/alerts/departure` | JWT | Create a departure alert |
| PUT | `/alerts/departure/{id}/toggle` | JWT | Enable / disable an alert |
| DELETE | `/alerts/departure/{id}` | JWT | Delete an alert |

When an alert fires, a **WebSocket push** is sent to all connected sessions for that user.

**Create alert request:**
```json
{
  "route_name": "Home to Office",
  "origin": "Miyapur",
  "destination": "Hitech City",
  "departure_time": "08:30",
  "advance_notice_minutes": 15,
  "days_of_week": ["monday", "tuesday", "wednesday", "thursday", "friday"]
}
```

Duplicate alerts (same user + route name + departure time) are rejected with `409 Conflict`.

---

### Carbon Footprint

Base path: `/api/v1/eco`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/eco/footprint` | JWT | CO₂ footprint for a specific trip |
| GET | `/eco/compare` | JWT | Side-by-side mode comparison |
| GET | `/eco/tips` | JWT | Personalised eco tips |

**Footprint response:**
```json
{
  "distance_km": 15,
  "mode": "car",
  "co2_emissions": "2.55 kg",
  "trees_to_offset": 0.14,
  "equivalent_km_of_flying": 10.2,
  "tip": "Switching to public transit saves 2.3 kg CO₂ on this route."
}
```

**Mode comparison** covers: Car, Motorcycle, Bus, Metro, Cycling, Walking — each with:
- CO₂ emissions (shown in grams or kg)
- Calories burned
- Savings vs. driving (absolute and percentage)
- Trees required to offset annually

---

### India Traffic

Base path: `/api/v1/india`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/india/overview` | No | National traffic summary |
| GET | `/india/cities` | No | Traffic status for all major cities |
| GET | `/india/cities/{city}` | No | Single city traffic detail |
| GET | `/india/heatmap` | No | State-level congestion heatmap data |
| GET | `/india/hotspots` | No | Top congestion hotspots nationwide |
| GET | `/india/health` | No | City health scores ranked |

Covers 50+ cities across all Indian states. Data is refreshed every 60 seconds via TomTom Traffic API.

---

### India Districts (WebSocket)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/india/districts` | No | All 766 districts (REST snapshot) |
| GET | `/india/districts/{district}` | No | Single district traffic data |
| WS | `/india/ws/districts` | No | Live district updates via WebSocket |

**WebSocket connection:**
```javascript
const ws = new WebSocket("ws://localhost:8000/api/v1/india/ws/districts");
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // data: { districts: [...], timestamp: "2026-05-27T14:32:10+05:30" }
};
```

District data is broadcast every 60 seconds with live congestion scores for all 766 Indian districts.

---

### Area Prediction

Base path: `/api/v1/prediction`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/prediction/forecast` | JWT | 12-hour congestion forecast for an area |
| GET | `/prediction/hourly-pattern` | JWT | Historical hourly traffic pattern |
| GET | `/prediction/compare` | JWT | Side-by-side multi-area comparison |

The prediction service uses a scikit-learn model trained on historical traffic records. Features include: hour of day, vehicle count, road type, and day of week.

---

### Admin

Base path: `/api/v1/admin` — requires `is_admin: true`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/admin/users` | Admin | List all users |
| GET | `/admin/users/{id}` | Admin | Get user detail |
| PUT | `/admin/users/{id}/toggle` | Admin | Activate / deactivate user |
| DELETE | `/admin/users/{id}` | Admin | Delete user account |
| GET | `/admin/stats` | Admin | System-wide statistics |
| GET | `/admin/db/health` | Admin | Database health check |
| POST | `/admin/db/vacuum` | Admin | Run VACUUM ANALYZE |
| GET | `/admin/traffic/records` | Admin | All traffic records (unfiltered) |
| DELETE | `/admin/traffic/records/{id}` | Admin | Delete any traffic record |

Default admin credentials (change immediately in production):
- Email: `admin@flowcast.in`
- Password: `Admin@1234`

---

## WebSocket Endpoints

### User notifications

```
ws://localhost:8000/api/v1/ws/{user_id}
```

Receives real-time pushes for:
- High-congestion alerts on saved routes
- Departure alert fires
- System notifications

### India district live feed

```
ws://localhost:8000/api/v1/india/ws/districts
```

Public endpoint. Broadcasts district-level traffic data for all 766 Indian districts every 60 seconds.

---

## Background Services

Four asyncio tasks run continuously after startup:

| Service | Interval | Description |
|---|---|---|
| Congestion Monitor | 60 s | Checks all saved routes for high congestion; pushes WebSocket notifications |
| Departure Alert Monitor | 60 s | Fires scheduled departure reminders N minutes before departure time |
| India Traffic Collector | 60 s | Polls TomTom API for city-level traffic across India |
| District Collector | 60 s | Aggregates district-level data and broadcasts via WebSocket |

---

## Database Schema

### Core tables

| Table | Primary Key | Description |
|---|---|---|
| `users` | UUID | Accounts (local + Google OAuth) |
| `traffic_records` | Integer + UUID col | Raw traffic observations |
| `prediction_results` | Integer + UUID col | ML prediction outputs |
| `incidents` | Integer + UUID col | Reported traffic incidents |
| `saved_routes` | UUID | User-saved optimized routes |
| `notifications` | UUID | User notification inbox |
| `favorite_locations` | UUID | Bookmarked places |
| `user_preferences` | UUID | Per-user settings |
| `trip_history` | UUID | Logged journeys |
| `departure_alerts` | UUID | Scheduled departure reminders |
| `route_share_tokens` | UUID | Shareable route links |

### Connection pool

```
pool_size=10  max_overflow=20  pool_pre_ping=True
```

### Startup migrations

On every startup the server checks if legacy `INTEGER` primary key columns need to be upgraded to `UUID`. Each migration drops and recreates only the affected tables. Production deployments should use Alembic.

---

## Rate Limiting

- **100 requests per minute per IP**
- Backed by Redis sliding window (falls back to in-memory if Redis is unavailable)
- Returns `429 Too Many Requests` when exceeded

---

## Google OAuth Setup

1. Go to [Google Cloud Console → APIs & Credentials](https://console.cloud.google.com/apis/credentials)
2. Create an **OAuth 2.0 Client ID** (Web application type)
3. Set **Authorized JavaScript origins**: `http://localhost:8000`
4. Set **Authorized redirect URIs**: `http://localhost:8000/api/v1/auth/google/callback`
5. Add test users in the **OAuth consent screen** (required while the app is in "Testing" mode)
6. Copy the Client ID and Client Secret into `.env`

**Sign-in flow:**
1. Open `GET /api/v1/auth/google/login` **in a browser** (not Swagger — Google OAuth blocks CORS fetch)
2. Click "Sign in with Google"
3. After consent, the callback page displays your `access_token`
4. Use the token in `Authorization: Bearer <token>` header for all protected endpoints

Existing local accounts with the same email are automatically linked to Google on first OAuth login.

---

## Running Tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v
```

Test configuration is in `pytest.ini`.

---

## Project Structure

```
flowcast-backend/
├── app/
│   ├── core/
│   │   └── rate_limiter.py          # Redis-backed rate limiting
│   ├── models/                      # SQLAlchemy ORM models
│   │   ├── user.py
│   │   ├── predictor.py             # TrafficRecord, Incident, PredictionResult
│   │   ├── route.py                 # SavedRoute
│   │   ├── notification.py
│   │   ├── favorite.py
│   │   ├── preferences.py
│   │   ├── trip.py
│   │   ├── alert.py
│   │   └── share.py
│   ├── routes/                      # FastAPI routers (one per feature area)
│   ├── schemas/                     # Pydantic request/response models
│   ├── services/                    # Business logic
│   │   ├── auth_service.py          # Password hashing, JWT issuance
│   │   ├── eta_service.py           # Congestion-aware ETA calculation
│   │   ├── route_service.py         # Google Maps integration
│   │   ├── notification_service.py  # Notification delivery + congestion checks
│   │   ├── heatmap_service.py       # Intensity scoring
│   │   ├── alert_service.py         # Departure alert firing
│   │   ├── prediction_service.py    # ML model inference
│   │   ├── realtime_collector.py    # India traffic polling (TomTom)
│   │   ├── district_collector.py    # 766-district aggregation + WS broadcast
│   │   ├── connection_manager.py    # WebSocket session management
│   │   └── city_aliases.py          # India location name normalisation
│   ├── database.py                  # Engine, session factory, migrations, seeding
│   └── main.py                      # App factory, router registration, lifespan
├── tests/
├── generate_diagram.py              # Architecture diagram generator (matplotlib)
├── flowcast_architecture.png        # Generated system architecture diagram
├── requirements.txt
├── .env
└── README.md
```
