"""Pydantic schemas for auth endpoints (Phase 4)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str
    password: str


class WorkspaceShort(BaseModel):
    id: UUID
    slug: str
    name: str

    model_config = {"from_attributes": True}


class RegisterResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    workspace: WorkspaceShort


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    expires_in: int


class UserRead(BaseModel):
    id: UUID
    email: str
    display_name: str
    is_active: bool
    oauth_provider: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
