import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Text, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id: Mapped[str] = mapped_column(VARCHAR(100), primary_key=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    # Phase 3
    tools: Mapped[list] = mapped_column(JSONB, default=list)
    skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # None = tous les skills
    subagents: Mapped[list] = mapped_column(JSONB, default=list)
    memory_sources: Mapped[list] = mapped_column(JSONB, default=list)
    interrupt_on: Mapped[dict] = mapped_column(JSONB, default=dict)
    backend_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    context_window_size: Mapped[int] = mapped_column(Integer, default=50, server_default="50")
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    # Phase 5 — Multi-provider LLM
    llm_provider: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)   # openrouter | anthropic | google | ollama
    llm_model: Mapped[str | None] = mapped_column(VARCHAR(200), nullable=True)      # ex: claude-sonnet-4-20250514
    provider_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_credentials.id", ondelete="SET NULL"), nullable=True
    )
    fallback_llm_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {"provider": "openrouter", "model": "gpt-4o-mini"}


class WorkspaceAgent(Base):
    """Junction table: links agents to workspaces. An agent can be in N workspaces."""
    __tablename__ = "workspace_agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[str] = mapped_column(VARCHAR(100), ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False)
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
