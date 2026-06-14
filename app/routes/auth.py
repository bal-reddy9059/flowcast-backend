"""Authentication routes for FlowCast."""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from collections import Counter
from datetime import timedelta as _td
from typing import Optional

from app.models.notification import Notification
from app.models.predictor import Incident, TrafficRecord
from app.models.route import SavedRoute
from app.schemas.user import TokenResponse, UserCreate, UserLogin, UserResponse
from app.services.auth_service import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    password: Optional[str] = Field(None, min_length=8, max_length=100)

    model_config = ConfigDict(json_schema_extra={
        "example": {"full_name": "Ravi Kumar", "password": "NewSecurePass123"}
    })


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Register new FlowCast commuter account."""
    query = select(User).where(User.email == payload.email)
    result = db.execute(query)
    existing_user = result.scalars().first()
    if existing_user is not None:
        logger.warning("Registration failed: email already registered %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(
        {"sub": user.email, "user_id": str(user.id)},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    logger.info("New user registered: %s", user.email)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
def login_user(
    payload: UserLogin,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Login to FlowCast account."""
    query = select(User).where(User.email == payload.email)
    result = db.execute(query)
    user = result.scalars().first()

    if user is not None and user.auth_provider == "google" and not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account uses Google Sign-In. Please log in with Google.",
        )

    if user is None or not verify_password(payload.password, user.hashed_password or ""):
        logger.warning("Failed login for: %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        logger.warning("Login blocked for deactivated account: %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is deactivated. Contact support.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(
        {"sub": user.email, "user_id": str(user.id)},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    logger.info("User logged in: %s", payload.email)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
def get_profile(current_user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    """Get current authenticated user profile."""
    return UserResponse.model_validate(current_user)


@router.patch("/profile", response_model=UserResponse, status_code=status.HTTP_200_OK)
def update_profile(
    payload: ProfileUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    """Update the authenticated user's full name and/or password."""
    if payload.full_name is None and payload.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one field to update: full_name or password",
        )
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip()
    if payload.password is not None:
        current_user.hashed_password = hash_password(payload.password)
    db.commit()
    db.refresh(current_user)
    logger.info("Profile updated for user %s", current_user.id)
    return UserResponse.model_validate(current_user)


@router.get("/dashboard", status_code=status.HTTP_200_OK)
def get_user_dashboard(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Personalised dashboard — saved routes status, notifications, city health, incidents."""
    now = datetime.now(timezone.utc)
    since_1h = now - _td(hours=1)

    # Saved routes with latest nearby traffic
    saved_routes = (
        db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(SavedRoute).where(
                SavedRoute.user_id == current_user.id,
                SavedRoute.is_active == True,
            )
        )
        .scalars()
        .all()
    )
    routes_status = []
    for route in saved_routes:
        record = (
            db.query(TrafficRecord)
            .filter(
                TrafficRecord.latitude.between(route.origin_lat - 0.02, route.origin_lat + 0.02),
                TrafficRecord.longitude.between(route.origin_lng - 0.02, route.origin_lng + 0.02),
            )
            .order_by(TrafficRecord.created_at.desc())
            .first()
        )
        routes_status.append({
            "route_id": route.id,
            "route_name": route.route_name,
            "origin": route.origin_name,
            "destination": route.destination_name,
            "congestion_level": record.congestion_level if record else "unknown",
            "average_speed_kmh": record.average_speed if record else None,
        })

    unread_notifications = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.is_read == False)
        .count()
    )
    active_incidents = db.query(Incident).filter(Incident.is_active == True).count()

    # City health score from last hour
    recent = db.query(TrafficRecord).filter(
        TrafficRecord.created_at >= since_1h,
        TrafficRecord.congestion_level.isnot(None),
    ).all()
    if recent:
        counts = Counter(r.congestion_level for r in recent)
        total = len(recent)
        health_score = round(max(0, 100 - counts.get("high", 0) / total * 70 - counts.get("medium", 0) / total * 25), 1)
    else:
        health_score = 100

    return {
        "user": {
            "id": current_user.id,
            "full_name": current_user.full_name,
            "email": current_user.email,
            "is_verified": current_user.is_verified,
        },
        "unread_notifications": unread_notifications,
        "active_incidents_citywide": active_incidents,
        "city_health_score": health_score,
        "saved_routes": routes_status,
        "generated_at": now.isoformat(),
    }


@router.post("/setup-admin", status_code=status.HTTP_200_OK)
def setup_admin(
    secret: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> dict:
    """
    Promote the currently authenticated user to admin.

    Requires the ADMIN_SETUP_SECRET from the server environment (default: flowcast-setup-2026).
    Can be called by any logged-in user who knows the secret — useful for the first setup
    or adding additional admins later.
    """
    import os
    expected = os.getenv("ADMIN_SETUP_SECRET", "flowcast-setup-2026")
    if secret != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid setup secret")

    if current_user.is_admin:
        return {"message": f"User '{current_user.email}' is already an admin."}

    current_user.is_admin = True
    db.commit()
    db.refresh(current_user)
    logger.info("User %s promoted to admin via setup endpoint", current_user.email)
    return {
        "message": f"User '{current_user.email}' is now an admin.",
        "hint": "Log in again to get a fresh token, then access /admin/* endpoints.",
    }


@router.post("/refresh", response_model=TokenResponse, status_code=status.HTTP_200_OK)
def refresh_token(current_user: Annotated[User, Depends(get_current_user)]) -> TokenResponse:
    """Refresh JWT access token before expiry."""
    access_token = create_access_token(
        {"sub": current_user.email, "user_id": str(current_user.id)},
        expires_delta=timedelta(minutes=30),
    )

    logger.info("Access token refreshed for user: %s", current_user.email)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=1800,
        user=UserResponse.model_validate(current_user),
    )


# ─── Google OAuth2 ─────────────────────────────────────────────────────────────

_GOOGLE_AUTH_URL    = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL   = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_GOOGLE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"

_SCOPES = "openid email profile"


class GoogleTokenRequest(BaseModel):
    """Payload for frontend-initiated Google Sign-In (ID token flow)."""
    id_token: str = Field(..., description="Google ID token from the frontend Sign-In SDK")


def _get_or_create_google_user(db: Session, google_info: dict) -> User:
    """Find existing user by google_id or email, or create a new Google account."""
    google_id  = google_info["sub"]
    email      = google_info["email"]
    full_name  = google_info.get("name") or email.split("@")[0]
    picture    = google_info.get("picture")

    # 1. Existing Google account
    user = db.query(User).filter(User.google_id == google_id).first()
    if user:
        user.last_login = datetime.utcnow()
        user.picture_url = picture
        db.commit()
        db.refresh(user)
        return user

    # 2. Email exists as local account → link Google to it
    user = db.query(User).filter(User.email == email).first()
    if user:
        user.google_id     = google_id
        user.auth_provider = "google"
        user.picture_url   = picture
        user.is_verified   = True
        user.last_login    = datetime.utcnow()
        db.commit()
        db.refresh(user)
        logger.info("Linked Google account to existing user %s", email)
        return user

    # 3. Brand-new Google user
    user = User(
        email          = email,
        full_name      = full_name,
        hashed_password= None,
        auth_provider  = "google",
        google_id      = google_id,
        picture_url    = picture,
        is_active      = True,
        is_verified    = True,
        last_login     = datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("New Google user created: %s", email)
    return user


def _issue_token(user: User) -> TokenResponse:
    access_token = create_access_token(
        {"sub": user.email, "user_id": str(user.id)},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


@router.get(
    "/google/login",
    summary="Sign in with Google",
    tags=["Authentication"],
)
def google_login_url() -> HTMLResponse:
    """Open this URL directly in your browser (not via Swagger Execute):

    `http://localhost:8000/api/v1/auth/google/login`

    Returns an HTML page with a Sign in with Google button that performs
    a proper browser navigation — required by Google OAuth (CORS fetch is blocked).
    """
    client_id    = os.getenv("GOOGLE_CLIENT_ID")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")

    if not client_id or client_id == "your_google_client_id_here":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env",
        )

    params = urlencode({
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         _SCOPES,
        "access_type":   "offline",
        "prompt":        "select_account",
    })
    auth_url = f"{_GOOGLE_AUTH_URL}?{params}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>FlowCast — Sign in with Google</title>
  <style>
    body {{ font-family:'Segoe UI',sans-serif; background:#f0f4f8;
            display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0; }}
    .card {{ background:#fff; border-radius:16px; padding:48px 40px; max-width:420px;
             width:100%; box-shadow:0 4px 24px rgba(0,0,0,.10); text-align:center; }}
    h2 {{ color:#1e293b; margin:0 0 8px; font-size:24px; }}
    p  {{ color:#64748b; font-size:14px; margin-bottom:32px; }}
    .google-btn {{
      display:inline-flex; align-items:center; gap:12px;
      background:#fff; border:1px solid #dadce0; border-radius:8px;
      padding:12px 24px; font-size:15px; font-weight:500; color:#3c4043;
      text-decoration:none; cursor:pointer;
      box-shadow:0 1px 3px rgba(0,0,0,.1);
      transition:box-shadow .2s;
    }}
    .google-btn:hover {{ box-shadow:0 2px 8px rgba(0,0,0,.15); }}
    .google-btn svg {{ width:20px; height:20px; flex-shrink:0; }}
  </style>
</head>
<body>
<div class="card">
  <h2>FlowCast</h2>
  <p>Sign in to continue to the FlowCast API</p>
  <a class="google-btn" href="{auth_url}">
    <svg viewBox="0 0 48 48">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.31-8.16 2.31-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
      <path fill="none" d="M0 0h48v48H0z"/>
    </svg>
    Sign in with Google
  </a>
</div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


@router.get(
    "/google/callback",
    summary="Google OAuth Callback",
    tags=["Authentication"],
)
def google_callback(
    code: str,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Handle Google's redirect after user consent.

    Google sends `?code=...` to this URL. The backend exchanges the code,
    creates/logs in the user, and returns an HTML page displaying the JWT token
    so the user can copy it into Swagger Authorize or the frontend.
    """
    client_id     = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri  = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")

    if not client_id or client_id == "your_google_client_id_here":
        raise HTTPException(status_code=503, detail="Google OAuth not configured.")

    # Exchange authorization code for tokens
    try:
        token_resp = httpx.post(_GOOGLE_TOKEN_URL, data={
            "code":          code,
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        }, timeout=10)
        token_resp.raise_for_status()
        tokens = token_resp.json()
    except Exception as exc:
        logger.error("Google token exchange failed: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to exchange Google authorization code.")

    # Fetch user info using the access token
    try:
        info_resp = httpx.get(_GOOGLE_USERINFO_URL, headers={
            "Authorization": f"Bearer {tokens['access_token']}"
        }, timeout=10)
        info_resp.raise_for_status()
        google_info = info_resp.json()
    except Exception as exc:
        logger.error("Google userinfo fetch failed: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to retrieve user info from Google.")

    if not google_info.get("email_verified", False):
        raise HTTPException(status_code=400, detail="Google account email is not verified.")

    user = _get_or_create_google_user(db, google_info)
    token_data = _issue_token(user)
    logger.info("Google callback login: %s", user.email)

    # Redirect to the Next.js frontend so the SPA can store the token
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
    redirect_target = (
        f"{frontend_url}/auth/google/callback"
        f"?token={token_data.access_token}"
        f"&name={user.full_name or ''}"
    )
    return RedirectResponse(url=redirect_target, status_code=302)


@router.post(
    "/google/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify Google ID Token (Frontend Flow)",
    tags=["Authentication"],
)
def google_token_login(
    payload: GoogleTokenRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Verify a Google ID token sent by the frontend and return a FlowCast JWT.

    Use this when the frontend handles Google Sign-In via the Google JS SDK or
    Firebase Auth and sends the resulting `id_token` to the backend.

    Steps (frontend):
    1. Implement Google Sign-In button using the Google Identity SDK.
    2. On success, receive the `credential` (ID token).
    3. POST `{ "id_token": "<credential>" }` to this endpoint.
    4. Use the returned `access_token` for all subsequent API calls.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id or client_id == "your_google_client_id_here":
        raise HTTPException(status_code=503, detail="Google OAuth not configured.")

    # Verify the ID token with Google
    try:
        resp = httpx.get(
            _GOOGLE_TOKEN_INFO_URL,
            params={"id_token": payload.id_token},
            timeout=10,
        )
        resp.raise_for_status()
        google_info = resp.json()
    except Exception as exc:
        logger.error("Google ID token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired Google ID token.")

    # Validate the token was issued for our app
    if google_info.get("aud") != client_id:
        raise HTTPException(status_code=401, detail="Google token audience mismatch.")

    if not google_info.get("email_verified") in (True, "true"):
        raise HTTPException(status_code=400, detail="Google account email is not verified.")

    user = _get_or_create_google_user(db, google_info)
    logger.info("Google ID token login: %s", user.email)
    return _issue_token(user)
