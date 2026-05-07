"""
User authentication schemas.

Defines request and response models for user registration, login, and JWT token handling.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Schema for creating a new user account."""

    email: EmailStr = Field(..., description="User email address")
    full_name: str = Field(..., min_length=2, max_length=255, description="Full name of the user")
    password: str = Field(..., min_length=8, description="User password")

    model_config = {
        "str_strip_whitespace": True,
        "extra": "forbid",
    }


class UserLogin(BaseModel):
    """Schema for authenticating an existing user."""

    email: EmailStr = Field(..., description="Registered user email address")
    password: str = Field(..., description="User password")

    model_config = {
        "str_strip_whitespace": True,
        "extra": "forbid",
    }


class UserResponse(BaseModel):
    """Schema returned when exposing authenticated user information."""

    id: int
    email: EmailStr
    full_name: str
    is_active: bool
    created_at: datetime

    model_config = {
        "from_attributes": True,
        "extra": "forbid",
    }


class TokenResponse(BaseModel):
    """Schema returned after a successful authentication request."""

    access_token: str
    token_type: str = Field("bearer", const=True)
    expires_in: int

    model_config = {
        "extra": "forbid",
    }


class TokenData(BaseModel):
    """Schema for data stored inside JWT access tokens."""

    email: Optional[EmailStr] = None
    user_id: Optional[int] = None

    model_config = {
        "extra": "forbid",
    }
