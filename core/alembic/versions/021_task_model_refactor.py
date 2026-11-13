"""Task model refactor — Phase 7 Bloc A

Revision ID: 021
Revises: 020
Create Date: 2026-04-14

Changes:
- TRUNCATE tasks (ephemeral data, no migration needed)
- DROP celery_id, payload
- ADD name, progress, progress_detail, triggered_by, backend_ref, params,
      started_at, completed_at
- ADD indexes idx_tasks_workspace_status, idx_tasks_workspace_created
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Truncate — tasks are ephemeral, no data to preserve
    op.execute("TRUNCATE TABLE tasks RESTART IDENTITY CASCADE")

    # Drop obsolete columns
    op.drop_column("tasks", "celery_id")
    op.drop_column("tasks", "payload")

    # Add new columns
    op.add_column("tasks", sa.Column("name", sa.VARCHAR(255), nullable=True))
    op.add_column("tasks", sa.Column("progress", sa.Integer, nullable=False, server_default="0"))
    op.add_column("tasks", sa.Column("progress_detail", sa.Text, nullable=True))
    op.add_column("tasks", sa.Column("triggered_by", sa.VARCHAR(100), nullable=True))
    op.add_column("tasks", sa.Column("backend_ref", sa.VARCHAR(255), nullable=True))
    op.add_column("tasks", sa.Column("params", JSONB, nullable=True))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime, nullable=True))
    op.add_column("tasks", sa.Column("completed_at", sa.DateTime, nullable=True))

    # Indexes (A5)
    op.create_index("idx_tasks_workspace_status", "tasks", ["workspace_id", "status"])
    op.create_index("idx_tasks_workspace_created", "tasks", ["workspace_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_tasks_workspace_created", table_name="tasks")
    op.drop_index("idx_tasks_workspace_status", table_name="tasks")

    op.drop_column("tasks", "completed_at")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "params")
    op.drop_column("tasks", "backend_ref")
    op.drop_column("tasks", "triggered_by")
    op.drop_column("tasks", "progress_detail")
    op.drop_column("tasks", "progress")
    op.drop_column("tasks", "name")

    op.add_column("tasks", sa.Column("payload", JSONB, nullable=False, server_default="{}"))
    op.add_column("tasks", sa.Column("celery_id", sa.VARCHAR(255), nullable=True))
