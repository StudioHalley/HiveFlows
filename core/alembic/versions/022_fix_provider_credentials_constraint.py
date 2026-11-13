"""Fix provider_credentials unique constraint — partial index WHERE is_default=true

Revision ID: 022
Revises: 021
Create Date: 2026-06-25

La contrainte précédente (workspace_id, provider, is_default) interdisait toute
2ème credential pour le même provider, même non-default. On la remplace par un
index partiel qui n'applique l'unicité que sur is_default=true.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_provider_credentials_workspace_provider_default", "provider_credentials")
    op.create_index(
        "uq_provider_credentials_workspace_provider_default",
        "provider_credentials",
        ["workspace_id", "provider"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_provider_credentials_workspace_provider_default", "provider_credentials")
    op.create_unique_constraint(
        "uq_provider_credentials_workspace_provider_default",
        "provider_credentials",
        ["workspace_id", "provider", "is_default"],
    )
