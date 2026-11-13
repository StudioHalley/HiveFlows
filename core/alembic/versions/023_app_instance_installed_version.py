"""Ajoute app_instances.installed_version (détection MAJ dispo — Couche 2)

Revision ID: 023
Revises: 022
Create Date: 2026-06-26

Stocke la version du catalogue (manifest) avec laquelle l'instance a été installée
ou mise à jour. Une MAJ est disponible quand installed_version != app_catalog.version.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_instances",
        sa.Column("installed_version", sa.VARCHAR(20), nullable=True),
    )
    # Backfill : aligne les instances existantes sur la version actuelle du catalogue
    # (on considère qu'elles tournent la version courante au moment de la migration).
    op.execute(
        """
        UPDATE app_instances ai
        SET installed_version = ac.version
        FROM app_catalog ac
        WHERE ai.app_id = ac.id
        """
    )


def downgrade() -> None:
    op.drop_column("app_instances", "installed_version")
