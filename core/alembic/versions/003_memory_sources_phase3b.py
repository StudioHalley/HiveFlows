"""Phase 3B : configurer memory_sources pour assistant-principal

Revision ID: 003
Revises: 002
Create Date: 2026-03-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Activer la mémoire cross-conversation pour l'agent par défaut.
    # /memories/AGENTS.md sera lu à chaque conversation et l'agent peut y écrire
    # via le tool edit_file (backend CompositeBackend → StoreBackend → PostgresStore).
    op.execute("""
        UPDATE agent_configs
        SET memory_sources = '["/memories/AGENTS.md"]'::jsonb
        WHERE id = 'assistant-principal'
          AND memory_sources = '[]'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE agent_configs
        SET memory_sources = '[]'::jsonb
        WHERE id = 'assistant-principal'
          AND memory_sources = '["/memories/AGENTS.md"]'::jsonb
    """)
