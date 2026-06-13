"""Webhook delivery service — HMAC-signed HTTP POST with retry logic."""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models.webhook import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

webhook_queue: asyncio.Queue = asyncio.Queue()

_MAX_RETRIES = 3
_RETRY_DELAY = 5  # seconds

# Human-readable error messages for common HTTP status codes
_HTTP_ERROR_HINTS: dict[int, str] = {
    400: "Bad Request — your endpoint rejected the payload (check content-type / validation)",
    401: "Unauthorized — your endpoint requires authentication",
    403: "Forbidden — your endpoint denied access",
    404: "Not Found — webhook URL does not exist, update it via PUT /webhooks/{id}",
    405: "Method Not Allowed — your endpoint does not accept POST requests",
    408: "Request Timeout — your endpoint took too long to respond (>10 s)",
    422: "Unprocessable Entity — your endpoint could not parse the payload",
    429: "Too Many Requests — your endpoint is rate-limiting FlowCast",
    500: "Internal Server Error — your endpoint threw an unhandled exception",
    502: "Bad Gateway — upstream server behind your endpoint is down",
    503: "Service Unavailable — your endpoint is temporarily unavailable",
    504: "Gateway Timeout — your endpoint proxy timed out",
}


def _sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _error_message(status_code: int) -> str:
    hint = _HTTP_ERROR_HINTS.get(status_code)
    if hint:
        return f"HTTP {status_code} — {hint}"
    if 400 <= status_code < 500:
        return f"HTTP {status_code} — client error, check your webhook URL and endpoint config"
    if status_code >= 500:
        return f"HTTP {status_code} — server error on your endpoint, check its logs"
    return f"HTTP {status_code}"


async def deliver_webhook(webhook: Webhook, event_type: str, payload: dict, db: Session) -> bool:
    """Deliver a signed webhook event with up to 3 retries. Returns True if delivered."""
    body      = json.dumps(payload, default=str).encode("utf-8")
    signature = _sign_payload(webhook.secret, body)
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type":             "application/json",
        "X-FlowCast-Event":         event_type,
        "X-FlowCast-Signature":     signature,
        "X-FlowCast-Delivery-Id":   delivery_id,
        "User-Agent":               "FlowCast-Webhooks/1.0",
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        delivery = WebhookDelivery(
            webhook_id=webhook.id,
            event_type=event_type,
            payload=body.decode("utf-8"),
            attempt=attempt,
        )
        db.add(delivery)
        attempted_at = datetime.now(timezone.utc)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook.url, content=body, headers=headers)

            # Always record attempted_at — whether success or failure
            delivery.delivered_at  = attempted_at
            delivery.http_status   = resp.status_code

            if resp.is_success:
                webhook.total_deliveries  += 1
                webhook.last_triggered_at  = attempted_at
                db.commit()
                logger.info(
                    "Webhook %s delivered event=%s attempt=%d status=%d",
                    webhook.id, event_type, attempt, resp.status_code,
                )
                return True
            else:
                delivery.error_message = _error_message(resp.status_code)
                webhook.failed_deliveries += 1
                db.commit()
                logger.warning(
                    "Webhook %s failed event=%s attempt=%d status=%d — %s",
                    webhook.id, event_type, attempt, resp.status_code, delivery.error_message,
                )

        except httpx.ConnectError as exc:
            delivery.delivered_at  = attempted_at
            delivery.http_status   = None
            delivery.error_message = f"Connection refused — could not reach {webhook.url} ({exc})"
            webhook.failed_deliveries += 1
            db.commit()
            logger.warning("Webhook %s connect error attempt %d: %s", webhook.id, attempt, exc)

        except httpx.TimeoutException:
            delivery.delivered_at  = attempted_at
            delivery.http_status   = None
            delivery.error_message = f"Timeout — endpoint did not respond within 10 seconds ({webhook.url})"
            webhook.failed_deliveries += 1
            db.commit()
            logger.warning("Webhook %s timeout attempt %d", webhook.id, attempt)

        except Exception as exc:
            delivery.delivered_at  = attempted_at
            delivery.http_status   = None
            delivery.error_message = str(exc)[:500]
            webhook.failed_deliveries += 1
            db.commit()
            logger.warning("Webhook %s error attempt %d: %s", webhook.id, attempt, exc)

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(_RETRY_DELAY)

    return False


async def dispatch_event_to_webhooks(
    user_id: str, event_type: str, payload: dict, db: Session
) -> None:
    """Find all active webhooks for this user+event and deliver each."""
    webhooks = (
        db.query(Webhook)
        .filter(Webhook.user_id == user_id, Webhook.is_active == True)
        .all()
    )
    for wh in webhooks:
        subscribed = [e.strip() for e in wh.events.split(",")]
        if event_type in subscribed or "*" in subscribed:
            await deliver_webhook(wh, event_type, payload, db)
