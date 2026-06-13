"""
Developer Portal — API key management.

POST   /developer/keys                   — create a new API key (auth required)
GET    /developer/keys                   — list all my API keys
GET    /developer/keys/{key_id}          — single key detail + today's usage
DELETE /developer/keys/{key_id}          — revoke a key
POST   /developer/keys/{key_id}/rotate   — generate a fresh key (old one revoked)
GET    /developer/keys/{key_id}/usage    — daily usage stats for the last 30 days
GET    /developer/scopes                 — list available permission scopes
GET    /developer/status                 — validate a key passed in X-API-Key header
"""

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.api_key import ApiKey, _TIER_LIMITS, _VALID_SCOPES
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/developer", tags=["Developer Portal"])
logger = logging.getLogger(__name__)

_PREFIX = "fc_"   # FlowCast key prefix


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_key() -> tuple[str, str, str]:
    """Return (raw_key, key_prefix, key_hash)."""
    raw      = _PREFIX + secrets.token_urlsafe(32)
    prefix   = raw[:12]
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, prefix, key_hash


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _serialize(key: ApiKey, show_prefix_only: bool = True) -> dict:
    return {
        "id":          str(key.id),
        "name":        key.name,
        "key_prefix":  key.key_prefix,
        "key_display": f"{key.key_prefix}{'*' * 20}",
        "scopes":      key.scopes.split(),
        "tier":        key.tier,
        "daily_limit": key.daily_limit,
        "daily_count": key.daily_count,
        "is_active":   key.is_active,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "expires_at":  key.expires_at.isoformat() if key.expires_at else None,
        "created_at":  key.created_at.isoformat(),
    }


# ── Request schemas ───────────────────────────────────────────────────────────

class KeyCreate(BaseModel):
    name:   str  = Field(..., min_length=1, max_length=100,
                         description="Friendly label for this key, e.g. 'My Dashboard App'")
    scopes: list[str] = Field(
        default=["read:traffic"],
        description=f"Permission scopes. Available: {sorted(_VALID_SCOPES)}",
    )
    tier:       Optional[str] = Field("free", description="free | pro | enterprise")
    expires_days: Optional[int] = Field(None, ge=1, le=365,
                                        description="Key expiry in days from now. Omit for no expiry.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/scopes", status_code=status.HTTP_200_OK,
            summary="List all available API permission scopes")
def list_scopes() -> dict:
    """Return all permission scopes that can be granted to an API key."""
    return {
        "scopes": sorted(_VALID_SCOPES),
        "description": {
            "read:traffic":    "Read traffic records and congestion data",
            "write:traffic":   "Submit new traffic observations",
            "read:eta":        "Query ETA calculations",
            "read:analytics":  "Access analytics and heatmap endpoints",
            "read:fleet":      "View fleet vehicles and assignments",
            "write:fleet":     "Manage fleet vehicles and drivers",
            "read:incidents":  "Browse active road incidents",
            "write:incidents": "Report and vote on incidents",
        },
    }


@router.post("/keys", status_code=status.HTTP_201_CREATED,
             summary="Create a new API key")
