"""
WebSocket connection manager for real-time notification delivery.

Manages active WebSocket connections per user and provides broadcasting
and targeted message delivery capabilities.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages WebSocket connections indexed by user ID.

    Maintains a dictionary of active connections and provides methods
    for sending messages to individual users or broadcasting to all.
    """

    def __init__(self) -> None:
        """Initialize the connection manager with an empty connections dictionary."""
        self.active_connections: Dict[int, WebSocket] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        """
        Accept and register a new WebSocket connection for a user.

        If the user already has an active connection, the old one is closed
        before accepting the new connection.

        Args:
            user_id: Unique identifier for the user
            websocket: WebSocket connection object

        Returns:
            None
        """
        if user_id in self.active_connections:
            old_ws = self.active_connections[user_id]
            try:
                await old_ws.close(code=1000, reason="New connection established")
            except Exception as error:
                logger.warning("Failed to close old connection for user %s: %s", user_id, error)

        await websocket.accept()
        self.active_connections[user_id] = websocket

        logger.info(
            "User %s connected to WebSocket. Total connections: %s",
            user_id,
            len(self.active_connections),
        )

    def disconnect(self, user_id: int) -> None:
        """
        Remove a user's WebSocket connection from active connections.

        Args:
            user_id: Unique identifier for the user

        Returns:
            None
        """
        if user_id in self.active_connections:
            del self.active_connections[user_id]

            logger.info(
                "User %s disconnected from WebSocket. Total connections: %s",
                user_id,
                len(self.active_connections),
            )

    async def send_to_user(self, user_id: int, message: dict) -> bool:
        """
        Send a message to a specific connected user.

        If the user is not connected or the send fails, the connection
        is cleaned up automatically.

        Args:
            user_id: Unique identifier for the user
            message: Dictionary payload to send (will be JSON serialized)

        Returns:
            True if message sent successfully, False if user not connected or send failed
        """
        if user_id not in self.active_connections:
            logger.debug("User %s not connected — message not sent", user_id)
            return False

        websocket = self.active_connections[user_id]

        try:
            await websocket.send_json(message)
            logger.debug("Message sent to user %s", user_id)
            return True

        except WebSocketDisconnect:
            logger.warning("WebSocket disconnected for user %s", user_id)
            self.disconnect(user_id)
            return False

        except RuntimeError as error:
            logger.warning("Runtime error sending to user %s: %s", user_id, error)
            self.disconnect(user_id)
            return False

        except Exception as error:
            logger.error("Failed to send message to user %s: %s", user_id, error)
            self.disconnect(user_id)
            return False

    async def broadcast(self, message: dict) -> None:
        """
        Send a message to all connected users.

        Handles disconnections gracefully by removing failed connections
        from the active pool without interrupting other broadcasts.

        Args:
            message: Dictionary payload to send to all users (will be JSON serialized)

        Returns:
            None
        """
        disconnected_users: List[int] = []

        # Copy keys to avoid modifying dict during iteration
        user_ids = list(self.active_connections.keys())

        for user_id in user_ids:
            if user_id not in self.active_connections:
                continue

            websocket = self.active_connections[user_id]

            try:
                await websocket.send_json(message)

            except WebSocketDisconnect:
                logger.warning("WebSocket disconnected during broadcast for user %s", user_id)
                disconnected_users.append(user_id)

            except Exception as error:
                logger.error("Failed to send broadcast to user %s: %s", user_id, error)
                disconnected_users.append(user_id)

        # Clean up disconnected users
        for user_id in disconnected_users:
            self.disconnect(user_id)

        logger.info(
            "Broadcast sent to %s users (removed %s dead connections)",
            len(user_ids) - len(disconnected_users),
            len(disconnected_users),
        )

    def get_connected_users(self) -> List[int]:
        """
        Retrieve list of all currently connected user IDs.

        Args:
            None

        Returns:
            List of connected user IDs
        """
        return list(self.active_connections.keys())

    def get_connection_count(self) -> int:
        """
        Get the total number of active WebSocket connections.

        Args:
            None

        Returns:
            Count of active connections
        """
        return len(self.active_connections)

    async def send_ping(self, user_id: int) -> bool:
        """
        Send a keepalive ping message to a connected user.

        Used to maintain connection health and detect stale connections.

        Args:
            user_id: Unique identifier for the user

        Returns:
            True if ping sent successfully, False otherwise
        """
        ping_message = {
            "type": "ping",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        result = await self.send_to_user(user_id, ping_message)

        if result:
            logger.debug("Ping sent to user %s", user_id)

        return result


# Global ConnectionManager instance used throughout the application
manager = ConnectionManager()
