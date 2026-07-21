"""Live Traffic Stories — human-friendly news feed of what's happening on roads right now."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.services.auth_service import get_current_user
from app.models.user import User

router = APIRouter(tags=["Live Traffic Stories"])
logger = logging.getLogger(__name__)

# WebSocket clients subscribed to live story updates
_story_sockets: set[WebSocket] = set()


@router.get("/traffic/stories", status_code=status.HTTP_200_OK)
async def get_traffic_stories(
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Get the current traffic stories feed — AI-generated news cards from live road events.

    Stories are refreshed every 5 minutes. Each card includes a headline, 2-sentence summary,
    severity level, location, and an optional action tip.
    """
    from app.services.story_generator import get_cached_stories, is_cache_stale, refresh_stories

    if is_cache_stale():
        stories = await refresh_stories(db)
    else:
        stories = get_cached_stories()

    return {
        "stories": stories,
        "count": len(stories),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@router.websocket("/traffic/ws/stories")
async def stories_websocket(websocket: WebSocket):
    """WebSocket: receive new traffic story cards as they are generated (every ~5 min).

    Connect and receive a `stories_update` message whenever the story feed refreshes.
    """
    await websocket.accept()
    _story_sockets.add(websocket)
    logger.info("Stories WS connected — %d clients", len(_story_sockets))

    # Send current stories immediately on connect
    db = SessionLocal()
    try:
        from app.services.story_generator import get_cached_stories, is_cache_stale, refresh_stories
        if is_cache_stale():
            stories = await refresh_stories(db)
        else:
            stories = get_cached_stories()
        await websocket.send_json({
            "type": "stories_update",
            "stories": stories,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        db.close()

    try:
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_json({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()})
            except Exception:
                break
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _story_sockets.discard(websocket)
        logger.info("Stories WS disconnected — %d clients remaining", len(_story_sockets))


async def broadcast_stories(stories: list[dict]) -> None:
    """Broadcast updated stories to all connected WebSocket clients."""
    if not _story_sockets:
        return
    payload = {
        "type": "stories_update",
        "stories": stories,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    dead = set()
    for ws in list(_story_sockets):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    _story_sockets.difference_update(dead)
