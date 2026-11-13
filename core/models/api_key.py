"""API Key model — workspace-scoped keys (Phase 4 Bloc E)."""

import hashlib
import secrets
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

_KEY_PREFIX_LEN = 8  # "hf_xxxxx" shown for identification


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key. Returns (full_key, prefix)."""
    raw = secrets.token_urlsafe(36)
    full_key = f"hf_{raw}"
    prefix = full_key[:_KEY_PREFIX_LEN]
    return full_key, prefix


def hash_api_key(key: str) -> str:
    """SHA256 hash of the API key for fast DB lookup.

    API keys are high-entropy random tokens (48+ chars) —
    SHA256 is sufficient and enables single-query authentication.
    """
    return hashlib.sha256(key.encode()).hexdigest()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(VARCHAR(10), nullable=False)
    name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    permissions: Mapped[list] = mapped_column(JSONB, default=lambda: ["chat", "tasks", "agents"])
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
