"""User authentication schemas.

Defines request and response models for user registration, login, and JWT token handling.
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


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

    id: uuid.UUID
    email: EmailStr
    full_name: str
    # Frontend header/profile often read `.name` / `.display_name` / camelCase
    name: Optional[str] = None
    display_name: Optional[str] = None
    fullName: Optional[str] = None
    displayName: Optional[str] = None
    avatar_initial: Optional[str] = None
    avatarInitial: Optional[str] = None
    is_active: bool
    is_admin: bool
    is_verified: bool
    auth_provider: str
    picture_url: Optional[str] = None
    pictureUrl: Optional[str] = None
    last_login: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    @model_validator(mode="after")
    def _fill_name_aliases(self) -> "UserResponse":
        display = (self.full_name or "").strip() or self.email.split("@")[0]
        initial = display[0].upper() if display else "U"
        self.name = self.name or display
        self.display_name = self.display_name or display
        self.fullName = self.fullName or display
        self.displayName = self.displayName or display
        self.avatar_initial = self.avatar_initial or initial
        self.avatarInitial = self.avatarInitial or initial
        self.pictureUrl = self.pictureUrl if self.pictureUrl is not None else self.picture_url
        return self


class TokenResponse(BaseModel):
    """Schema returned after a successful authentication request."""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", example="bearer", description="Authentication scheme")
    expires_in: int = Field(1800, description="Seconds until the token expires")
    user: UserResponse = Field(..., description="Authenticated user data")


class TokenData(BaseModel):
    """Schema for decoded JWT payload data used internally."""

    email: Optional[str] = Field(None, description="User email extracted from token payload")
    user_id: Optional[uuid.UUID] = Field(None, description="User ID extracted from token payload")
