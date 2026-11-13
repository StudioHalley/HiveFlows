"""Agent configs Phase 3: nouvelles colonnes + seed assistant-principal

Revision ID: 002
Revises: 001
Create Date: 2026-03-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_configs", sa.Column("tools", JSONB, server_default=sa.text("'[]'")))
    op.add_column("agent_configs", sa.Column("skills", JSONB, nullable=True))  # NULL = tous les skills
    op.add_column("agent_configs", sa.Column("subagents", JSONB, server_default=sa.text("'[]'")))
    op.add_column("agent_configs", sa.Column("memory_sources", JSONB, server_default=sa.text("'[]'")))
    op.add_column("agent_configs", sa.Column("interrupt_on", JSONB, server_default=sa.text("'{}'")))
    op.add_column("agent_configs", sa.Column("backend_config", JSONB, server_default=sa.text("'{}'")))
    op.add_column("agent_configs", sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()")))

    # Seed : agent par défaut (ON CONFLICT = idempotent si déjà existant depuis Phase 1)
    op.execute("""
        INSERT INTO agent_configs
            (id, name, description, llm_config, system_prompt, tools, skills, subagents,
             memory_sources, interrupt_on, backend_config, is_active)
        VALUES (
            'assistant-principal',
            'Assistant Principal',
            'Agent généraliste HiveFlows',
            '{"provider": "openrouter", "model": "openai/gpt-4o-mini"}',
            NULL,
            '["run_script", "run_skill_script"]',
            NULL,
            '[]',
            '[]',
            '{}',
            '{}',
            true
        )
        ON CONFLICT (id) DO UPDATE SET
            tools           = EXCLUDED.tools,
            subagents       = EXCLUDED.subagents,
            memory_sources  = EXCLUDED.memory_sources,
            interrupt_on    = EXCLUDED.interrupt_on,
            backend_config  = EXCLUDED.backend_config
    """)


def downgrade() -> None:
    op.drop_column("agent_configs", "updated_at")
    op.drop_column("agent_configs", "backend_config")
    op.drop_column("agent_configs", "interrupt_on")
    op.drop_column("agent_configs", "memory_sources")
    op.drop_column("agent_configs", "subagents")
    op.drop_column("agent_configs", "skills")
    op.drop_column("agent_configs", "tools")
