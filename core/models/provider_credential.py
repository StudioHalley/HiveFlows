import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, VARCHAR
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )  # NULL = global (site-admin)
    provider: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)  # openrouter | anthropic | google | ollama
    display_name: Mapped[str | None] = mapped_column(VARCHAR(200), nullable=True)
    credential_data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON chiffré Fernet
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
