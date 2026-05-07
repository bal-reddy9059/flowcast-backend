# Flowcast Traffic API

A real-time traffic monitoring system with intelligent traffic prediction, incident detection, and network-wide analytics. Built with FastAPI, PostgreSQL, and WebSocket support for live updates.

---

## 🚀 Features

### Core Functionality
- **Real-Time Traffic Monitoring**: Live traffic data capture and monitoring from multiple sources
- **Google Maps Integration**: Seamless integration with Google Maps API for traffic data
- **WebSocket Push Updates**: Real-time bidirectional communication for live traffic updates
- **Traffic Prediction**: ML-based traffic forecasting with confidence scoring
- **Incident Detection**: Automatic detection and tracking of traffic incidents (accidents, road work, etc.)
- **Historical Data**: PostgreSQL-based persistence for historical analysis and auditing

### Analytics & Insights
- **Network Snapshot**: City-wide congestion overview across all monitored locations
- **Location Summary**: Detailed statistics and active incidents for specific locations
- **Congestion Trends**: Hourly congestion patterns for trend analysis and forecasting
- **Vehicle Count Analysis**: Real-time and historical vehicle count metrics
- **Speed & Travel Time Metrics**: Real-world traffic speed and estimated travel times

### Developer Features
- **RESTful API**: Clean, well-documented REST endpoints
- **Swagger/OpenAPI Documentation**: Interactive API documentation at `/docs`
- **CORS Support**: Cross-origin requests enabled for frontend integration
- **Dummy Data Generation**: Built-in test data for development and testing
- **Modular Architecture**: Clean separation of concerns with models, routes, and services

---

## 🛠️ Technology Stack

| Component | Technology |
|-----------|-----------|
| **Framework** | FastAPI 0.136.1 |
| **Server** | Uvicorn |
| **Database** | PostgreSQL |
| **ORM** | SQLAlchemy |
| **Validation** | Pydantic |
| **Real-Time** | WebSocket |
| **Maps API** | Google Maps |

---

## 📋 Requirements

- Python 3.8+
- PostgreSQL 12+
- Google Maps API key (for live traffic data)

---

## ⚙️ Installation & Setup

### 1. Clone and Setup Virtual Environment
```bash
cd flowcast-backend
python -m venv venv
source venv/Scripts/activate  # On Windows
# or
source venv/bin/activate      # On macOS/Linux
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the project root:
```env
DATABASE_URL=postgresql://user:password@localhost:5432/flowcast
GOOGLE_MAPS_API_KEY=your_api_key_here
SECRET_KEY=your_secret_key
DEBUG=True
```

### 4. Initialize Database
```bash
python seed.py  # Run database initialization/seeding
```

### 5. Run the Server
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`

---

## 📡 API Endpoints

### Root Endpoint
```
GET /
```
Returns API information and available endpoints.

### Traffic Data
```
GET /traffic
```
Get real-time traffic snapshot.

```
GET /traffic/history
```
Get historical traffic records.

```
GET /traffic/dummy
```
Generate and retrieve dummy traffic data for testing.

### WebSocket
```
WS /traffic/ws
```
Real-time traffic updates via WebSocket connection.

**Example WebSocket subscription:**
```javascript
const ws = new WebSocket('ws://localhost:8000/traffic/ws');
ws.onmessage = (event) => {
  const trafficData = JSON.parse(event.data);
  console.log('Traffic Update:', trafficData);
};
```

### Analytics

#### Network Snapshot
```
GET /analytics/snapshot?hours=1
```
Network-wide congestion view across all monitored locations.

**Query Parameters:**
- `hours` (int): Look-back window (1-24 hours, default: 1)

**Response:**
```json
{
  "timestamp": "2026-05-07T10:30:00Z",
  "total_locations": 42,
  "average_congestion": "moderate",
  "high_congestion_count": 8,
  "locations": [...]
}
```

#### Location Summary
```
GET /analytics/location?location=Main Street&hours=1
```
Aggregated statistics and active incidents for a specific location.

**Query Parameters:**
- `location` (str): Location name (required)
- `hours` (int): Look-back window (1-24 hours, default: 1)

**Response:**
```json
{
  "location": "Main Street",
  "vehicle_count_avg": 45,
  "speed_avg_kmh": 35.5,
  "congestion_level": "moderate",
  "incidents": [...]
}
```

#### Congestion Trend
```
GET /analytics/trend?location=Main Street&intervals=6
```
Hourly congestion trend for chart rendering and analysis.

**Query Parameters:**
- `location` (str): Location name (required)
- `intervals` (int): Number of hourly buckets (2-24, default: 6)

