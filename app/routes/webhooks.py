"""Webhook integration endpoints."""

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.webhook import Webhook, WebhookDelivery
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.webhook_service import deliver_webhook

router = APIRouter(prefix="/webhooks", tags=["Webhook Integrations"])
logger = logging.getLogger(__name__)

_VALID_EVENTS = {
    "congestion_spike", "congestion_clearing",
    "zone_alert", "departure_alert",
    "incident_new", "rule_triggered",
    "speed_drop", "speed_recovery",
    "*",
}

_EVENT_DESCRIPTIONS = {
    "congestion_spike":    "Fired when a location jumps to high congestion",
    "congestion_clearing": "Fired when congestion drops from high → medium or low",
    "zone_alert":          "Fired when a geofence zone hits its congestion threshold",
    "departure_alert":     "Fired when a user's departure reminder is triggered",
    "incident_new":        "Fired when a new road incident is reported",
    "rule_triggered":      "Fired when a custom alert rule condition is met",
    "speed_drop":          "Fired when average speed drops >20% within a minute",
    "speed_recovery":      "Fired when average speed recovers >20% within a minute",
    "*":                   "Wildcard — receives every event type",
}

# Demo webhooks seeded for first-time users
_DEMO_WEBHOOKS = [
    {
        "url":    "https://httpbin.org/post",
        "events": ["congestion_spike", "zone_alert"],
        "label":  "Demo — Congestion & Zone Alerts (httpbin echo)",
    },
    {
        "url":    "https://httpbin.org/post",
        "events": ["incident_new", "rule_triggered"],
        "label":  "Demo — Incidents & Custom Rules (httpbin echo)",
    },
    {
        "url":    "https://httpbin.org/post",
        "events": ["departure_alert", "speed_drop", "speed_recovery"],
        "label":  "Demo — Departure & Speed Events (httpbin echo)",
    },
]


# ── Request schemas ────────────────────────────────────────────────────────────

class WebhookCreate(BaseModel):
    name:   Optional[str]       = Field(
        None, max_length=200,
        description="Friendly label for this webhook, e.g. 'Slack Traffic Alerts'",
    )
    url:    str                 = Field(
        ..., min_length=8, max_length=500,
        description="HTTPS endpoint that will receive the signed POST payload",
    )
    events: list[str]           = Field(
        default=["congestion_spike"],
        description="List of event types to subscribe to. Use '*' for all events.",
    )
    org_id: Optional[uuid.UUID] = Field(
        None,
        description="Optional organization UUID to scope this webhook",
    )

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "name":   "Slack — Congestion & Incident Alerts",
            "url":    "https://hooks.example.com/your-webhook-url-here",
            "events": ["congestion_spike", "incident_new", "zone_alert"],
            "org_id": None,
        }
    })


class WebhookUpdate(BaseModel):
    url:       Optional[str]       = Field(
        None, min_length=8, max_length=500,
        description="New destination URL",
    )
    events:    Optional[list[str]] = Field(
        None,
        description="Replace subscribed event list",
    )
    is_active: Optional[bool]      = Field(
        None,
        description="Enable (true) or disable (false) this webhook",
    )

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "url":       "https://my-server.com/flowcast-hook",
            "events":    ["congestion_spike", "congestion_clearing", "speed_drop"],
            "is_active": True,
        }
    })


# ── Helpers ────────────────────────────────────────────────────────────────────

