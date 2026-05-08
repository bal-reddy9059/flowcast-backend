"""User authentication schemas.

Defines request and response models for user registration, login, and JWT token handling.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    """Schema for registering a new user account."""

    email: EmailStr = Field(..., example="commuter@gmail.com", description="User email address")
    full_name: str = Field(
        ..., min_length=2, max_length=100, example="Ravi Kumar", description="Full name of the user"
    )
    password: str = Field(
        ..., min_length=8, max_length=100, example="SecurePass123", description="User password"
    )

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        """Ensure the password contains at least one uppercase letter and one number."""
        if not any(char.isupper() for char in value) or not any(char.isdigit() for char in value):
            raise ValueError("Password must have 1 uppercase and 1 number")
        return value


class UserLogin(BaseModel):
    """Schema for authenticating an existing user."""

    email: EmailStr = Field(..., example="commuter@gmail.com", description="Registered user email address")
    password: str = Field(..., example="SecurePass123", description="User password")

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class UserResponse(BaseModel):
    """Schema returned when exposing authenticated user information."""

    id: int
    email: EmailStr
    full_name: str
    is_active: bool
    is_admin: bool
    is_verified: bool
    last_login: Optional[datetime]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    """Schema returned after a successful authentication request."""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", example="bearer", description="Authentication scheme")
    expires_in: int = Field(1800, description="Seconds until the token expires")
    user: UserResponse = Field(..., description="Authenticated user data")


class TokenData(BaseModel):
    """Schema for decoded JWT payload data used internally."""

    email: Optional[str] = Field(None, description="User email extracted from token payload")
    user_id: Optional[int] = Field(None, description="User ID extracted from token payload")
