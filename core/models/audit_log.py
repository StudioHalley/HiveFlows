"""AuditLog model — Phase 4 Bloc G."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Text, VARCHAR
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(VARCHAR(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_audit_workspace", "workspace_id", created_at.desc()),
        Index("idx_audit_user", "user_id", created_at.desc()),
        Index("idx_audit_action", "action"),
    )
