# Flowcast Traffic API

A real-time traffic monitoring API with Google Maps live traffic fallback, PostgreSQL persistence, and traffic heatmap support. Built with FastAPI, PostgreSQL, and WebSocket streaming for frontend-friendly live updates.

---

## 🚀 Features

### Core Functionality
- **Real-Time Traffic Snapshot**: Current traffic conditions with a single API call
- **Google Maps Live Traffic**: Optional live traffic data from Google Maps Distance Matrix when `origin` and `destination` are provided
- **Dummy Traffic Fallback**: Automatic dummy data generation when live traffic data is unavailable
- **Traffic History Storage**: PostgreSQL persistence for traffic snapshots and historical review
- **Traffic History Queries**: Filter stored traffic history by location and limit result size
- **WebSocket Streaming**: Push-based traffic updates every 5 seconds via `/traffic/ws`

### Heatmap & Visualization
- **Heatmap Data API**: `/traffic/heatmap` returns intensity points for map overlays
- **Hotspot Detection**: `/traffic/heatmap/hotspots` returns top congestion hotspot locations
- **City Summary**: `/traffic/heatmap/summary` computes congestion statistics and location rankings

### Developer Features
- **RESTful API**: Simple, documented endpoints for traffic and heatmap data
- **Swagger/OpenAPI Documentation**: Interactive API docs available at `/docs`
- **CORS Support**: Frontend-friendly cross-origin access enabled
- **Modular Architecture**: Clean separation between routers, services, and database setup

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
Returns API status and available routes.

### Traffic Data
```
GET /traffic
```
Get a live traffic snapshot. When `origin` and `destination` are provided and a valid `GOOGLE_MAPS_API_KEY` is configured, live Google Maps traffic data is returned. Otherwise, dummy traffic data is served.

```
GET /traffic/history
```
Read stored traffic snapshots from PostgreSQL with optional location filtering.

```
GET /traffic/dummy
```
Return randomized dummy traffic data without persisting to the database.

### WebSocket
```
WS /traffic/ws
```
Real-time traffic updates streamed every 5 seconds.

**Example WebSocket subscription:**
```javascript
const ws = new WebSocket('ws://localhost:8000/traffic/ws');
ws.onmessage = (event) => {
  const trafficData = JSON.parse(event.data);
  console.log('Traffic Update:', trafficData);
};
```

### Heatmap Endpoints

```
GET /traffic/heatmap
```
Retrieve heatmap intensity points for map visualization.

Query Parameters:
- `hours` (int, 1-24): traffic history window to include
- `congestion_filter` (low|medium|high): optional filter
- `min_intensity` (float, 0.0-1.0): minimum intensity threshold
- `limit` (int, 1-1000): maximum number of points returned

```
GET /traffic/heatmap/hotspots
```
Get the top congestion hotspots in the current dataset.

```
GET /traffic/heatmap/summary
```
Get a city-wide congestion summary with counts, average intensity, and best/worst locations.

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
| `congestion_level` | String | low \| moderate \| high \| very_high |
| `speed_kmh` | Float | Average speed in km/h |
| `travel_time_mins` | Float | Travel time estimate in minutes |
| `timestamp` | DateTime | Traffic observation timestamp |

---

## 📁 Project Structure

```
flowcast-backend/
├── app/
│   ├── models/
│   │   └── predictor.py          # Heatmap and traffic models
│   ├── routes/
│   │   └── heatmap.py            # Traffic heatmap endpoints
│   ├── database.py               # Database configuration
│   └── __init__.py
├── routers/
│   └── traffic.py                # Core traffic router
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

### Get Dummy Traffic Data
```bash
curl http://localhost:8000/traffic/dummy
```

### Get Stored Traffic History
```bash
curl "http://localhost:8000/traffic/history?limit=20&location=Delhi"
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

### Get Heatmap Data
```bash
curl "http://localhost:8000/traffic/heatmap?hours=1&limit=200"
```

### Get Heatmap Hotspots
```bash
curl http://localhost:8000/traffic/heatmap/hotspots
```

### Get Heatmap Summary
```bash
curl http://localhost:8000/traffic/heatmap/summary
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
