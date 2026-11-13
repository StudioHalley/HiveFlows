import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Text, VARCHAR
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(VARCHAR(255), unique=True, nullable=False)
    slug: Mapped[str | None] = mapped_column(VARCHAR(100), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(VARCHAR(200), nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    oauth_provider: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)
    oauth_id: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_site_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