def create_key(
    body: KeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Generate a new API key for programmatic access.

    **The raw key is shown only once in the response — store it securely.**

    Subsequent requests show only the `key_prefix` (first 12 chars) for identification.
    """
    # Validate scopes
    invalid = [s for s in body.scopes if s not in _VALID_SCOPES]
    if invalid:
        raise HTTPException(400, detail=f"Unknown scopes: {invalid}. Use GET /developer/scopes.")

    tier = body.tier or "free"
    if tier not in _TIER_LIMITS:
        raise HTTPException(400, detail=f"tier must be one of: {list(_TIER_LIMITS.keys())}")

    # Limit: max 10 active keys per user
    existing_count = db.query(ApiKey).filter(
        ApiKey.user_id == current_user.id, ApiKey.is_active == True
    ).count()
    if existing_count >= 10:
        raise HTTPException(400, detail="Maximum 10 active API keys per user. Revoke one first.")

    raw_key, prefix, key_hash = _generate_key()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_days)
        if body.expires_days else None
    )

    key = ApiKey(
        user_id=current_user.id,
        name=body.name,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=" ".join(body.scopes),
        tier=tier,
        is_active=True,
        daily_limit=_TIER_LIMITS[tier],
        daily_count=0,
        expires_at=expires_at,
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    logger.info("API key created: user=%s name='%s' tier=%s", current_user.email, body.name, tier)

    return {
        "message": "API key created. Copy the raw_key now — it will NOT be shown again.",
        "raw_key": raw_key,
        "key": _serialize(key),
        "usage_header": "X-API-Key: " + raw_key,
    }


@router.get("/keys", status_code=status.HTTP_200_OK,
            summary="List all your API keys")
def list_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return all API keys belonging to the authenticated user."""
    keys = db.query(ApiKey).filter(ApiKey.user_id == current_user.id).order_by(
        ApiKey.created_at.desc()
    ).all()
    return {
        "total": len(keys),
        "active": sum(1 for k in keys if k.is_active),
        "keys": [_serialize(k) for k in keys],
    }


@router.get("/keys/{key_id}", status_code=status.HTTP_200_OK,
            summary="Get a single API key and today's usage")
def get_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return detail and today's request count for one API key."""
    key = _get_key_or_404(key_id, current_user.id, db)
    return _serialize(key)


@router.delete("/keys/{key_id}", status_code=status.HTTP_200_OK,
               summary="Revoke an API key")
def revoke_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Permanently deactivate an API key. All requests using it will be rejected immediately."""
    key = _get_key_or_404(key_id, current_user.id, db)
    key.is_active = False
    db.commit()
    logger.info("API key revoked: user=%s key=%s", current_user.email, key.key_prefix)
    return {"message": f"Key '{key.name}' ({key.key_prefix}…) revoked.", "key_id": str(key_id)}


@router.post("/keys/{key_id}/rotate", status_code=status.HTTP_200_OK,
             summary="Rotate an API key — old key is revoked, new one issued")
def rotate_key(
    key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Invalidate the current key and issue a brand-new one with the same name, scopes, and tier.

    **Copy the new raw_key immediately — it is shown only once.**
    """
    old_key = _get_key_or_404(key_id, current_user.id, db)
    old_key.is_active = False

    raw_key, prefix, key_hash = _generate_key()
    new_key = ApiKey(
        user_id=current_user.id,
        name=old_key.name,
        key_prefix=prefix,
        key_hash=key_hash,
        scopes=old_key.scopes,
        tier=old_key.tier,
        is_active=True,
        daily_limit=old_key.daily_limit,
        daily_count=0,
        expires_at=old_key.expires_at,
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    logger.info("API key rotated: user=%s old=%s new=%s", current_user.email, old_key.key_prefix, prefix)
    return {
        "message": "Key rotated. Old key is revoked. Copy the new raw_key — shown only once.",
        "raw_key": raw_key,
        "key": _serialize(new_key),
    }


@router.get("/status", status_code=status.HTTP_200_OK,
            summary="Validate an API key passed via X-API-Key header")
def validate_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> dict:
    """
    Test whether an API key is valid, active, and within rate limits.

    Pass the key in the `X-API-Key` request header.
    This endpoint does NOT consume a request from the daily quota.
    """
    if not x_api_key:
        raise HTTPException(401, detail="Provide your API key in the X-API-Key header.")

    key_obj = _lookup_raw_key(x_api_key, db)
    if key_obj is None:
        raise HTTPException(401, detail="Invalid API key.")
    if not key_obj.is_active:
        raise HTTPException(403, detail="API key has been revoked.")
    if key_obj.expires_at and key_obj.expires_at < datetime.now(timezone.utc):
        raise HTTPException(403, detail="API key has expired.")

    _reset_daily_count_if_needed(key_obj, db)
    remaining = None if key_obj.daily_limit is None else max(0, key_obj.daily_limit - key_obj.daily_count)

    return {
        "valid": True,
        "name":           key_obj.name,
        "tier":           key_obj.tier,
        "scopes":         key_obj.scopes.split(),
        "daily_limit":    key_obj.daily_limit,
        "daily_used":     key_obj.daily_count,
        "daily_remaining": remaining,
        "last_used_at":   key_obj.last_used_at.isoformat() if key_obj.last_used_at else None,
    }


# ── FastAPI dependency for API-key auth ───────────────────────────────────────

def get_api_key_user(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Dependency: resolve an API key to its owner User.

    Returns None if no X-API-Key header present (caller falls through to JWT auth).
    Raises 401/403/429 on invalid / revoked / rate-limited keys.
    """
    if not x_api_key:
        return None

    key_obj = _lookup_raw_key(x_api_key, db)
    if key_obj is None:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    if not key_obj.is_active:
        raise HTTPException(status_code=403, detail="API key revoked.")
    if key_obj.expires_at and key_obj.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="API key expired.")

    _reset_daily_count_if_needed(key_obj, db)

    if key_obj.daily_limit is not None and key_obj.daily_count >= key_obj.daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Daily API limit of {key_obj.daily_limit} requests reached. Upgrade to pro.",
            headers={"Retry-After": "86400"},
        )

    # Increment usage
    key_obj.daily_count   += 1
    key_obj.last_used_at   = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception:
        db.rollback()

    return db.query(User).filter(User.id == key_obj.user_id).first()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_key_or_404(key_id: uuid.UUID, user_id: uuid.UUID, db: Session) -> ApiKey:
    key = db.query(ApiKey).filter(
        ApiKey.id == key_id, ApiKey.user_id == user_id
    ).first()
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found.")
    return key


def _lookup_raw_key(raw_key: str, db: Session) -> Optional[ApiKey]:
    h = hashlib.sha256(raw_key.encode()).hexdigest()
    return db.query(ApiKey).filter(ApiKey.key_hash == h).first()


def _reset_daily_count_if_needed(key: ApiKey, db: Session) -> None:
    today = datetime.now(timezone.utc).date()
    if key.count_date is None or key.count_date.date() < today:
        key.daily_count = 0
        key.count_date  = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:
            db.rollback()
