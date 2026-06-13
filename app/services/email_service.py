"""
Email notification delivery for FlowCast.

Uses Python stdlib smtplib (wrapped in asyncio executor) — no extra packages needed.
Configure via environment variables:

  SMTP_HOST      (default: smtp.gmail.com)
  SMTP_PORT      (default: 587)
  SMTP_USER      sender email address
  SMTP_PASSWORD  sender password / app-password
  SMTP_FROM_NAME (default: FlowCast Alerts)

If SMTP_USER is not set, email sending is silently skipped (graceful degradation).
"""

import asyncio
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

_SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
_SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER     = os.getenv("SMTP_USER", "")
_SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
_FROM_NAME     = os.getenv("SMTP_FROM_NAME", "FlowCast Alerts")


# ── HTML templates ─────────────────────────────────────────────────────────────

def _base_template(title: str, body_html: str, unsubscribe_url: str = "#") -> str:
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body      {{ font-family: Arial, sans-serif; background:#f4f4f4; margin:0; padding:0; }}
    .wrap     {{ max-width:600px; margin:20px auto; background:#fff;
                 border-radius:8px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.1); }}
    .header   {{ background:#1a73e8; color:#fff; padding:20px 24px; }}
    .header h1{{ margin:0; font-size:20px; }}
    .body     {{ padding:24px; color:#333; line-height:1.6; }}
    .badge    {{ display:inline-block; padding:3px 10px; border-radius:12px;
                 font-size:12px; font-weight:bold; color:#fff; }}
    .high     {{ background:#d32f2f; }}
    .medium   {{ background:#f57c00; }}
    .low      {{ background:#388e3c; }}
    .footer   {{ background:#f4f4f4; padding:12px 24px; font-size:12px; color:#888;
                 text-align:center; }}
    .btn      {{ display:inline-block; margin-top:16px; padding:10px 20px;
                 background:#1a73e8; color:#fff; text-decoration:none;
                 border-radius:4px; font-size:14px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header"><h1>🚦 FlowCast — {title}</h1></div>
    <div class="body">{body_html}</div>
    <div class="footer">
      You received this because you have email alerts enabled in FlowCast.<br>
      <a href="{unsubscribe_url}">Unsubscribe</a>
    </div>
  </div>
</body>
</html>
"""


def _congestion_alert_html(title: str, message: str, location: str, severity: str) -> str:
    sev_class = {"high": "high", "medium": "medium", "low": "low"}.get(severity, "medium")
    return _base_template(title, f"""
      <p><strong>{title}</strong></p>
      <p>
        <span class="badge {sev_class}">{severity.upper()}</span>
        &nbsp; <strong>{location}</strong>
      </p>
      <p>{message}</p>
      <p style="color:#666;font-size:13px;">
        Open the FlowCast app for live updates and alternative routes.
      </p>
    """)


def _departure_alert_html(title: str, message: str, location: str) -> str:
    return _base_template(title, f"""
      <p>⏰ <strong>Time to leave!</strong></p>
      <p>{message}</p>
      <p><strong>Destination:</strong> {location}</p>
      <p style="color:#666;font-size:13px;">
        Check the FlowCast app for real-time ETA and route conditions.
      </p>
    """)


def _report_ready_html(title: str, message: str) -> str:
    return _base_template(title, f"""
      <p>📊 <strong>{title}</strong></p>
      <p>{message}</p>
    """)


def _generic_html(title: str, message: str) -> str:
    return _base_template(title, f"<p>{message}</p>")


# ── Core send function ─────────────────────────────────────────────────────────

def _send_email_sync(to_address: str, subject: str, html_body: str) -> bool:
    """Synchronous SMTP send — call via asyncio executor."""
    if not _SMTP_USER or not _SMTP_PASSWORD:
        logger.debug("Email skipped (SMTP_USER not configured): %s", subject)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{_FROM_NAME} <{_SMTP_USER}>"
    msg["To"]      = to_address
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(_SMTP_USER, _SMTP_PASSWORD)
            server.sendmail(_SMTP_USER, to_address, msg.as_string())
        logger.info("Email sent to %s — %s", to_address, subject)
        return True
    except smtplib.SMTPException as exc:
        logger.warning("SMTP error sending to %s: %s", to_address, exc)
        return False
    except Exception as exc:
        logger.warning("Email send failed for %s: %s", to_address, exc)
        return False


async def send_email(to_address: str, subject: str, html_body: str) -> bool:
    """Async wrapper — runs SMTP in a thread so it doesn't block the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_email_sync, to_address, subject, html_body)


# ── Typed helpers called by notification_service ──────────────────────────────

async def send_congestion_alert(
    to_email: str, title: str, message: str, location: str, severity: str
) -> bool:
    html = _congestion_alert_html(title, message, location, severity)
    return await send_email(to_email, f"🚦 {title}", html)


async def send_departure_alert(
    to_email: str, title: str, message: str, location: str
) -> bool:
    html = _departure_alert_html(title, message, location)
    return await send_email(to_email, f"⏰ {title}", html)


async def send_report_ready(to_email: str, title: str, message: str) -> bool:
    html = _report_ready_html(title, message)
    return await send_email(to_email, f"📊 {title}", html)


async def send_generic_notification(to_email: str, title: str, message: str) -> bool:
    html = _generic_html(title, message)
    return await send_email(to_email, title, html)


def smtp_configured() -> bool:
    """Return True if SMTP credentials are set in the environment."""
    return bool(_SMTP_USER and _SMTP_PASSWORD)
