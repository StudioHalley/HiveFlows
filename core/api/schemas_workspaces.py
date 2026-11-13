"""Pydantic schemas for workspace & member endpoints (Phase 4 — Bloc B)."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class WorkspaceCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("Le slug ne peut pas être vide.")
        if not _SLUG_RE.match(v):
            raise ValueError(
                "Le slug ne doit contenir que des lettres minuscules, "
                "chiffres et tirets (ex: mon-workspace)."
            )
        return v


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class WorkspaceRead(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    owner_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class WorkspaceWithRole(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    owner_id: UUID
    is_active: bool
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MemberRead(BaseModel):
    id: UUID
    user_id: UUID
    email: str
    display_name: str
    role: str
    created_at: datetime


class MemberAdd(BaseModel):
    email: str
    role: str = "member"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"admin", "member", "viewer"}
        if v not in allowed:
            raise ValueError(f"Role must be one of: {', '.join(sorted(allowed))}")
        return v


class MemberUpdateRole(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"admin", "member", "viewer"}
        if v not in allowed:
            raise ValueError(f"Role must be one of: {', '.join(sorted(allowed))}")
        return v
