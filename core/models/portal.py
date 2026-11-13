"""Portal models — Phase Portal Bloc A.

Tables: portal_devices, portal_enroll_tokens, portal_audit_log
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, Text, UniqueConstraint, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class PortalDeviceStatus(str, enum.Enum):
    pending = "pending"
    connected = "connected"
    disconnected = "disconnected"
    revoked = "revoked"


class PortalAuditSource(str, enum.Enum):
    agent = "agent"
    app = "app"
    dashboard = "dashboard"
    unknown = "unknown"


class PortalDevice(Base):
    __tablename__ = "portal_devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    services: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[PortalDeviceStatus] = mapped_column(
        Enum(PortalDeviceStatus, name="portal_device_status"),
        nullable=False,
        default=PortalDeviceStatus.pending,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_portal_device_workspace_slug"),
    )

    audit_logs: Mapped[list["PortalAuditLog"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class PortalEnrollToken(Base):
    __tablename__ = "portal_enroll_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    device_name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    token_hash: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PortalAuditLog(Base):
    __tablename__ = "portal_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("portal_devices.id", ondelete="CASCADE"), nullable=False
    )
    # Stored as plain UUIDs (no FK) — best-effort audit, agents/tasks may be deleted
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source: Mapped[PortalAuditSource] = mapped_column(
        Enum(PortalAuditSource, name="portal_audit_source"),
        nullable=False,
        default=PortalAuditSource.unknown,
    )
    service_slug: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    method: Mapped[str] = mapped_column(VARCHAR(10), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bytes_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_portal_audit_device_ts", "device_id", "ts"),
        Index("idx_portal_audit_agent_ts", "agent_id", "ts"),
    )

    device: Mapped["PortalDevice"] = relationship(back_populates="audit_logs")