def _dt_utc(dt: Optional[datetime]) -> Optional[str]:
    """Format any datetime as UTC ISO-8601 with timezone suffix."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _delivery_row(d: WebhookDelivery) -> dict:
    """Serialize one WebhookDelivery to a concise dict."""
    success = bool(d.http_status and 200 <= d.http_status < 300)
    return {
        "id":            str(d.id),
        "event_type":    d.event_type,
        "http_status":   d.http_status,
        "status":        "success" if success else "failed",
        "attempt":       d.attempt,
        "delivered_at":  _dt_utc(d.delivered_at),
        "created_at":    _dt_utc(d.created_at),
        "error_message": d.error_message,
    }


def _webhook_dict(wh: Webhook, db: Optional[Session] = None,
                  include_stats: bool = True) -> dict:
    total      = wh.total_deliveries or 0
    failed     = wh.failed_deliveries or 0
    successful = max(0, total - failed)

    base = {
        "id":    str(wh.id),
        "name":  wh.name or wh.url,
        "url":   wh.url,
        "events": wh.events.split(","),
        "event_descriptions": {
            e.strip(): _EVENT_DESCRIPTIONS.get(e.strip(), "")
            for e in wh.events.split(",")
        },
        "is_active":         wh.is_active,
        "is_demo":           wh.url == "https://httpbin.org/post",
        "last_triggered_at": _dt_utc(wh.last_triggered_at),
        "created_at":        _dt_utc(wh.created_at),
    }

    if include_stats:
        base["stats"] = {
            "total_deliveries":      total,
            "successful_deliveries": successful,
            "failed_deliveries":     failed,
            "success_rate_pct":      round(successful / total * 100, 1) if total else 0.0,
        }
        if total == 0:
            base["needs_test_ping"] = True
            base["hint"] = (
                f"POST /api/v1/webhooks/{wh.id}/test  "
                "— fire a live ping to verify your endpoint."
            )

    # Inline last 5 deliveries so both list and detail views are self-contained
    if db is not None:
        recent = (
            db.query(WebhookDelivery)
            .filter(WebhookDelivery.webhook_id == wh.id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(5)
            .all()
        )
        base["recent_deliveries"] = [_delivery_row(d) for d in recent]
        base["delivery_log_url"]  = f"/api/v1/webhooks/{wh.id}/deliveries"

    return base


def _get_webhook_or_404(webhook_id, user_id, db) -> Webhook:
    wh = db.query(Webhook).filter(
        Webhook.id == webhook_id, Webhook.user_id == user_id
    ).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return wh


def _seed_demo_webhooks(user: User, db: Session) -> list[Webhook]:
    """
    Create 3 demo webhooks for first-time users — idempotent, keyed by URL+events.
    Each points to httpbin.org/post which echoes the request back, so test pings
    always show a successful delivery in the log.
    """
    created = []
    for demo in _DEMO_WEBHOOKS:
        events_str = ",".join(demo["events"])
        existing = db.query(Webhook).filter(
            Webhook.user_id == user.id,
            Webhook.url == demo["url"],
            Webhook.events == events_str,
        ).first()
        if existing:
            created.append(existing)
            continue

        wh = Webhook(
            user_id=user.id,
            name=demo["label"],
            url=demo["url"],
            secret=secrets.token_hex(32),
            events=events_str,
            is_active=True,
        )
        db.add(wh)
        created.append(wh)

    try:
        db.commit()
        for wh in created:
            db.refresh(wh)
        logger.info("Demo webhooks seeded for user %s (%d total)", user.id, len(created))
    except Exception as exc:
        db.rollback()
        logger.error("Demo webhook seed failed: %s", exc)

    return created


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/event-types", status_code=status.HTTP_200_OK,
            summary="Live event-type stats — counts, locations, last fired")
def list_event_types(db: Session = Depends(get_db)) -> dict:
    """
    Every subscribable event type enriched with **real-time DB stats**:

    - `count_1h` / `count_24h` — occurrences in last 1 h / 24 h
    - `last_fired`  — ISO timestamp of most recent occurrence (any time window)
    - `top_locations` — up to 3 most active locations (falls back to 24 h when 1 h is quiet)
    - `status`      — `active` (fired in last hour) / `quiet`
    - `sample_payload` — real location pulled from live DB data
    """
    from datetime import timedelta
    from collections import Counter
    from app.models.predictor import TrafficRecord, Incident
    from app.models.zone import ZoneAlert, GeofenceZone
    from app.models.rule import RuleEvaluation, AlertRule
    from app.models.alert import DepartureAlert

    now = datetime.now(timezone.utc)
    h1  = now - timedelta(hours=1)
    h24 = now - timedelta(hours=24)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _normalize(dt: Optional[datetime]) -> Optional[datetime]:
        """Make any datetime UTC-aware; return None for None values."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _fmt(dt) -> Optional[str]:
        n = _normalize(dt)
        return n.isoformat() if n else None

    def _top(rows_1h, rows_24h, attr: str = "location") -> list[str]:
        """Return top 3 locations from 1h window; fall back to 24h if 1h is empty."""
        pool = rows_1h if rows_1h else rows_24h
        locs = [getattr(r, attr, None) for r in pool if getattr(r, attr, None)]
        return [loc for loc, _ in Counter(locs).most_common(3)]

    def _last(rows, ts_attr: str = "created_at") -> Optional[datetime]:
        """Return the most recent normalized (UTC-aware) datetime from a list of rows."""
        if not rows:
            return None
        candidates = [
            _normalize(getattr(r, ts_attr))
            for r in rows
            if getattr(r, ts_attr, None) is not None
        ]
        return max(candidates) if candidates else None

    def _status(count_1h: int) -> str:
        return "active" if count_1h > 0 else "quiet"

    def _sample(event: str, loc: Optional[str] = None) -> dict:
        return {
            "event":     event,
            "location":  loc or "Silk Board Junction",
            "timestamp": now.isoformat(),
            "data":      _SAMPLE_DATA.get(event, {}),
        }

    # ── congestion_spike (high congestion records) ────────────────────────────
    cs_1h  = db.query(TrafficRecord).filter(
        TrafficRecord.congestion_level == "high",
        TrafficRecord.created_at >= h1,
    ).all()
    cs_24h = db.query(TrafficRecord).filter(
        TrafficRecord.congestion_level == "high",
        TrafficRecord.created_at >= h24,
    ).all()
    cs_top  = _top(cs_1h, cs_24h, "location")
    cs_last = _last(cs_1h or cs_24h)

    # ── congestion_clearing (low/medium records) ──────────────────────────────
    cc_1h  = db.query(TrafficRecord).filter(
        TrafficRecord.congestion_level.in_(["low", "medium"]),
        TrafficRecord.created_at >= h1,
    ).all()
    cc_24h = db.query(TrafficRecord).filter(
        TrafficRecord.congestion_level.in_(["low", "medium"]),
        TrafficRecord.created_at >= h24,
    ).all()
    cc_top  = _top(cc_1h, cc_24h, "location")
    cc_last = _last(cc_1h or cc_24h)

    # ── zone_alert (ZoneAlert joined with GeofenceZone for zone name) ─────────
    def _zone_rows(since):
        return db.query(ZoneAlert, GeofenceZone).join(
            GeofenceZone, ZoneAlert.zone_id == GeofenceZone.id
        ).filter(ZoneAlert.triggered_at >= since).all()

    za_1h_rows  = _zone_rows(h1)
    za_24h_rows = _zone_rows(h24)
    za_1h_count = len(za_1h_rows)
    za_24h_count = len(za_24h_rows)
    # Extract zone names — use 1h pool if non-empty, fall back to 24h
    za_pool = za_1h_rows if za_1h_rows else za_24h_rows
    za_top  = [n for n, _ in Counter(g.name for _, g in za_pool).most_common(3)]
    za_last = max((za.triggered_at for za, _ in za_1h_rows or za_24h_rows), default=None)
    # Also get high-congestion locations inside the most-recently-triggered zones
    if not za_top and za_24h_rows:
        # Fall back to congestion_spike locations as a proxy
        za_top = cs_top[:3]

    # ── departure_alert ───────────────────────────────────────────────────────
    dep_1h  = db.query(DepartureAlert).filter(
        DepartureAlert.last_triggered_at >= h1,
    ).all()
    dep_24h = db.query(DepartureAlert).filter(
        DepartureAlert.last_triggered_at >= h24,
    ).all()
    dep_pool = dep_1h if dep_1h else dep_24h
    dep_top  = [n for n, _ in Counter(
        r.destination_name for r in dep_pool if r.destination_name
    ).most_common(3)]
    # If still empty, show origin → destination strings
    if not dep_top:
        dep_top = list({
            f"{r.origin_name} → {r.destination_name}"
            for r in dep_24h if r.origin_name
        })[:3]
    dep_last = _last(dep_1h or dep_24h, "last_triggered_at")

    # ── incident_new ──────────────────────────────────────────────────────────
    inc_1h  = db.query(Incident).filter(Incident.created_at >= h1).all()
    inc_24h = db.query(Incident).filter(Incident.created_at >= h24).all()
    inc_top = _top(inc_1h, inc_24h, "location")
    inc_last = _last(inc_1h or inc_24h)
    inc_active_count = db.query(Incident).filter(Incident.is_active == True).count()
    # Fallback: use highest-congestion locations as likely incident zones
    if not inc_top:
        inc_top = cs_top[:3]

    # ── rule_triggered ────────────────────────────────────────────────────────
    re_1h  = db.query(RuleEvaluation).filter(
        RuleEvaluation.triggered_at >= h1,
    ).all()
    re_24h = db.query(RuleEvaluation).filter(
        RuleEvaluation.triggered_at >= h24,
    ).all()
    re_top  = _top(re_1h, re_24h, "location")
    re_last = _last(re_1h or re_24h, "triggered_at")
    # Fallback: show locations from active alert rules
    if not re_top:
        rule_locs = [r.location for r in db.query(AlertRule).filter(
            AlertRule.is_active == True
        ).limit(10).all() if r.location]
        re_top = [loc for loc, _ in Counter(rule_locs).most_common(3)]

    # ── speed_drop  (< 25 km/h — gridlock / very slow) ───────────────────────
    sd_1h  = db.query(TrafficRecord).filter(
        TrafficRecord.average_speed < 25,
        TrafficRecord.average_speed.isnot(None),
        TrafficRecord.created_at >= h1,
    ).all()
    sd_24h = db.query(TrafficRecord).filter(
        TrafficRecord.average_speed < 25,
        TrafficRecord.average_speed.isnot(None),
        TrafficRecord.created_at >= h24,
    ).all()
    sd_top  = _top(sd_1h, sd_24h, "location")
    sd_last = _last(sd_1h or sd_24h)

    # ── speed_recovery  (> 35 km/h — normal flow resuming) ───────────────────
    # FIX: lowered threshold from 50 → 35 km/h, removed strict congestion=low check
    sr_1h  = db.query(TrafficRecord).filter(
        TrafficRecord.average_speed > 35,
        TrafficRecord.average_speed.isnot(None),
        TrafficRecord.congestion_level.in_(["low", "medium"]),
        TrafficRecord.created_at >= h1,
    ).all()
    sr_24h = db.query(TrafficRecord).filter(
        TrafficRecord.average_speed > 35,
        TrafficRecord.average_speed.isnot(None),
        TrafficRecord.congestion_level.in_(["low", "medium"]),
        TrafficRecord.created_at >= h24,
    ).all()
    sr_top  = _top(sr_1h, sr_24h, "location")
    sr_last = _last(sr_1h or sr_24h)

    # ── wildcard (*) aggregate ────────────────────────────────────────────────
    all_last_dts = [
        _normalize(dt)
        for dt in [cs_last, za_last, inc_last, re_last, sd_last, sr_last]
        if dt is not None
    ]
    wildcard_last = max(all_last_dts) if all_last_dts else None

    # ── assemble response ─────────────────────────────────────────────────────
    event_types = [
        {
            "event":          "congestion_spike",
            "description":    _EVENT_DESCRIPTIONS["congestion_spike"],
            "status":         _status(len(cs_1h)),
            "count_1h":       len(cs_1h),
            "count_24h":      len(cs_24h),
            "last_fired":     _fmt(cs_last),
            "top_locations":  cs_top,
            "sample_payload": _sample("congestion_spike", cs_top[0] if cs_top else None),
        },
        {
            "event":          "congestion_clearing",
            "description":    _EVENT_DESCRIPTIONS["congestion_clearing"],
            "status":         _status(len(cc_1h)),
            "count_1h":       len(cc_1h),
            "count_24h":      len(cc_24h),
            "last_fired":     _fmt(cc_last),
            "top_locations":  cc_top,
            "sample_payload": _sample("congestion_clearing", cc_top[0] if cc_top else None),
        },
        {
            "event":          "zone_alert",
            "description":    _EVENT_DESCRIPTIONS["zone_alert"],
            "status":         _status(za_1h_count),
            "count_1h":       za_1h_count,
            "count_24h":      za_24h_count,
            "last_fired":     _fmt(za_last),
            "top_locations":  za_top,
            "sample_payload": _sample("zone_alert", za_top[0] if za_top else None),
        },
        {
            "event":          "departure_alert",
            "description":    _EVENT_DESCRIPTIONS["departure_alert"],
            "status":         _status(len(dep_1h)),
            "count_1h":       len(dep_1h),
            "count_24h":      len(dep_24h),
            "last_fired":     _fmt(dep_last),
            "top_locations":  dep_top,
            "sample_payload": _sample("departure_alert", dep_top[0] if dep_top else None),
        },
        {
            "event":          "incident_new",
            "description":    _EVENT_DESCRIPTIONS["incident_new"],
            "status":         _status(len(inc_1h)),
            "count_1h":       len(inc_1h),
            "count_24h":      len(inc_24h),
            "last_fired":     _fmt(inc_last),
            "top_locations":  inc_top,
            "active_incidents": inc_active_count,
            "sample_payload": _sample("incident_new", inc_top[0] if inc_top else None),
        },
        {
            "event":          "rule_triggered",
            "description":    _EVENT_DESCRIPTIONS["rule_triggered"],
            "status":         _status(len(re_1h)),
            "count_1h":       len(re_1h),
            "count_24h":      len(re_24h),
            "last_fired":     _fmt(re_last),
            "top_locations":  re_top,
            "sample_payload": _sample("rule_triggered", re_top[0] if re_top else None),
        },
        {
            "event":          "speed_drop",
            "description":    _EVENT_DESCRIPTIONS["speed_drop"],
            "status":         _status(len(sd_1h)),
            "count_1h":       len(sd_1h),
            "count_24h":      len(sd_24h),
            "last_fired":     _fmt(sd_last),
            "top_locations":  sd_top,
            "sample_payload": _sample("speed_drop", sd_top[0] if sd_top else None),
        },
        {
            "event":          "speed_recovery",
            "description":    _EVENT_DESCRIPTIONS["speed_recovery"],
            "status":         _status(len(sr_1h)),
            "count_1h":       len(sr_1h),
            "count_24h":      len(sr_24h),
            "last_fired":     _fmt(sr_last),
            "top_locations":  sr_top,
            "sample_payload": _sample("speed_recovery", sr_top[0] if sr_top else None),
        },
        {
            "event":          "*",
            "description":    _EVENT_DESCRIPTIONS["*"],
            "status":         "active",
            "count_1h":       len(cs_1h) + za_1h_count + len(inc_1h) + len(re_1h) + len(sd_1h) + len(sr_1h),
            "count_24h":      len(cs_24h) + za_24h_count + len(inc_24h) + len(re_24h) + len(sd_24h) + len(sr_24h),
            "last_fired":     _fmt(wildcard_last),
            "top_locations":  cs_top[:3],   # network-wide hotspots
            "sample_payload": _sample("*", cs_top[0] if cs_top else None),
        },
    ]

    total_1h   = sum(e["count_1h"]  for e in event_types if e["event"] != "*")
    total_24h  = sum(e["count_24h"] for e in event_types if e["event"] != "*")
    active_now = sum(1 for e in event_types if e["status"] == "active" and e["event"] != "*")

    return {
        "event_types": event_types,
        "total": len(event_types),
        "network_summary": {
            "events_last_1h":   total_1h,
            "events_last_24h":  total_24h,
            "active_types_now": active_now,
            "generated_at":     now.isoformat(),
        },
        "tip":       "Use 'POST /api/v1/webhooks/{id}/test' to send a test ping after registering.",
        "live_feed": "Connect to ws://<host>/api/v1/webhooks/ws/live-events for real-time event stream.",
    }


