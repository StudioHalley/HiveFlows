import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class AppCatalog(Base):
    """Catalogue des apps disponibles — une entrée par manifest YAML."""
    __tablename__ = "app_catalog"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    slug: Mapped[str] = mapped_column(VARCHAR(100), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="1.0.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str] = mapped_column(VARCHAR(500), nullable=False)
    app_type: Mapped[str] = mapped_column(VARCHAR(20), nullable=False)       # persistent | backend
    ingress_mode: Mapped[str] = mapped_column(VARCHAR(20), default="iframe") # iframe | link | none
    default_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    env_schema: Mapped[list] = mapped_column(JSONB, default=list)
    default_mounts: Mapped[list] = mapped_column(JSONB, default=list)
    resources: Mapped[dict] = mapped_column(JSONB, default=dict)
    icon: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    tools_schema: Mapped[list] = mapped_column(JSONB, default=list)
    readme_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    instances: Mapped[list["AppInstance"]] = relationship(back_populates="app", cascade="all, delete-orphan")


class AppInstance(Base):
    """Instance d'une app installée dans un workspace."""
    __tablename__ = "app_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_catalog.id"), nullable=False)
    container_name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    # pulling | starting | running | stopped | crashed | error
    status: Mapped[str] = mapped_column(VARCHAR(20), default="stopped", nullable=False)
    # Version du catalogue (manifest) installée — sert à détecter les MAJ dispo.
    installed_version: Mapped[str | None] = mapped_column(VARCHAR(20), nullable=True)
    env_values: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON chiffré Fernet
    custom_mounts: Mapped[list] = mapped_column(JSONB, default=list)
    container_ip: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)
    installed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    app: Mapped["AppCatalog"] = relationship(back_populates="instances")
