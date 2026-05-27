"""Generate FlowCast System Architecture Diagram."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

W, H = 34, 23
fig, ax = plt.subplots(figsize=(W, H))
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")
BG = "#0d1117"
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

def box(x, y, w, h, fc, ec="#2d3748", lw=1.5, r=0.28, z=3):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
        facecolor=fc, edgecolor=ec, linewidth=lw, zorder=z))

def txt(x, y, s, sz=9.5, c="#f1f5f9", bold=False, ha="center", va="center"):
    ax.text(x, y, s, ha=ha, va=va, fontsize=sz, color=c,
            fontweight="bold" if bold else "normal", zorder=5, linespacing=1.4)

def arrow(x1, y1, x2, y2, c="#60a5fa", lw=2.5):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=c, lw=lw), zorder=4)

def section(x, y, w, h, fc, title, tc):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.5",
        facecolor=fc, edgecolor="#1e293b", linewidth=2.5, zorder=1))
    txt(x + w/2, y + h - 0.38, title, sz=10.5, c=tc, bold=True)

MX, MW = 0.4, 22.6
RX, RW = 23.5, 10.1
CX = MX + MW / 2

# ── Title ──────────────────────────────────────────────────────────────────────
txt(W/2, 22.55, "FlowCast Backend — System Architecture",
    sz=20, bold=True, c="#f8fafc")
txt(W/2, 22.05,
    "Real-time Traffic Prediction & Monitoring API  ·  FastAPI + PostgreSQL + Redis + WebSockets",
    sz=10.5, c="#94a3b8")

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — CLIENTS  (y: 19.6 → 21.6)
# ══════════════════════════════════════════════════════════════════════════════
section(MX, 19.6, MW, 2.0, "#0c2240", "CLIENT LAYER", "#38bdf8")

clients = [
    ("Web Browser\n(Swagger UI)", "#0369a1"),
    ("React / Vue\nFrontend SPA",  "#0369a1"),
    ("Mobile App\n(Android / iOS)","#0369a1"),
    ("Third-party\nAPI Clients",   "#0369a1"),
    ("WebSocket\nClients",         "#0369a1"),
    ("Admin\nDashboard",           "#075985"),
]
bw, n = 3.2, len(clients)
gp = (MW - n * bw) / (n + 1)
for i, (lbl, col) in enumerate(clients):
    bx = MX + gp + i * (bw + gp)
    box(bx, 19.88, bw, 1.48, col)
    txt(bx + bw/2, 20.62, lbl, sz=10, c="#e0f2fe")

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — API GATEWAY  (y: 17.1 → 19.3)
# ══════════════════════════════════════════════════════════════════════════════
arrow(CX, 19.6, CX, 19.32, c="#38bdf8")
section(MX, 17.1, MW, 2.2, "#170d40", "API GATEWAY — FastAPI + uvicorn", "#a78bfa")

gw = [
    ("Rate Limiter\n100 req / min", "#4c1d95"),
    ("CORS\nMiddleware",            "#4c1d95"),
    ("JWT Auth\nMiddleware",        "#4c1d95"),
    ("Exception\nHandler",          "#4c1d95"),
    ("Google OAuth\nMiddleware",    "#4c1d95"),
    ("OpenAPI\n/docs  /redoc",      "#3b0764"),
    ("WebSocket\nManager",          "#3b0764"),
]
bw2, n2 = 2.9, len(gw)
gp2 = (MW - n2 * bw2) / (n2 + 1)
for i, (lbl, col) in enumerate(gw):
    bx = MX + gp2 + i * (bw2 + gp2)
    box(bx, 17.42, bw2, 1.55, col)
    txt(bx + bw2/2, 18.20, lbl, sz=10, c="#ede9fe")

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — ROUTE MODULES  (y: 12.0 → 16.8)
# ══════════════════════════════════════════════════════════════════════════════
arrow(CX, 17.1, CX, 16.82, c="#818cf8")
section(MX, 12.0, MW, 4.8, "#091f10", "API ROUTE MODULES   /api/v1/...", "#34d399")

routes_a = [
    ("/auth\nRegister · Login\nGoogle OAuth 2.0",    "#064e3b"),
    ("/traffic\nRecords · Predict\nAnomaly · CSV",   "#064e3b"),
    ("/eta\nReal-time ETA\nHyderabad areas",         "#064e3b"),
    ("/analytics\nTrends · Snapshot\nCity Health",   "#064e3b"),
    ("/heatmap\nIntensity Grid\nHotspots",           "#064e3b"),
    ("/india  +WS\nAll-India Monitor\n766 Districts","#064e3b"),
]
bw3, n3 = 3.3, len(routes_a)
gp3 = (MW - n3 * bw3) / (n3 + 1)
for i, (lbl, col) in enumerate(routes_a):
    bx = MX + gp3 + i * (bw3 + gp3)
    box(bx, 14.45, bw3, 2.1, col)
    txt(bx + bw3/2, 15.50, lbl, sz=10, c="#d1fae5")

routes_b = [
    ("/notifications\nInbox · Stats · WS",         "#1e3a5f"),
    ("/routes\nOptimize · Save · Share",           "#1e3a5f"),
    ("/trips  /commute\nLog · History · Forecast", "#1e3a5f"),
    ("/favorites  /user\nBookmarks · Prefs",       "#1e3a5f"),
    ("/alerts/departure\nSchedule · WS push",      "#1e3a5f"),
    ("/eco  /prediction\nCO₂ · 12 h Forecast",    "#1e3a5f"),
]
for i, (lbl, col) in enumerate(routes_b):
    bx = MX + gp3 + i * (bw3 + gp3)
    box(bx, 12.25, bw3, 1.95, col)
    txt(bx + bw3/2, 13.23, lbl, sz=10, c="#bfdbfe")

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — SERVICE LAYER  (y: 7.5 → 11.7)
# ══════════════════════════════════════════════════════════════════════════════
arrow(CX, 12.0, CX, 11.72, c="#34d399")
section(MX, 7.5, MW, 4.2, "#160c00", "SERVICE LAYER", "#f59e0b")

svcs_a = [
    ("Auth Service\nHash · Verify · JWT",  "#78350f"),
    ("ETA Service\nCongestion-aware ETA",  "#78350f"),
    ("Route Service\nGoogle Maps API",     "#78350f"),
    ("Notification Svc\nWS delivery",      "#78350f"),
    ("Heatmap Service\nIntensity scoring", "#78350f"),
    ("Alert Service\nDeparture · WS push", "#78350f"),
]
bw4, n4 = 3.3, len(svcs_a)
gp4 = (MW - n4 * bw4) / (n4 + 1)
for i, (lbl, col) in enumerate(svcs_a):
    bx = MX + gp4 + i * (bw4 + gp4)
    box(bx, 9.85, bw4, 1.65, col)
    txt(bx + bw4/2, 10.675, lbl, sz=10, c="#fef3c7")

svcs_b = [
    ("Realtime Collector\nTomTom → India cities",    "#431407"),
    ("District Collector\n766 districts · WS",        "#431407"),
    ("Prediction Service\nscikit-learn · ML",         "#431407"),
    ("Connection Manager\nWebSocket sessions",        "#431407"),
    ("City Aliases\nIndia location names",            "#431407"),
]
bw4b, n4b = 3.9, len(svcs_b)
gp4b = (MW - n4b * bw4b) / (n4b + 1)
for i, (lbl, col) in enumerate(svcs_b):
    bx = MX + gp4b + i * (bw4b + gp4b)
    box(bx, 7.85, bw4b, 1.65, col)
    txt(bx + bw4b/2, 8.675, lbl, sz=10, c="#fed7aa")

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 5 — DATA LAYER  (y: 3.5 → 7.2)
# ══════════════════════════════════════════════════════════════════════════════
arrow(CX, 7.5, CX, 7.22, c="#f59e0b")
section(MX, 3.5, MW, 3.7, "#130a25", "DATA LAYER", "#e879f9")

DBW = (MW - 1.0 - 1.0) / 3
DBY, DBH = 3.78, 2.97

PGX = MX + 0.5
box(PGX, DBY, DBW, DBH, "#581c87")
cx = PGX + DBW/2
txt(cx, DBY+2.60, "PostgreSQL",               sz=12,  bold=True, c="#f0abfc")
txt(cx, DBY+2.17, "traffic_data  database",   sz=9.5, c="#e9d5ff")
txt(cx, DBY+1.74, "users  ·  saved_routes",   sz=9.5, c="#e9d5ff")
txt(cx, DBY+1.31, "notifications  ·  alerts", sz=9.5, c="#e9d5ff")
txt(cx, DBY+0.88, "trip_history  ·  faves",   sz=9.5, c="#e9d5ff")
txt(cx, DBY+0.45, "share_tokens  ·  prefs",   sz=9,   c="#c4b5fd")

RDX = PGX + DBW + 0.5
box(RDX, DBY, DBW, DBH, "#1e3a5f")
cx = RDX + DBW/2
txt(cx, DBY+2.60, "Redis Cache",               sz=12,  bold=True, c="#93c5fd")
txt(cx, DBY+2.17, "Rate limiting",              sz=9.5, c="#bfdbfe")
txt(cx, DBY+1.74, "Sliding window counter",     sz=9.5, c="#bfdbfe")
txt(cx, DBY+1.31, "100 requests / min / IP",    sz=9.5, c="#bfdbfe")
txt(cx, DBY+0.88, "Session token cache",        sz=9.5, c="#bfdbfe")
txt(cx, DBY+0.45, "Fallback: in-memory dict",   sz=9,   c="#7dd3fc")

ORX = RDX + DBW + 0.5
box(ORX, DBY, DBW, DBH, "#14532d")
cx = ORX + DBW/2
txt(cx, DBY+2.60, "SQLAlchemy ORM",              sz=12,  bold=True, c="#86efac")
txt(cx, DBY+2.17, "pool_size=10  overflow=20",   sz=9.5, c="#bbf7d0")
txt(cx, DBY+1.74, "UUID primary keys (all)",     sz=9.5, c="#bbf7d0")
txt(cx, DBY+1.31, "Auto-migration on startup",   sz=9.5, c="#bbf7d0")
txt(cx, DBY+0.88, "pool_pre_ping = True",        sz=9.5, c="#bbf7d0")
txt(cx, DBY+0.45, "Admin seeded on first run",   sz=9,   c="#4ade80")

# ══════════════════════════════════════════════════════════════════════════════
# RIGHT PANEL — EXTERNAL SERVICES  (y: 3.5 → 21.65)
# ══════════════════════════════════════════════════════════════════════════════
section(RX, 3.5, RW, 18.15, "#160800", "EXTERNAL SERVICES", "#fb923c")

COL_W = (RW - 0.7) / 2
C1X = RX + 0.3
C2X = C1X + COL_W + 0.1
EXT_H = 1.75

ext_rows = [
    (19.25, "Google Maps\nDirections API",     "Google OAuth 2.0\nSign-in / Callback"),
    (17.20, "TomTom Traffic\nReal-time India", "OpenRouteService\nFree routing fallback"),
    (15.15, "Google Cloud\nAPI Console",        "IPCC / DEFRA\nCO₂ emissions data"),
]
for ry, l1, l2 in ext_rows:
    box(C1X, ry, COL_W, EXT_H, "#7c2d12")
    txt(C1X + COL_W/2, ry + EXT_H/2, l1, sz=10, c="#fed7aa")
    box(C2X, ry, COL_W, EXT_H, "#7c2d12")
    txt(C2X + COL_W/2, ry + EXT_H/2, l2, sz=10, c="#fed7aa")

box(C1X, 12.95, COL_W, 1.6, "#1c4f2a")
txt(C1X + COL_W/2, 13.75, "PostgreSQL\nDB Server  :5432", sz=10, c="#86efac")
box(C2X, 12.95, COL_W, 1.6, "#1c2f4f")
txt(C2X + COL_W/2, 13.75, "Redis Server\nlocalhost  :6379", sz=10, c="#93c5fd")

box(C1X, 10.6, RW-0.6, 2.0, "#1c1917")
txt(RX+RW/2, 12.22, "Background Tasks (asyncio)", sz=10.5, bold=True, c="#d6d3d1")
txt(RX+RW/2, 11.80, "Congestion Monitor · Departure Alert Monitor", sz=9.5, c="#a8a29e")
txt(RX+RW/2, 11.40, "India Traffic Collector · District Collector",  sz=9.5, c="#a8a29e")
txt(RX+RW/2, 11.00, "All tasks run every 60 seconds",               sz=9,   c="#78716c")

box(C1X, 8.5, RW-0.6, 1.8, "#1e1b2e")
txt(RX+RW/2, 9.92, "Admin Panel  /api/v1/admin",           sz=10.5, bold=True, c="#c4b5fd")
txt(RX+RW/2, 9.47, "User management · DB health · VACUUM", sz=9.5,  c="#a78bfa")
txt(RX+RW/2, 9.02, "System stats · Traffic record admin",  sz=9.5,  c="#a78bfa")

box(C1X, 6.35, RW-0.6, 1.8, "#1a1209")
txt(RX+RW/2, 7.77, "JWT Authentication",                    sz=10.5, bold=True, c="#fbbf24")
txt(RX+RW/2, 7.32, "HS256 · Expires in 30 minutes",        sz=9.5,  c="#fde68a")
txt(RX+RW/2, 6.87, "Bearer token · Secret key from .env",  sz=9.5,  c="#fde68a")

arrow(MX+MW, 10.5, RX, 10.5, c="#fb923c")

# ══════════════════════════════════════════════════════════════════════════════
# LEGEND
# ══════════════════════════════════════════════════════════════════════════════
legend = [
    ("#0369a1", "Client Apps"),
    ("#4c1d95", "API Middleware"),
    ("#064e3b", "Primary Routes"),
    ("#1e3a5f", "Secondary Routes"),
    ("#78350f", "Core Services"),
    ("#431407", "Infra Services"),
    ("#581c87", "PostgreSQL"),
    ("#7c2d12", "External APIs"),
]
step = W / (len(legend) + 0.5)
for i, (col, lbl) in enumerate(legend):
    lx = 0.5 + i * step
    box(lx, 0.5, 0.6, 0.55, col)
    txt(lx + 0.75, 0.78, lbl, sz=9.5, c="#94a3b8", ha="left")

txt(W/2, 0.15,
    "FlowCast API  v1.0.0  ·  FastAPI + PostgreSQL + Redis + WebSockets  ·  India Traffic Monitoring System",
    sz=9, c="#475569")

plt.tight_layout(pad=0.1)
plt.savefig("flowcast_architecture.png", dpi=150, bbox_inches="tight",
            facecolor=BG, edgecolor="none")
print("Saved: flowcast_architecture.png")