# ── Sample payloads per event type ───────────────────────────────────────────
_SAMPLE_DATA: dict[str, dict] = {
    "congestion_spike":    {"congestion_level": "high",   "average_speed_kmh": 8.4,  "vehicle_count": 1240},
    "congestion_clearing": {"from_level": "high", "to_level": "medium", "average_speed_kmh": 32.1},
    "zone_alert":          {"zone_name": "My Zone", "congestion_level": "high", "affected_locations": 4, "avg_speed_kmh": 11.2},
    "departure_alert":     {"route": "Home → Office", "eta_minutes": 28, "congestion_level": "medium", "leave_by": "08:15"},
    "incident_new":        {"incident_type": "accident", "severity": "moderate", "upvotes": 3, "expires_hours": 4},
    "rule_triggered":      {"rule_name": "Silk Board High", "metric": "congestion_level", "value": "high", "duration_minutes": 10},
    "speed_drop":          {"from_speed_kmh": 45.0, "to_speed_kmh": 9.2, "drop_pct": 79.6},
    "speed_recovery":      {"from_speed_kmh": 9.2,  "to_speed_kmh": 44.1, "gain_pct": 379.3},
    "*":                   {"note": "All event types — wildcard subscription"},
}


# ── Live Event Feed WebSocket ─────────────────────────────────────────────────

