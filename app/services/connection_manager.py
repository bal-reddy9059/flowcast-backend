"""
WebSocket connection manager for real-time notification delivery.

Manages active WebSocket connections per user and provides broadcasting
and targeted message delivery capabilities.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections indexed by a primary key.

    Clients often connect with email while the server pushes with UUID — aliases
    map either identifier to the same live socket.
    """

    def __init__(self) -> None:
        self.active_connections: Dict[str, WebSocket] = {}
        # alias key → primary connection key
        self._aliases: Dict[str, str] = {}
        self._send_locks: Dict[str, asyncio.Lock] = {}

    def _normalize(self, key: str) -> str:
        return str(key).strip()

    def _register_aliases(self, primary: str, aliases: Optional[List[str]]) -> None:
        keys = [primary]
        if aliases:
            keys.extend(aliases)
        for raw in keys:
            if not raw:
                continue
            k = self._normalize(raw)
            self._aliases[k] = primary
            low = k.lower()
            if low != k:
                self._aliases[low] = primary

    def _resolve_key(self, user_id: str) -> Optional[str]:
        """Resolve UUID/email (or comma-separated candidates) to an active socket key."""
        candidates = [c.strip() for c in str(user_id).split(",") if c.strip()]
        expanded: list[str] = []
        for c in candidates:
            if c not in expanded:
                expanded.append(c)
            low = c.lower()
            if low not in expanded:
                expanded.append(low)

        for c in expanded:
            if c in self.active_connections:
                return c
            primary = self._aliases.get(c)
            if primary and primary in self.active_connections:
                return primary
        return None

    @staticmethod
    def _is_connected(websocket: WebSocket) -> bool:
        return (
            websocket.application_state == WebSocketState.CONNECTED
            and websocket.client_state != WebSocketState.DISCONNECTED
        )

    async def connect(
        self,
        user_id: str,
        websocket: WebSocket,
        aliases: Optional[List[str]] = None,
    ) -> None:
        """
        Accept and register a new WebSocket connection for a user.

        The new socket is registered before the old one is closed. This ensures
        a late disconnect event from the old socket cannot remove the replacement.
        """
        primary = self._normalize(user_id)
        await websocket.accept()

        old_ws = self.active_connections.get(primary)

        # Drop stale aliases that pointed at this primary
        self._aliases = {k: v for k, v in self._aliases.items() if v != primary}

        self.active_connections[primary] = websocket
        self._send_locks.setdefault(primary, asyncio.Lock())
        self._register_aliases(primary, aliases)

        if old_ws is not None and old_ws is not websocket and self._is_connected(old_ws):
            try:
                await old_ws.close(code=4001, reason="Connection replaced")
            except (RuntimeError, WebSocketDisconnect):
                logger.debug("Old WebSocket for user %s was already closed", primary)
            except Exception as error:
                logger.warning("Failed to close old connection for user %s: %s", primary, error)

        logger.info(
            "User %s connected to WebSocket. Total connections: %s",
            primary,
            len(self.active_connections),
        )

    def disconnect(self, user_id: str, websocket: Optional[WebSocket] = None) -> None:
        """Remove a connection, unless it has already been replaced by a newer socket."""
        primary = self._resolve_key(user_id) or self._normalize(user_id)
        active = self.active_connections.get(primary)

        if active is not None and (websocket is None or active is websocket):
            del self.active_connections[primary]
            self._send_locks.pop(primary, None)
            self._aliases = {k: v for k, v in self._aliases.items() if v != primary}
            logger.info(
                "User %s disconnected from WebSocket. Total connections: %s",
                primary,
                len(self.active_connections),
            )

    async def send_to_user(self, user_id: str, message: dict) -> bool:
        """
        Send a message to a specific connected user.

        ``user_id`` may be a UUID, email, or comma-separated candidates.
        Aliases registered at connect time are also tried.
        """
        target_key = self._resolve_key(user_id)
        if target_key is None:
            logger.debug("User %s not connected — message not sent", user_id)
            return False

        websocket = self.active_connections[target_key]
        return await self.send_to_connection(target_key, websocket, message)

    async def send_to_connection(
        self,
        user_id: str,
        websocket: WebSocket,
        message: dict,
    ) -> bool:
        """Send only if ``websocket`` is still this user's active connection."""
        target_key = self._resolve_key(user_id)
        if target_key is None or self.active_connections.get(target_key) is not websocket:
            return False

        lock = self._send_locks.setdefault(target_key, asyncio.Lock())
        async with lock:
            if (
                self.active_connections.get(target_key) is not websocket
                or not self._is_connected(websocket)
            ):
                self.disconnect(target_key, websocket)
                return False
            try:
                await websocket.send_json(message)
                logger.debug("Message sent to user %s (key=%s)", user_id, target_key)
                return True
            except (WebSocketDisconnect, RuntimeError) as error:
                logger.debug("WebSocket closed while sending to user %s: %s", target_key, error)
                self.disconnect(target_key, websocket)
                return False
            except Exception as error:
                logger.error("Failed to send message to user %s: %s", target_key, error)
                self.disconnect(target_key, websocket)
                return False

    async def broadcast(self, message: dict) -> None:
        """Send a message to all connected users."""
        user_ids = list(self.active_connections.keys())
        sent = 0

        for uid in user_ids:
            websocket = self.active_connections.get(uid)
            if websocket is not None and await self.send_to_connection(uid, websocket, message):
                sent += 1

        logger.info(
            "Broadcast sent to %s users (removed %s dead connections)",
            sent,
            len(user_ids) - sent,
        )

    def get_connected_users(self) -> List[str]:
        """Retrieve list of all currently connected primary keys."""
        return list(self.active_connections.keys())

    def get_connection_count(self) -> int:
        """Get the total number of active WebSocket connections."""
        return len(self.active_connections)

    async def send_ping(
        self,
        user_id: str,
        websocket: Optional[WebSocket] = None,
    ) -> bool:
        """Send a keepalive ping message to a connected user."""
        target_key = self._resolve_key(user_id)
        if target_key is None:
            return False
        if websocket is not None and self.active_connections.get(target_key) is not websocket:
            return False
        ping_message = {
            "type": "ping",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        active = self.active_connections.get(target_key)
        if active is None:
            return False
        result = await self.send_to_connection(target_key, active, ping_message)
        if result:
            logger.debug("Ping sent to user %s", user_id)
        return result


# Global ConnectionManager instance used throughout the application
manager = ConnectionManager()
