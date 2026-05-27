"""Generate FlowCast System Architecture Diagram."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(figsize=(26, 18))
ax.set_xlim(0, 26)
ax.set_ylim(0, 18)
ax.axis("off")
fig.patch.set_facecolor("#0f172a")
ax.set_facecolor("#0f172a")

# ── Helpers ──────────────────────────────────────────────────────────────────

def box(x, y, w, h, color, radius=0.3, alpha=1.0):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=color, edgecolor="#334155", linewidth=1.2, alpha=alpha, zorder=3
    ))

def label(x, y, text, size=8, color="#f1f5f9", bold=False, ha="center", va="center"):
    ax.text(x, y, text, ha=ha, va=va, fontsize=size,
            color=color, fontweight="bold" if bold else "normal", zorder=4)

def arrow(x1, y1, x2, y2, color="#475569"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.4), zorder=2)

def section_bg(x, y, w, h, color, title, title_color="#94a3b8"):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0,rounding_size=0.4",
        facecolor=color, edgecolor="#1e293b", linewidth=2, zorder=1
    ))
    label(x + w/2, y + h - 0.28, title, size=8.5, color=title_color, bold=True)

# ══════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════
label(13, 17.4, "FlowCast Backend — System Architecture", size=17, bold=True, color="#f8fafc")
label(13, 17.0, "Real-time Traffic Prediction & Monitoring API for India", size=10, color="#94a3b8")

# ══════════════════════════════════════════════════════
# LAYER 1 — CLIENTS
# ══════════════════════════════════════════════════════
section_bg(0.3, 14.8, 25.4, 1.9, "#1e293b", "CLIENT LAYER", "#38bdf8")

clients = [
    ("Web Browser\n(Swagger UI)", 1.2),
    ("Frontend SPA\n(React / Vue)", 5.0),
    ("Mobile App\n(Android / iOS)", 8.8),
    ("Third-party\nAPI Clients", 12.6),
    ("WebSocket\nClients", 16.4),
    ("Admin\nDashboard", 20.2),
]
for txt, cx in clients:
    box(cx, 15.1, 2.8, 1.3, "#0369a1")
    label(cx + 1.4, 15.75, txt, size=8, color="#e0f2fe")

# ══════════════════════════════════════════════════════
# LAYER 2 — API GATEWAY
# ══════════════════════════════════════════════════════
section_bg(0.3, 12.5, 25.4, 2.0, "#1a2744", "API GATEWAY — FastAPI (uvicorn)", "#818cf8")

gw_items = [
    ("Rate Limiter\n100 req/min", 1.2, "#4c1d95"),
    ("CORS\nMiddleware", 4.5, "#4c1d95"),
    ("JWT Auth\nMiddleware", 7.8, "#4c1d95"),
    ("Exception\nHandler", 11.1, "#4c1d95"),
    ("Google OAuth\nMiddleware", 14.4, "#4c1d95"),
    ("OpenAPI\nDocs /docs", 17.7, "#4c1d95"),
    ("WebSocket\nManager", 21.0, "#4c1d95"),
]
for txt, cx, col in gw_items:
    box(cx, 12.75, 2.8, 1.45, col)
    label(cx + 1.4, 13.48, txt, size=7.5, color="#ede9fe")

# ══════════════════════════════════════════════════════
# LAYER 3 — ROUTE MODULES
# ══════════════════════════════════════════════════════
section_bg(0.3, 9.0, 25.4, 3.2, "#172035", "API ROUTE MODULES  (/api/v1/...)", "#34d399")

routes = [
    ("/auth\nRegister·Login\nGoogle OAuth", 0.5, "#064e3b"),
    ("/traffic\nRecords·Predict\nAnomaly·CSV", 3.2, "#064e3b"),
    ("/eta\nReal-time ETA\nHyderabad", 5.9, "#064e3b"),
    ("/analytics\nTrends·Health\nTimelapse", 8.6, "#064e3b"),
    ("/heatmap\nIndia Heatmap\nHotspots", 11.3, "#064e3b"),
    ("/notifications\nPush·History\nWS·Stats", 14.0, "#064e3b"),
    ("/routes\nOptimize·Save\nShare", 16.7, "#064e3b"),
    ("/commute\nForecast\nDeparture", 19.4, "#064e3b"),
    ("/india\nAll-India\nDistricts WS", 22.1, "#064e3b"),
]
for txt, cx, col in routes:
    box(cx, 10.6, 2.5, 1.45, col)
    label(cx + 1.25, 11.33, txt, size=7, color="#d1fae5")

routes2 = [
    ("/favorites\nBookmarks\nStatus", 0.5, "#1c3163"),
    ("/user/preferences\nSettings·Mode\nQuiet Hours", 3.5, "#1c3163"),
    ("/trips\nLog·History\nStats", 6.9, "#1c3163"),
    ("/alerts/departure\nSchedule\nReminders", 9.9, "#1c3163"),
    ("/eco\nCO₂ Calc\nMode Compare", 12.9, "#1c3163"),
    ("/prediction\nForecast·12h\nArea Compare", 15.9, "#1c3163"),
    ("/admin\nUsers·DB\nMonitoring", 18.9, "#1c3163"),
]
for txt, cx, col in routes2:
    box(cx, 9.2, 2.8, 1.3, col)
    label(cx + 1.4, 9.85, txt, size=7, color="#bfdbfe")

# ══════════════════════════════════════════════════════
# LAYER 4 — SERVICES
# ══════════════════════════════════════════════════════
section_bg(0.3, 5.8, 17.2, 2.9, "#1a1a2e", "SERVICE LAYER", "#f59e0b")

services = [
    ("Auth Service\nhash·verify·JWT", 0.5, "#78350f"),
    ("ETA Service\ncongestion calc", 3.5, "#78350f"),
    ("Route Service\nGoogle Maps", 6.5, "#78350f"),
    ("Notification\nService·WS push", 9.5, "#78350f"),
    ("Heatmap Service\nintensity score", 12.5, "#78350f"),
    ("Alert Service\ndeparture check", 15.5, "#78350f"),
]
for txt, cx, col in services:
    box(cx, 7.6, 2.8, 1.2, col)
    label(cx + 1.4, 8.2, txt, size=7.2, color="#fef3c7")

services2 = [
    ("Realtime Collector\nIndia traffic poll", 0.5, "#431407"),
    ("District Collector\n766 districts WS", 3.9, "#431407"),
    ("Prediction Service\nML·sklearn", 7.3, "#431407"),
    ("Connection Manager\nWS sessions", 10.7, "#431407"),
    ("City Aliases\nIndia locations", 14.1, "#431407"),
]
for txt, cx, col in services2:
    box(cx, 6.1, 3.2, 1.3, col)
    label(cx + 1.6, 6.75, txt, size=7, color="#fed7aa")

# ══════════════════════════════════════════════════════
# LAYER 5 — DATA STORES
# ══════════════════════════════════════════════════════
section_bg(0.3, 2.8, 17.2, 2.7, "#111827", "DATA LAYER", "#e879f9")

box(0.6, 3.1, 5.0, 2.0, "#581c87")
label(3.1, 4.35, "PostgreSQL", size=9, bold=True, color="#f0abfc")
label(3.1, 3.95, "traffic_data database", size=7.5, color="#e9d5ff")
label(3.1, 3.60, "users · saved_routes · notifications", size=6.8, color="#e9d5ff")
label(3.1, 3.25, "trip_history · departure_alerts", size=6.8, color="#e9d5ff")

box(6.2, 3.1, 4.4, 2.0, "#1e3a5f")
label(8.4, 4.35, "Redis Cache", size=9, bold=True, color="#93c5fd")
label(8.4, 3.95, "Rate limiting buckets", size=7.5, color="#bfdbfe")
label(8.4, 3.60, "Session tokens", size=7.5, color="#bfdbfe")
label(8.4, 3.25, "Fallback: in-memory", size=7.5, color="#bfdbfe")

box(11.2, 3.1, 5.8, 2.0, "#14532d")
label(14.1, 4.35, "SQLAlchemy ORM", size=9, bold=True, color="#86efac")
label(14.1, 3.95, "Pool size: 10 + 20 overflow", size=7.5, color="#bbf7d0")
label(14.1, 3.60, "UUID primary keys", size=7.5, color="#bbf7d0")
label(14.1, 3.25, "Auto-migration on startup", size=7.5, color="#bbf7d0")

# ══════════════════════════════════════════════════════
# LAYER 6 — EXTERNAL APIs (right side)
# ══════════════════════════════════════════════════════
section_bg(17.8, 2.8, 7.9, 8.9, "#1a1209", "EXTERNAL SERVICES", "#fb923c")

ext = [
    ("Google Maps\nDirections API", 18.1, 9.7, "#7c2d12"),
    ("Google OAuth 2.0\nSign-in", 21.4, 9.7, "#7c2d12"),
    ("TomTom Traffic\nReal-time India", 18.1, 7.8, "#7c2d12"),
    ("OpenRouteService\nFree routing", 21.4, 7.8, "#7c2d12"),
    ("Google Cloud\nConsole", 18.1, 5.9, "#7c2d12"),
    ("IPCC/DEFRA\nCO₂ Data", 21.4, 5.9, "#7c2d12"),
    ("PostgreSQL\nDB Server", 18.1, 4.0, "#1c4f2a"),
    ("Redis Server\n:6379", 21.4, 4.0, "#1c2f4f"),
]
for txt, cx, cy, col in ext:
    box(cx, cy, 3.0, 1.5, col)
    label(cx + 1.5, cy + 0.75, txt, size=7.5, color="#fed7aa")

# ══════════════════════════════════════════════════════
# BACKGROUND TASKS BOX
# ══════════════════════════════════════════════════════
box(18.1, 2.8, 7.4, 1.0, "#1c1917")
label(21.8, 3.3, "Background Tasks (asyncio)  |  Congestion Monitor · Departure Alert Monitor\nIndia Traffic Collector · District Collector  —  all run every 60s", size=6.8, color="#d6d3d1")

# ══════════════════════════════════════════════════════
# ARROWS between layers
# ══════════════════════════════════════════════════════
# Clients → Gateway
arrow(13, 14.8, 13, 14.5, "#38bdf8")
# Gateway → Routes
arrow(13, 12.5, 13, 12.2, "#818cf8")
# Routes → Services
arrow(9, 9.0, 9, 8.8, "#34d399")
# Services → Data
arrow(9, 5.8, 9, 5.5, "#f59e0b")
# Services → External
arrow(17.5, 7.2, 17.8, 7.2, "#fb923c")

# ══════════════════════════════════════════════════════
# LEGEND
# ══════════════════════════════════════════════════════
legend_items = [
    ("#0369a1", "Client Apps"),
    ("#4c1d95", "Middleware"),
    ("#064e3b", "Route Handlers"),
    ("#78350f", "Business Services"),
    ("#581c87", "PostgreSQL"),
    ("#1e3a5f", "Redis"),
    ("#7c2d12", "External APIs"),
]
lx = 0.5
for i, (col, lbl) in enumerate(legend_items):
    bx = lx + i * 3.55
    box(bx, 0.25, 0.5, 0.45, col)
    label(bx + 0.7, 0.48, lbl, size=7.5, color="#94a3b8", ha="left")

label(13, 0.05, "FlowCast API  v1.0.0  ·  FastAPI + PostgreSQL + Redis + WebSockets  ·  India Traffic Monitoring", size=7.5, color="#475569")

plt.tight_layout(pad=0.2)
plt.savefig("flowcast_architecture.png", dpi=150, bbox_inches="tight",
            facecolor="#0f172a", edgecolor="none")
print("Saved: flowcast_architecture.png")
