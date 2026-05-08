"""Authentication routes for FlowCast."""

import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.user import TokenResponse, UserCreate, UserLogin, UserResponse
from app.services.auth_service import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Register new FlowCast commuter account."""
    query = select(User).where(User.email == payload.email)
    result = await db.execute(query)
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
        created_at=datetime.utcnow(),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(
        {"sub": user.email, "user_id": user.id},
        expires_delta=timedelta(minutes=30),
    )

    logger.info("New user registered: %s", user.email)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=1800,
        user=UserResponse.from_orm(user),
    )


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login_user(
    payload: UserLogin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Login to FlowCast account."""
    query = select(User).where(User.email == payload.email)
    result = await db.execute(query)
    user = result.scalars().first()

    if user is None or not verify_password(payload.password, user.hashed_password):
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

    user.last_login = datetime.utcnow()
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(
        {"sub": user.email, "user_id": user.id},
        expires_delta=timedelta(minutes=30),
    )

    logger.info("User logged in: %s", payload.email)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=1800,
        user=UserResponse.from_orm(user),
    )


@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def get_profile(current_user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    """Get current authenticated user profile."""
    return UserResponse.from_orm(current_user)


@router.post("/refresh", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def refresh_token(current_user: Annotated[User, Depends(get_current_user)]) -> TokenResponse:
    """Refresh JWT access token before expiry."""
    access_token = create_access_token(
        {"sub": current_user.email, "user_id": current_user.id},
        expires_delta=timedelta(minutes=30),
    )

    logger.info("Access token refreshed for user: %s", current_user.email)
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=1800,
        user=UserResponse.from_orm(current_user),
    )