from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/ws/live-events")
async def live_events_ws(websocket: WebSocket) -> None:
    """
    Real-time webhook event feed WebSocket.

    Connect: `ws://<host>/api/v1/webhooks/ws/live-events`

    Pushes a live snapshot every 10 seconds showing:
    - Which event types fired in the last 60 seconds
    - Counts, locations, last fired time
    - Network-wide activity score

    ```json
    {
      "type": "live_event_snapshot",
      "timestamp": "...",
      "activity_score": 73,
      "events_last_60s": 12,
      "firing": [
        {
          "event": "congestion_spike",
          "count_60s": 5,
          "last_location": "Silk Board Junction",
          "last_fired": "..."
        }
      ]
    }
    ```
    """
    import asyncio
    from datetime import timedelta
    from app.database import SessionLocal
    from app.models.predictor import TrafficRecord, Incident
    from app.models.zone import ZoneAlert
    from app.models.rule import RuleEvaluation

    await websocket.accept()
    await websocket.send_json({
        "type":    "connected",
        "message": "Live webhook event feed — snapshot every 10 seconds.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        while True:
            tick = datetime.now(timezone.utc)
            window = tick - timedelta(seconds=60)
            db = SessionLocal()
            try:
                # congestion_spike
                cs = db.query(TrafficRecord).filter(
                    TrafficRecord.congestion_level == "high",
                    TrafficRecord.created_at >= window,
                ).all()
                # congestion_clearing
                cc = db.query(TrafficRecord).filter(
                    TrafficRecord.congestion_level.in_(["low", "medium"]),
                    TrafficRecord.created_at >= window,
                ).all()
                # zone_alert
                za = db.query(ZoneAlert).filter(ZoneAlert.triggered_at >= window).all()
                # incident_new
                inc = db.query(Incident).filter(Incident.created_at >= window).all()
                # rule_triggered
                re = db.query(RuleEvaluation).filter(
                    RuleEvaluation.triggered_at >= window,
                ).all()
                # speed_drop
                sd = db.query(TrafficRecord).filter(
                    TrafficRecord.average_speed < 20,
                    TrafficRecord.average_speed.isnot(None),
                    TrafficRecord.created_at >= window,
                ).all()
                # speed_recovery
                sr = db.query(TrafficRecord).filter(
                    TrafficRecord.average_speed > 50,
                    TrafficRecord.congestion_level == "low",
                    TrafficRecord.created_at >= window,
                ).all()

                def _norm(dt):
                    """Make datetime UTC-aware or return epoch for sort key."""
                    if dt is None:
                        return datetime.min.replace(tzinfo=timezone.utc)
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

                def _last_loc(records, attr="location") -> Optional[str]:
                    if not records: return None
                    latest = max(records, key=lambda r: _norm(
                        getattr(r, "created_at", None) or getattr(r, "triggered_at", None)
                    ))
                    return getattr(latest, attr, None)

                def _last_ts(records, attr="created_at") -> Optional[str]:
                    if not records: return None
                    latest = max(records, key=lambda r: _norm(getattr(r, attr, None)))
                    ts = _norm(getattr(latest, attr, None))
                    return ts.isoformat() if ts.year > 1 else None

                firing = []
                for label, rows, loc_attr, ts_attr in [
                    ("congestion_spike",    cs,  "location",    "created_at"),
                    ("congestion_clearing", cc,  "location",    "created_at"),
                    ("zone_alert",          za,  None,          "triggered_at"),
                    ("incident_new",        inc, "location",    "created_at"),
                    ("rule_triggered",      re,  "location",    "triggered_at"),
                    ("speed_drop",          sd,  "location",    "created_at"),
                    ("speed_recovery",      sr,  "location",    "created_at"),
                ]:
                    if not rows:
                        continue
                    firing.append({
                        "event":         label,
                        "count_60s":     len(rows),
                        "last_location": _last_loc(rows, loc_attr) if loc_attr else None,
                        "last_fired":    _last_ts(rows, ts_attr),
                        "status":        "firing" if len(rows) >= 3 else "occasional",
                    })

                total_events = sum(e["count_60s"] for e in firing)
                # Activity score 0-100: based on congestion spikes + incidents
                spike_score = min(len(cs) * 3, 50)
                inc_score   = min(len(inc) * 10, 30)
                rule_score  = min(len(re) * 5, 20)
                activity    = min(spike_score + inc_score + rule_score, 100)

                await websocket.send_json({
                    "type":            "live_event_snapshot",
                    "timestamp":       tick.isoformat(),
                    "window_seconds":  60,
                    "activity_score":  activity,
                    "events_last_60s": total_events,
                    "active_types":    len(firing),
                    "firing":          firing,
                    "quiet_types":     [
                        e for e in _VALID_EVENTS - {"*"}
                        if e not in {f["event"] for f in firing}
                    ],
                    "network_health":  "congested" if activity > 60 else "moderate" if activity > 30 else "clear",
                })
            finally:
                db.close()

            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("Live events WS error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass


@router.post("", status_code=status.HTTP_201_CREATED,
             summary="Register a new webhook endpoint")
def register_webhook(
    payload: WebhookCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Register a webhook endpoint to receive FlowCast traffic events.

    FlowCast will POST a signed JSON payload to your URL when subscribed
    events fire. Verify the `X-FlowCast-Signature` HMAC-SHA256 header using
    the `secret` returned here (shown only once — store it securely).

    **Available events:** congestion_spike, congestion_clearing, zone_alert,
    departure_alert, incident_new, rule_triggered, speed_drop, speed_recovery, *
    """
    invalid = [e for e in payload.events if e not in _VALID_EVENTS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown events: {invalid}. Use GET /webhooks/event-types."
        )
    if not (payload.url.startswith("http://") or payload.url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    wh = Webhook(
        user_id=current_user.id,
        org_id=payload.org_id,
        name=payload.name or payload.url,
        url=payload.url,
        secret=secrets.token_hex(32),
        events=",".join(sorted(set(payload.events))),
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    logger.info("Webhook registered for user %s → %s", current_user.id, payload.url)
    return {
        **_webhook_dict(wh, db=db),
        "secret": wh.secret,
        "message": "Webhook registered. Copy the secret now — it is not shown again.",
        "next_step": f"POST /api/v1/webhooks/{wh.id}/test  to send a test ping.",
    }


@router.get("", status_code=status.HTTP_200_OK,
            summary="List all your webhooks")
async def list_webhooks(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Return all webhooks registered by the current user.

    **First call:** auto-creates 3 demo webhooks pointing to `https://httpbin.org/post`
    and fires a test ping to each — so `total_deliveries` and `last_triggered_at`
    are immediately populated with real data.
    """
    webhooks = db.query(Webhook).filter(Webhook.user_id == current_user.id).all()

    # Auto-seed and auto-test on first call
    freshly_seeded = False
    if not webhooks:
        webhooks = _seed_demo_webhooks(current_user, db)
        freshly_seeded = True

    # Fire a test ping for each webhook that has never been tested
    untested = [wh for wh in webhooks if (wh.total_deliveries or 0) == 0 and wh.is_active]
    if untested:
        for wh in untested:
            try:
                ping_payload = {
                    "event":      "test",
                    "message":    "FlowCast auto-test ping — verifying endpoint is reachable.",
                    "webhook_id": str(wh.id),
                    "name":       wh.name,
                    "url":        wh.url,
                    "events":     wh.events.split(","),
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "sent_by":    "FlowCast auto-test",
                }
                await deliver_webhook(wh, "test", ping_payload, db)
                db.refresh(wh)
            except Exception as exc:
                logger.warning("Auto-test ping failed for webhook %s: %s", wh.id, exc)

    result = [_webhook_dict(wh, db=db) for wh in webhooks]

    total_deliveries = sum(wh.total_deliveries or 0 for wh in webhooks)
    total_failed     = sum(wh.failed_deliveries or 0 for wh in webhooks)
    successful       = total_deliveries - total_failed

    return {
        "webhooks": result,
        "total":    len(result),
        "summary": {
            "active":                sum(1 for wh in webhooks if wh.is_active),
            "inactive":              sum(1 for wh in webhooks if not wh.is_active),
            "total_deliveries":      total_deliveries,
            "successful_deliveries": successful,
            "total_failed":          total_failed,
            "overall_success_pct":   round(successful / total_deliveries * 100, 1)
                                     if total_deliveries else 0.0,
        },
        "available_events": list(_EVENT_DESCRIPTIONS.keys()),
        "auto_tested": freshly_seeded,
        "tip": (
            "Demo webhooks point to httpbin.org/post (public echo). "
            "Replace with your real endpoint URL via PUT /webhooks/{id}."
            if freshly_seeded else
            "Use POST /webhooks/{id}/test to send another test ping anytime."
        ),
    }


@router.get("/{webhook_id}", status_code=status.HTTP_200_OK,
            summary="Get webhook detail and delivery stats")
async def get_webhook(
    webhook_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Get full webhook detail with live delivery statistics and the last 5 delivery attempts.

    If this webhook has never been tested, a **test ping is auto-fired** to your endpoint
    so `recent_deliveries`, `last_triggered_at`, and `stats` are immediately populated
    with real data rather than zeros.
    """
    wh = _get_webhook_or_404(webhook_id, current_user.id, db)

    # Auto-fire test ping if this webhook has never been delivered
    auto_tested = False
    if (wh.total_deliveries or 0) == 0 and wh.is_active:
        try:
            ping_payload = {
                "event":      "test",
                "message":    "FlowCast auto-test ping — verifying your endpoint is reachable.",
                "webhook_id": str(wh.id),
                "name":       wh.name,
                "url":        wh.url,
                "events":     wh.events.split(","),
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "sent_by":    "FlowCast auto-test (triggered by GET detail)",
            }
            await deliver_webhook(wh, "test", ping_payload, db)
            db.refresh(wh)
            auto_tested = True
            logger.info("Auto-test ping sent for webhook %s", wh.id)
        except Exception as exc:
            logger.warning("Auto-test ping failed for webhook %s: %s", wh.id, exc)

    # Build unified response — _webhook_dict(db=db) already includes stats + recent_deliveries
    result = _webhook_dict(wh, db=db)

    # Add detail-only fields on top
    result["secret_hint"] = f"...{wh.secret[-8:]}"
    result["auto_tested"] = auto_tested
    if auto_tested:
        result["auto_test_message"] = (
            "A test ping was automatically sent to your endpoint. "
            "Check recent_deliveries below to confirm it was received."
        )

    # Remove redundant fields that _webhook_dict already covers
    result.pop("needs_test_ping",  None)
    result.pop("hint",             None)

    return result


@router.put("/{webhook_id}", status_code=status.HTTP_200_OK,
            summary="Update webhook URL, events, or active status")
def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Update the URL, subscribed events, or enabled status of a webhook."""
    wh = _get_webhook_or_404(webhook_id, current_user.id, db)
    if payload.url is not None:
        if not (payload.url.startswith("http://") or payload.url.startswith("https://")):
            raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
        wh.url = payload.url
    if payload.events is not None:
        invalid = [e for e in payload.events if e not in _VALID_EVENTS]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown events: {invalid}")
        wh.events = ",".join(sorted(set(payload.events)))
    if payload.is_active is not None:
        wh.is_active = payload.is_active
    db.commit()
    db.refresh(wh)
    return {**_webhook_dict(wh, db=db), "message": "Webhook updated."}


@router.delete("/{webhook_id}", status_code=status.HTTP_200_OK,
               summary="Delete a webhook")
def delete_webhook(
    webhook_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Permanently delete a webhook registration and its delivery history."""
    wh = _get_webhook_or_404(webhook_id, current_user.id, db)
    db.delete(wh)
    db.commit()
    return {"message": "Webhook deleted", "id": str(webhook_id)}


@router.post("/{webhook_id}/test", status_code=status.HTTP_200_OK,
             summary="Send a test ping to verify your endpoint")
async def test_webhook(
    webhook_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Fire a test ping payload to your webhook URL.

    The payload is signed with `X-FlowCast-Signature` just like a real event.
    Check the delivery log at `GET /webhooks/{id}/deliveries` to see the result.
    """
    wh = _get_webhook_or_404(webhook_id, current_user.id, db)
    if not wh.is_active:
        raise HTTPException(status_code=400, detail="Webhook is disabled — enable it first.")

    test_payload = {
        "event":      "test",
        "message":    "FlowCast webhook test ping — your endpoint is reachable!",
        "webhook_id": str(wh.id),
        "url":        wh.url,
        "events":     wh.events.split(","),
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "sent_by":    current_user.email,
    }
    delivered = await deliver_webhook(wh, "test", test_payload, db)

    return {
        "delivered":  delivered,
        "url":        wh.url,
        "message":    "Test ping delivered successfully ✓" if delivered else "Test ping failed — check the URL and try again.",
        "next_step":  f"GET /api/v1/webhooks/{wh.id}/deliveries  to see the delivery log.",
    }


@router.get("/{webhook_id}/deliveries", status_code=status.HTTP_200_OK,
            summary="Full delivery log for a webhook")
async def delivery_log(
    webhook_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Last 50 delivery attempts with status codes, timestamps, and human-readable error details.

    **Auto-fires a test ping** if this webhook has never been tested, so the log is
    never returned empty on the first call.

    - `attempted_at`  — when the HTTP request was made (never null)
    - `status_label`  — `success` / `failed` / `pending`
    - `status_icon`   — ✓ / ✗ / ⏳
    - `error_message` — plain-English explanation + fix hint for every failure code
    """
    from collections import Counter

    wh = _get_webhook_or_404(webhook_id, current_user.id, db)

    # ── Auto-fire test ping when log is empty ─────────────────────────────────
    auto_tested = False
    if (wh.total_deliveries or 0) == 0 and wh.is_active:
        try:
            ping_payload = {
                "event":      "test",
                "message":    "FlowCast auto-test ping — verifying your endpoint is reachable.",
                "webhook_id": str(wh.id),
                "name":       wh.name,
                "url":        wh.url,
                "events":     wh.events.split(","),
                "timestamp":  datetime.now(timezone.utc).isoformat(),
                "sent_by":    "FlowCast auto-test (triggered by GET deliveries)",
            }
            await deliver_webhook(wh, "test", ping_payload, db)
            db.refresh(wh)
            auto_tested = True
            logger.info("Auto-test ping sent for webhook %s (delivery log was empty)", wh.id)
        except Exception as exc:
            logger.warning("Auto-test ping failed for webhook %s: %s", wh.id, exc)

    # ── Fetch delivery records ────────────────────────────────────────────────
    deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(50)
        .all()
    )

    # ── Per-row helpers ───────────────────────────────────────────────────────
    def _status_label(d: WebhookDelivery) -> str:
        if d.http_status and 200 <= d.http_status < 300:
            return "success"
        if d.http_status is not None:
            return "failed"
        return "pending"

    def _attempted_at(d: WebhookDelivery) -> Optional[str]:
        return _dt_utc(d.delivered_at or d.created_at)

    # ── Build rows ────────────────────────────────────────────────────────────
    rows = []
    for d in deliveries:
        sl = _status_label(d)
        rows.append({
            "id":            str(d.id),
            "event_type":    d.event_type,
            "http_status":   d.http_status,
            "status_label":  sl,
            "status_icon":   "✓" if sl == "success" else ("✗" if sl == "failed" else "⏳"),
            "attempt":       d.attempt,
            "attempted_at":  _attempted_at(d),
            "delivered_at":  _dt_utc(d.delivered_at),
            "error_message": d.error_message,
            "created_at":    _dt_utc(d.created_at),
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    total   = len(rows)
    success = sum(1 for r in rows if r["status_label"] == "success")
    failed  = sum(1 for r in rows if r["status_label"] == "failed")
    rate    = round(success / total * 100, 1) if total else 0.0
    by_event = Counter(d.event_type for d in deliveries)

    result = {
        "webhook_id":   str(webhook_id),
        "webhook_name": wh.name or wh.url,
        "webhook_url":  wh.url,
        "webhook_events": wh.events.split(","),
        "summary": {
            "total":            total,
            "successful":       success,
            "failed":           failed,
            "success_rate_pct": rate,
            "last_delivery_at": rows[0]["attempted_at"] if rows else None,
            "events_breakdown": dict(by_event),
        },
        "deliveries": rows,
    }

    if auto_tested:
        result["auto_tested"] = True
        result["auto_test_message"] = (
            "No previous deliveries found — a test ping was automatically fired. "
            "Check the deliveries list above to confirm your endpoint received it."
        )

    return result


@router.post("/{webhook_id}/rotate-secret", status_code=status.HTTP_200_OK,
             summary="Rotate the signing secret for a webhook")
def rotate_secret(
    webhook_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """
    Generate a new HMAC signing secret for this webhook.

    Update your server-side verification logic with the new secret immediately —
    the old secret stops working as soon as this call returns.
    The new secret is shown **only once**.
    """
    wh = _get_webhook_or_404(webhook_id, current_user.id, db)
    new_secret = secrets.token_hex(32)
    wh.secret = new_secret
    db.commit()
    logger.info("Webhook secret rotated: user=%s webhook=%s", current_user.id, webhook_id)
    return {
        "message": "Secret rotated. Update your server immediately — old secret is invalid.",
        "new_secret": new_secret,
        "webhook_id": str(webhook_id),
    }
