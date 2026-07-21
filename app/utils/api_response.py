"""
Standard API response envelope used across FlowCast endpoints.

Format:
{
  "success": true,
  "data": { ... },
  "timestamp": "2026-07-14T19:00:00+05:30",   // IST
  "message": "optional human-readable note"
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_IST = ZoneInfo("Asia/Kolkata")

# Docs / schema / static — leave untouched
_SKIP_PREFIXES = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/health",
)


def to_ist_iso(dt: datetime | None = None) -> str:
    """Return an ISO-8601 timestamp in Asia/Kolkata."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()


def api_success(
    data: Any = None,
    *,
    message: Optional[str] = None,
    **extra: Any,
) -> dict:
    """Build a standard success response."""
    body: dict[str, Any] = {
        "success": True,
        "data": data,
        "timestamp": to_ist_iso(),
    }
    if message is not None:
        body["message"] = message
    if extra:
        body.update(extra)
    return body


def api_error(
    error: str,
    *,
    code: Optional[str] = None,
    details: Any = None,
) -> dict:
    """Build a standard error payload (use with JSONResponse / HTTPException detail)."""
    body: dict[str, Any] = {
        "success": False,
        "error": error,
        "timestamp": to_ist_iso(),
    }
    if code is not None:
        body["code"] = code
    if details is not None:
        body["details"] = details
    return body


def _already_enveloped(payload: Any) -> bool:
    return isinstance(payload, dict) and "success" in payload


def wrap_payload(payload: Any, *, status_code: int) -> dict[str, Any]:
    """Normalize any JSON body into the standard envelope."""
    if _already_enveloped(payload):
        if "timestamp" not in payload:
            payload = {**payload, "timestamp": to_ist_iso()}
        return payload

    if 200 <= status_code < 300:
        return api_success(data=payload)

    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, list):
        return api_error("Validation error", code="validation_error", details=detail)
    if isinstance(detail, dict):
        return api_error(
            str(detail.get("message") or detail.get("error") or "Request failed"),
            details=detail,
        )
    return api_error(str(detail) if detail is not None else "Request failed")


class ApiResponseEnvelopeMiddleware:
    """Pure ASGI middleware — wrap every JSON HTTP response in the standard envelope."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        status_code = 200
        response_headers: list[tuple[bytes, bytes]] = []
        body_parts: list[bytes] = []
        started = False
        sent = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_headers, started, sent

            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers") or [])
                started = True
                # Defer until full body is buffered (JSON rewrite).
                return

            if message["type"] == "http.response.body":
                body_parts.append(message.get("body") or b"")
                if message.get("more_body"):
                    return

                headers_dict = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in response_headers
                }
                content_type = headers_dict.get("content-type", "")
                raw = b"".join(body_parts)

                if "application/json" not in content_type:
                    await send({
                        "type": "http.response.start",
                        "status": status_code,
                        "headers": response_headers,
                    })
                    await send({
                        "type": "http.response.body",
                        "body": raw,
                        "more_body": False,
                    })
                    sent = True
                    return

                try:
                    payload = json.loads(raw) if raw else None
                except Exception:
                    await send({
                        "type": "http.response.start",
                        "status": status_code,
                        "headers": response_headers,
                    })
                    await send({
                        "type": "http.response.body",
                        "body": raw,
                        "more_body": False,
                    })
                    sent = True
                    return

                wrapped = wrap_payload(payload, status_code=status_code)
                response = JSONResponse(content=wrapped, status_code=status_code)
                # Preserve CORS / custom headers from the original response.
                for key, value in response_headers:
                    k = key.decode("latin-1").lower()
                    if k in ("content-length", "content-type"):
                        continue
                    response.raw_headers.append((key, value))
                await response(scope, receive, send)
                sent = True
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)

        # Edge case: start was deferred but no body arrived
        if started and not sent:
            await send({
                "type": "http.response.start",
                "status": status_code,
                "headers": response_headers,
            })
            await send({"type": "http.response.body", "body": b"", "more_body": False})
