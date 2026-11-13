"""Phase 3C : ajouter un subagent 'researcher' a assistant-principal

Revision ID: 004
Revises: 003
Create Date: 2026-03-09
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RESEARCHER_SUBAGENT = """[
  {
    "name": "researcher",
    "description": "Specialiste de la recherche et synthese d information. Utilise ce subagent quand tu dois trouver, analyser ou synthetiser des informations precises sur un sujet.",
    "system_prompt": "Tu es un expert en recherche et synthese d informations. Ton role est de chercher, analyser et synthetiser des informations precises sur n importe quel sujet. Sois methodique, structure tes resultats clairement, et indique quand tu manques d informations."
  }
]"""


def upgrade() -> None:
    # Activer le subagent researcher sur l'agent principal.
    # Ne remplace que si subagents est vide (idempotent sur install fraiche).
    op.execute(f"""
        UPDATE agent_configs
        SET subagents = '{_RESEARCHER_SUBAGENT}'::jsonb
        WHERE id = 'assistant-principal'
          AND subagents = '[]'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE agent_configs
        SET subagents = '[]'::jsonb
        WHERE id = 'assistant-principal'
    """)