**Response:**
```json
{
  "location": "Main Street",
  "trend": [
    {"hour": 0, "congestion_level": "low", "vehicle_count": 20},
    {"hour": 1, "congestion_level": "medium", "vehicle_count": 45},
    ...
  ]
}
```

---

## 🗄️ Database Schema

### TrafficRecord
Core table for storing traffic observations.

| Field | Type | Description |
|-------|------|-------------|
| `id` | Integer | Primary key |
| `location` | String | Location name |
| `latitude` | Float | GPS latitude |
| `longitude` | Float | GPS longitude |
| `vehicle_count` | Integer | Number of vehicles detected |
| `average_speed` | Float | Average speed (km/h) |
| `congestion_level` | String | low \| medium \| high |
| `road_type` | String | Road category |
| `timestamp` | DateTime | Data collection time |
| `created_at` | DateTime | Record creation time |

### PredictionResult
Traffic prediction records with confidence metrics.

| Field | Type | Description |
|-------|------|-------------|
| `id` | Integer | Primary key |
| `location` | String | Target location |
| `predicted_congestion` | String | Forecasted congestion level |
| `confidence_score` | Float | Model confidence (0-1) |
| `prediction_for` | DateTime | Prediction target time |
| `model_version` | String | ML model version |
| `created_at` | DateTime | Prediction timestamp |

### Incident
Detected traffic incidents (accidents, road work, etc.).

| Field | Type | Description |
|-------|------|-------------|
| `id` | Integer | Primary key |
| `location` | String | Incident location |
| `latitude` | Float | GPS latitude |
| `longitude` | Float | GPS longitude |
| `incident_type` | String | accident \| road_work \| weather \| other |
| `severity` | String | low \| medium \| high \| critical |
| `description` | String | Incident details |
| `resolved_at` | DateTime | Resolution timestamp |
| `created_at` | DateTime | Detection timestamp |

---

## 📁 Project Structure

```
flowcast-backend/
├── app/
│   ├── models/
│   │   └── predictor.py          # Database models & schemas
│   ├── routes/
│   │   ├── traffic.py            # Traffic endpoints
│   │   └── analytics.py          # Analytics endpoints
│   ├── services/
│   │   └── realtime.py           # Real-time analysis service
│   ├── database.py               # Database configuration
│   └── __init__.py
├── routers/
│   └── traffic.py                # Traffic router (legacy)
├── services/
│   └── traffic_service.py        # Traffic business logic
├── main.py                        # FastAPI app entry point
├── models.py                      # ORM models
├── schemas.py                     # Pydantic schemas
├── database.py                    # Database setup
├── seed.py                        # Database seeding
├── requirements.txt               # Python dependencies
└── README.md                      # This file
```

---

## 🔄 Traffic Congestion Classification

The system classifies congestion based on vehicle count and average speed:

### Vehicle Count Thresholds
- **Low**: 0-30 vehicles
- **Medium**: 31-80 vehicles
- **High**: 81+ vehicles

### Speed Thresholds
- **Low**: 61+ km/h
- **Medium**: 26-60 km/h
- **High**: 0-25 km/h

---

## 🚀 Running the Application

### Development Mode
```bash
uvicorn main:app --reload
```
The API will auto-reload on file changes.

### Production Mode
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Access Documentation
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## 📊 Example Usage

### Fetch Real-Time Traffic
```bash
curl http://localhost:8000/traffic
```

### Get Analytics Snapshot
```bash
curl "http://localhost:8000/analytics/snapshot?hours=2"
```

### Get Location Statistics
```bash
curl "http://localhost:8000/analytics/location?location=Main%20Street&hours=1"
```

### Subscribe to Live Updates (WebSocket)
```python
import asyncio
import websockets
import json

async def listen():
    uri = "ws://localhost:8000/traffic/ws"
    async with websockets.connect(uri) as websocket:
        while True:
            data = await websocket.recv()
            print(json.loads(data))

asyncio.run(listen())
```

---

## 🔧 Configuration

Edit `main.py` to customize:
- CORS allowed origins
- API title and description
- Database connection settings
- WebSocket parameters

---

## 📝 License

This project is licensed under the MIT License.

---

## 🤝 Contributing

Contributions are welcome! Please ensure:
1. Code follows PEP 8 style guidelines
2. All endpoints are documented
3. Database migrations are included
4. Tests pass before submission

---

## 📧 Support

For issues or questions, please open an issue in the repository.

---

**Last Updated**: May 2026  
**API Version**: 1.0.0
