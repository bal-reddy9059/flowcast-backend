"""Claude AI integration service — powers all FlowCast AI features."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_client = None

SYSTEM_PROMPT = (
    "You are FlowCast AI, an expert traffic intelligence assistant for India. "
    "You provide real-time, data-driven advice about road traffic, commutes, and routes across Indian cities. "
    "Rules: "
    "(1) Lead with the answer — give the recommendation first, explain second. "
    "(2) Be concise — 2-4 sentences for simple questions, 5-6 max for complex ones. "
    "(3) Use specific numbers when available (e.g., '18 min delay' not 'some delay'). "
    "(4) Reference real Indian roads, junctions, and landmarks naturally (NH48, Silk Board, JNTU, etc.). "
    "(5) Factor in India-specific patterns: peak hours 8-10am and 5-8pm, monsoon slowdowns, festival traffic. "
    "(6) Always end with a clear action when relevant ('Leave by 5:30pm', 'Take the Expressway')."
)


def _get_client():
    global _client
    if _client is None:
        try:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                _client = anthropic.Anthropic(api_key=api_key)
            else:
                logger.warning("ANTHROPIC_API_KEY not set — AI features will use fallback mode")
        except ImportError:
            logger.warning("anthropic package not installed — AI features unavailable")
    return _client


def build_traffic_context(location: str, db: Session, user_id: Optional[str] = None) -> str:
    """Build a compact snapshot of live traffic + user data for Claude context."""
    from app.models.predictor import TrafficRecord, Incident
    from app.services.city_aliases import location_filter

    lines = []
    since = datetime.now(timezone.utc) - timedelta(hours=2)

    records = (
        db.query(TrafficRecord)
        .filter(location_filter(TrafficRecord.location, location), TrafficRecord.created_at >= since)
        .order_by(TrafficRecord.created_at.desc())
        .limit(8)
        .all()
    )

    if records:
        latest = records[0]
        speeds = [r.average_speed for r in records if r.average_speed]
        avg_speed = round(sum(speeds) / len(speeds), 1) if speeds else None
        speed_std = None
        if len(speeds) > 1:
            mean = sum(speeds) / len(speeds)
            speed_std = round((sum((s - mean) ** 2 for s in speeds) / len(speeds)) ** 0.5, 1)
        lines.append(
            f"Traffic at {location}: congestion={latest.congestion_level}, "
            f"avg_speed={avg_speed} km/h, speed_variability={speed_std} km/h"
        )
    else:
        lines.append(f"No recent traffic data available for {location}")

    incidents = (
        db.query(Incident)
        .filter(location_filter(Incident.location, location), Incident.is_active.is_(True))
        .limit(3)
        .all()
    )
    for inc in incidents:
        lines.append(f"Active incident at {inc.location}: {inc.incident_type}, severity={inc.severity}")

    if user_id:
        from app.models.trip import TripHistory
        trips = (
            db.query(TripHistory)
            .filter(TripHistory.user_id == user_id)
            .order_by(TripHistory.created_at.desc())
            .limit(15)
            .all()
        )
        if trips:
            etas = [t.predicted_eta_minutes for t in trips if t.predicted_eta_minutes]
            avg_eta = round(sum(etas) / len(etas), 1) if etas else 0
            routes = set(f"{t.origin_name}→{t.destination_name}" for t in trips)
            lines.append(f"User has {len(trips)} recent trips, avg ETA {avg_eta} min, routes: {', '.join(list(routes)[:3])}")

    now = datetime.now(timezone.utc)
    lines.append(f"Current time (IST): {now.strftime('%A %H:%M')}")

    return "\n".join(lines)


def ask_claude(context: str, user_message: str) -> str:
    """Call Claude with traffic context. Falls back gracefully if no API key."""
    client = _get_client()
    if client is None:
        return _fallback_chat(user_message, context)
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{
                "role": "user",
                "content": [{"type": "text", "text": f"Traffic data:\n{context}\n\nQuestion: {user_message}", "cache_control": {"type": "ephemeral"}}],
            }],
        )
        return response.content[0].text
    except Exception as exc:
        logger.error("Claude API error: %s", exc)
        return _fallback_chat(user_message, context)


def generate_narrative(route_context: str) -> str:
    """Generate a 2-3 sentence human-readable route briefing."""
    client = _get_client()
    if client is None:
        return _fallback_narrative(route_context)
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=250,
            system=[{"type": "text", "text": (
                "Write a route briefing like a traffic reporter. 2-3 sentences max. "
                "Mention the most important condition (biggest delay or good news) and give a specific time or action tip. "
                "Be direct and use numbers."
            ), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Write a briefing for this route:\n{route_context}"}],
        )
        return response.content[0].text
    except Exception as exc:
        logger.error("Narrative generation error: %s", exc)
        return _fallback_narrative(route_context)


def generate_stories(events_context: str) -> list:
    """Generate traffic story cards from events. Returns list of dicts."""
    client = _get_client()
    if client is None:
        return []
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
            system=[{"type": "text", "text": (
                "Generate traffic news cards from events. Return a JSON array of 3-5 objects, each with: "
                "headline (under 10 words, punchy), body (2 sentences), severity (low/medium/high), "
                "location (city name), tip (short action, optional). Return only valid JSON, no markdown."
            ), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Generate story cards from:\n{events_context}"}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as exc:
        logger.error("Story generation error: %s", exc)
        return []


def generate_fleet_insights(fleet_context: str) -> list:
    """Generate AI fleet insights. Returns list of insight dicts."""
    client = _get_client()
    if client is None:
        return [{"type": "config", "title": "Set ANTHROPIC_API_KEY for AI insights", "detail": "Add your Anthropic API key to .env to enable fleet AI analysis.", "action": "Set ANTHROPIC_API_KEY in .env", "priority": "high"}]
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=700,
            system=[{"type": "text", "text": (
                "Analyze fleet data and return actionable insights for fleet managers as a JSON array. "
                "Each insight object: type (fuel_waste/route_optimization/scheduling/driver_behavior), "
                "title (under 10 words), detail (2-3 sentences with specific numbers), action (1 short sentence), "
                "priority (high/medium/low). Return only valid JSON, no markdown."
            ), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Analyze this fleet data:\n{fleet_context}"}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as exc:
        logger.error("Fleet insights error: %s", exc)
        return []


def generate_multimodal_plan(journey_context: str) -> dict:
    """Generate an AI-powered multi-modal journey plan."""
    client = _get_client()
    if client is None:
        return {"error": "Set ANTHROPIC_API_KEY to enable multi-modal AI planning."}
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
            system=[{"type": "text", "text": (
                "Plan optimal multi-modal journeys in Indian cities. Available modes: driving, metro, "
                "bus, auto_rickshaw, walking, cycling. Return a JSON object with: "
                "segments (array of {mode, from, to, duration_min, cost_inr, notes}), "
                "total_duration_min (number), vs_driving_only_min (savings, positive = faster), "
                "total_cost_inr (number), carbon_saved_kg (number vs driving), "
                "summary (1 sentence). Use realistic Indian fares and travel times. Return only valid JSON."
            ), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Plan a multi-modal journey:\n{journey_context}"}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as exc:
        logger.error("Multimodal planning error: %s", exc)
        return {"error": str(exc)}


def generate_departure_coaching(coaching_context: str) -> dict:
    """Generate a personalized departure recommendation from personal trip history."""
    client = _get_client()
    if client is None:
        return {"error": "Set ANTHROPIC_API_KEY to enable personalized departure coaching."}
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=[{"type": "text", "text": (
                "You are a personal commute coach who analyzes trip history data. "
                "Return a JSON object with: recommended_window (e.g., '17:45 – 18:00'), "
                "confidence_pct (0-100), reasoning (2-3 sentences mentioning specific patterns), "
                "alternatives (array of {time, saves_min, probability_pct}), "
                "today_warning (null or short string about today's specific risk). "
                "Return only valid JSON."
            ), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Analyze this commute data and recommend departure time:\n{coaching_context}"}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as exc:
        logger.error("Departure coaching error: %s", exc)
        return {"error": str(exc)}


# ── Fallbacks ──────────────────────────────────────────────────────────────────

def _fallback_chat(user_message: str, context: str) -> str:
    msg = user_message.lower()
    if any(w in msg for w in ["leave", "go now", "should i", "depart"]):
        if "high" in context:
            return "Traffic is currently heavy. If your schedule allows, waiting 30–45 minutes may help. Check the forecast endpoint for a precise window."
        return "Traffic looks manageable right now. Conditions are reasonable for heading out."
    if any(w in msg for w in ["route", "way", "path", "how to get"]):
        return "Use the route optimization endpoint (/api/v1/routes/optimize) for live turn-by-turn options. Set ANTHROPIC_API_KEY for full AI advice."
    return "FlowCast AI is ready! Set ANTHROPIC_API_KEY in your .env file to enable full natural language responses."


def _fallback_narrative(route_context: str) -> str:
    if "high" in route_context:
        return "Traffic on this route is currently heavy with significant delays. Plan for extra travel time and consider departure timing carefully."
    if "medium" in route_context:
        return "Moderate traffic conditions on this route. Minor delays possible — add 10–15 minutes to your estimate."
    return "Traffic is flowing well on this route. Good conditions for travel right now."
