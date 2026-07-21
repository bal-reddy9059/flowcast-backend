"""
Run once to (re)create stations + crowd_logs tables and seed all stations.
  python app/database/crowd_seed.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from app.crowd_db import create_crowd_pool

STATIONS = [
    # ── Bangalore ──────────────────────────────────────────────────────────────
    {
        "id": "341fed3e-210b-5aba-9846-149f991b9a10",
        "name": "KSR Bangalore City Railway Station",
        "type": "railway", "city": "Bangalore", "state": "Karnataka",
        "capacity": 12000, "peak_hours": "6-10 AM, 4-9 PM",
        "lat": 12.9775, "lng": 77.5713,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    {
        "id": "feebf092-004f-5b71-b156-bb26c4533492",
        "name": "Majestic Bus Terminal",
        "type": "bus", "city": "Bangalore", "state": "Karnataka",
        "capacity": 5000, "peak_hours": "7-10 AM, 5-9 PM",
        "lat": 12.9767, "lng": 77.5713,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM"],
    },
    {
        "id": "5c36b0c7-3c47-59eb-908b-c77f2871d590",
        "name": "Shivajinagar Bus Stand",
        "type": "bus", "city": "Bangalore", "state": "Karnataka",
        "capacity": 3000, "peak_hours": "8-11 AM, 4-8 PM",
        "lat": 12.9850, "lng": 77.6010,
        "amenities": ["Waiting Hall", "Restrooms"],
    },
    # ── Hyderabad ──────────────────────────────────────────────────────────────
    {
        "id": "2683f4a8-0414-5c84-9f3a-6d876bc7fe01",
        "name": "Hyderabad Deccan Railway Station",
        "type": "railway", "city": "Hyderabad", "state": "Telangana",
        "capacity": 10000, "peak_hours": "6-10 AM, 4-9 PM",
        "lat": 17.3950, "lng": 78.4744,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    {
        "id": "b6596665-ae90-53e1-8ef5-7a707f470185",
        "name": "Mahatma Gandhi Bus Station",
        "type": "bus", "city": "Hyderabad", "state": "Telangana",
        "capacity": 8000, "peak_hours": "7-10 AM, 5-9 PM",
        "lat": 17.3850, "lng": 78.4867,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM"],
    },
    {
        "id": "86c7d3f0-7889-5d9a-8134-c1ed9ff24147",
        "name": "Secunderabad Junction",
        "type": "railway", "city": "Hyderabad", "state": "Telangana",
        "capacity": 15000, "peak_hours": "6-10 AM, 4-9 PM",
        "lat": 17.4399, "lng": 78.4983,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking", "Lounge"],
    },
    # ── Delhi ──────────────────────────────────────────────────────────────────
    {
        "id": "bc0d77d8-4c6c-531f-9b51-1399fb2a1f8c",
        "name": "New Delhi Railway Station",
        "type": "railway", "city": "Delhi", "state": "Delhi",
        "capacity": 50000, "peak_hours": "5-10 AM, 4-10 PM",
        "lat": 28.6420, "lng": 77.2193,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking", "Lounge", "Medical"],
    },
    {
        "id": "547b6e0b-97da-53bc-a501-a9fcff30f201",
        "name": "ISBT Kashmere Gate",
        "type": "bus", "city": "Delhi", "state": "Delhi",
        "capacity": 20000, "peak_hours": "6-10 AM, 4-9 PM",
        "lat": 28.6674, "lng": 77.2270,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    # ── Mumbai ─────────────────────────────────────────────────────────────────
    {
        "id": "b7cab3c4-08a2-5809-af85-db3d78059ca2",
        "name": "Chhatrapati Shivaji Maharaj Terminus",
        "type": "railway", "city": "Mumbai", "state": "Maharashtra",
        "capacity": 60000, "peak_hours": "7-11 AM, 5-10 PM",
        "lat": 18.9402, "lng": 72.8356,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking", "Lounge", "Medical"],
    },
    {
        "id": "e9525d9f-a685-584b-9d7c-1cd86ceee9ea",
        "name": "Mumbai Central Bus Depot",
        "type": "bus", "city": "Mumbai", "state": "Maharashtra",
        "capacity": 15000, "peak_hours": "7-10 AM, 5-9 PM",
        "lat": 18.9686, "lng": 72.8194,
        "amenities": ["Waiting Hall", "Restrooms", "ATM", "Food Court"],
    },
    # ── Chennai ────────────────────────────────────────────────────────────────
    {
        "id": "a00c4c52-54fe-5e4c-97ef-9514091e23fa",
        "name": "Chennai Central Railway Station",
        "type": "railway", "city": "Chennai", "state": "Tamil Nadu",
        "capacity": 30000, "peak_hours": "6-10 AM, 4-9 PM",
        "lat": 13.0827, "lng": 80.2707,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking", "Lounge"],
    },
    {
        "id": "732ff2f5-6945-5c86-b617-c4a12d5e86ac",
        "name": "CMBT Bus Terminal",
        "type": "bus", "city": "Chennai", "state": "Tamil Nadu",
        "capacity": 12000, "peak_hours": "7-10 AM, 5-9 PM",
        "lat": 13.0694, "lng": 80.2101,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM"],
    },
    # ── Kolkata ────────────────────────────────────────────────────────────────
    {
        "id": "eef08644-0e67-59b5-a528-b6ce499e39e9",
        "name": "Howrah Junction",
        "type": "railway", "city": "Kolkata", "state": "West Bengal",
        "capacity": 45000, "peak_hours": "6-10 AM, 4-9 PM",
        "lat": 22.5839, "lng": 88.3424,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking", "Lounge", "Medical"],
    },
    {
        "id": "8aae450d-df04-5a35-b5b5-9c262d349719",
        "name": "Esplanade Bus Terminus",
        "type": "bus", "city": "Kolkata", "state": "West Bengal",
        "capacity": 10000, "peak_hours": "7-10 AM, 5-9 PM",
        "lat": 22.5697, "lng": 88.3524,
        "amenities": ["Waiting Hall", "Restrooms", "ATM"],
    },
    # ── Pune ───────────────────────────────────────────────────────────────────
    {
        "id": "c7f296e8-1896-528c-bbfe-97fc97b4cfaf",
        "name": "Pune Junction",
        "type": "railway", "city": "Pune", "state": "Maharashtra",
        "capacity": 18000, "peak_hours": "7-10 AM, 5-9 PM",
        "lat": 18.5284, "lng": 73.8742,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    {
        "id": "8dd6641b-135a-59e6-892a-7ff6c6c988f4",
        "name": "Shivajinagar Bus Stand Pune",
        "type": "bus", "city": "Pune", "state": "Maharashtra",
        "capacity": 6000, "peak_hours": "8-11 AM, 4-8 PM",
        "lat": 18.5309, "lng": 73.8474,
        "amenities": ["Waiting Hall", "Restrooms", "ATM"],
    },
    # ── Ahmedabad ──────────────────────────────────────────────────────────────
    {
        "id": "b8d7431b-b668-50f4-8516-b76fc0260d75",
        "name": "Ahmedabad Junction",
        "type": "railway", "city": "Ahmedabad", "state": "Gujarat",
        "capacity": 20000, "peak_hours": "6-10 AM, 4-8 PM",
        "lat": 23.0225, "lng": 72.5714,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    {
        "id": "9c2d7ad2-9505-5c87-9349-216150f5d984",
        "name": "Geeta Mandir Bus Terminal",
        "type": "bus", "city": "Ahmedabad", "state": "Gujarat",
        "capacity": 8000, "peak_hours": "7-10 AM, 4-8 PM",
        "lat": 23.0204, "lng": 72.5987,
        "amenities": ["Waiting Hall", "Restrooms", "ATM", "Food Court"],
    },
    # ── Jaipur ─────────────────────────────────────────────────────────────────
    {
        "id": "a0905853-fe49-5f76-afae-61147ac51fcd",
        "name": "Jaipur Junction",
        "type": "railway", "city": "Jaipur", "state": "Rajasthan",
        "capacity": 16000, "peak_hours": "6-10 AM, 4-8 PM",
        "lat": 26.9196, "lng": 75.7878,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    {
        "id": "384ec47b-c0b2-5bd5-af5b-c35d4b44dcfd",
        "name": "Sindhi Camp Bus Stand",
        "type": "bus", "city": "Jaipur", "state": "Rajasthan",
        "capacity": 7000, "peak_hours": "7-10 AM, 4-8 PM",
        "lat": 26.9124, "lng": 75.7873,
        "amenities": ["Waiting Hall", "Restrooms", "ATM"],
    },
    # ── Lucknow ────────────────────────────────────────────────────────────────
    {
        "id": "c61c2295-2392-5fa6-9a7c-b87f9eb1ea56",
        "name": "Lucknow Charbagh Railway Station",
        "type": "railway", "city": "Lucknow", "state": "Uttar Pradesh",
        "capacity": 22000, "peak_hours": "6-10 AM, 4-9 PM",
        "lat": 26.8381, "lng": 80.9346,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking", "Lounge"],
    },
    {
        "id": "088e23e5-dc84-5637-8f4c-a19b57bf71fd",
        "name": "Lucknow Bus Terminal",
        "type": "bus", "city": "Lucknow", "state": "Uttar Pradesh",
        "capacity": 9000, "peak_hours": "7-10 AM, 4-8 PM",
        "lat": 26.8500, "lng": 80.9400,
        "amenities": ["Waiting Hall", "Restrooms", "ATM"],
    },
    # ── Surat ──────────────────────────────────────────────────────────────────
    {
        "id": "d7c2562a-4e4f-5fee-8a89-0b5fd6cd25e5",
        "name": "Surat Railway Station",
        "type": "railway", "city": "Surat", "state": "Gujarat",
        "capacity": 14000, "peak_hours": "6-10 AM, 4-8 PM",
        "lat": 21.2048, "lng": 72.8318,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    {
        "id": "f635de36-267e-56a5-9cbc-edf162f8f313",
        "name": "Surat Central Bus Terminal",
        "type": "bus", "city": "Surat", "state": "Gujarat",
        "capacity": 6000, "peak_hours": "7-10 AM, 4-8 PM",
        "lat": 21.1950, "lng": 72.8350,
        "amenities": ["Waiting Hall", "Restrooms", "ATM"],
    },
    # ── Bhopal ─────────────────────────────────────────────────────────────────
    {
        "id": "bd4ed181-821f-581b-8e01-e3c0cd3cf73d",
        "name": "Bhopal Junction",
        "type": "railway", "city": "Bhopal", "state": "Madhya Pradesh",
        "capacity": 12000, "peak_hours": "6-10 AM, 4-8 PM",
        "lat": 23.2688, "lng": 77.4121,
        "amenities": ["Waiting Hall", "Food Court", "Restrooms", "ATM", "Parking"],
    },
    {
        "id": "dd33af0d-6cd7-515a-a119-2f9cb6699b06",
        "name": "Bhopal ISBT Bus Terminal",
        "type": "bus", "city": "Bhopal", "state": "Madhya Pradesh",
        "capacity": 5000, "peak_hours": "7-10 AM, 4-8 PM",
        "lat": 23.2500, "lng": 77.4000,
        "amenities": ["Waiting Hall", "Restrooms", "ATM"],
    },
]


async def migrate():
    pool = await create_crowd_pool()
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS crowd_logs CASCADE")
        await conn.execute("DROP TABLE IF EXISTS stations CASCADE")
        print("[OK] Old tables dropped")

        await conn.execute("""
            CREATE TABLE stations (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name        VARCHAR(255) NOT NULL,
                type        VARCHAR(50)  NOT NULL CHECK (type IN ('bus', 'railway')),
                city        VARCHAR(100) NOT NULL,
                state       VARCHAR(100) NOT NULL,
                capacity    INTEGER NOT NULL,
                peak_hours  VARCHAR(100),
                lat         DECIMAL(9,6),
                lng         DECIMAL(9,6),
                amenities   TEXT[],
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        print("[OK] stations table created")

        await conn.execute("""
            CREATE TABLE crowd_logs (
                id           SERIAL PRIMARY KEY,
                station_id   UUID NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
                crowd_score  INTEGER NOT NULL,
                crowd_level  VARCHAR(20) NOT NULL,
                predicted_at TIMESTAMP DEFAULT NOW(),
                hour_of_day  INTEGER,
                day_of_week  INTEGER
            )
        """)
        await conn.execute("CREATE INDEX ON crowd_logs (station_id, predicted_at DESC)")
        print("[OK] crowd_logs table created")

        for s in STATIONS:
            await conn.execute(
                """
                INSERT INTO stations
                    (id, name, type, city, state, capacity, peak_hours, lat, lng, amenities)
                VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                s["id"], s["name"], s["type"], s["city"], s["state"],
                s["capacity"], s["peak_hours"], s["lat"], s["lng"], s["amenities"],
            )
            print(f"  [OK] {s['city']:12s} | {s['type']:8s} | {s['name']}")

    await pool.close()
    cities = len({s["city"] for s in STATIONS})
    print(f"\n[OK] {len(STATIONS)} stations across {cities} cities seeded with UUID ids")


if __name__ == "__main__":
    asyncio.run(migrate())
