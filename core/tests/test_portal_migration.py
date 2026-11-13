"""Tests migration 020 — portal_devices, portal_enroll_tokens, portal_audit_log.

Requires DATABASE_URL pointing to a real PostgreSQL instance.
Run with: pytest core/tests/test_portal_migration.py -v

These tests verify the migration round-trip (up + down) using Alembic's
programmatic API and SQLAlchemy inspection.
"""

import os
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


PORTAL_TABLES = {"portal_devices", "portal_enroll_tokens", "portal_audit_log"}
PORTAL_ENUMS = {"portal_device_status", "portal_audit_source"}


@pytest.fixture(scope="module")
def alembic_cfg():
    """Alembic config pointing to the core alembic.ini."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(base, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(base, "alembic"))
    return cfg


@pytest.fixture(scope="module")
def engine():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        pytest.skip("DATABASE_URL not set — skipping migration tests")
    return create_engine(db_url)


@pytest.fixture(scope="module", autouse=True)
def run_migrations(alembic_cfg, engine):
    """Ensure DB is at revision 019 before the test, then run 020 up."""
    # Upgrade to just before 020 to guarantee a clean baseline
    command.upgrade(alembic_cfg, "019")
    yield
    # Teardown: downgrade back to 019 so the DB is left clean
    command.downgrade(alembic_cfg, "019")


class TestMigrationUp:
    def test_upgrade_to_020(self, alembic_cfg):
        command.upgrade(alembic_cfg, "020")

    def test_portal_tables_exist(self, engine, alembic_cfg):
        command.upgrade(alembic_cfg, "020")
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        for table in PORTAL_TABLES:
            assert table in existing, f"Table '{table}' missing after upgrade"

    def test_portal_devices_columns(self, engine, alembic_cfg):
        command.upgrade(alembic_cfg, "020")
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("portal_devices")}
        expected = {"id", "workspace_id", "slug", "name", "token_hash", "services", "status", "last_seen_at", "created_at"}
        assert expected.issubset(cols)

    def test_portal_enroll_tokens_columns(self, engine, alembic_cfg):
        command.upgrade(alembic_cfg, "020")
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("portal_enroll_tokens")}
        expected = {"id", "workspace_id", "device_name", "token_hash", "expires_at", "used_at", "created_at"}
        assert expected.issubset(cols)

    def test_portal_audit_log_columns(self, engine, alembic_cfg):
        command.upgrade(alembic_cfg, "020")
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("portal_audit_log")}
        expected = {"id", "device_id", "agent_id", "task_id", "source", "service_slug", "method", "path", "status_code", "duration_ms", "bytes_out", "bytes_in", "error", "ts"}
        assert expected.issubset(cols)

    def test_portal_audit_indexes_exist(self, engine, alembic_cfg):
        command.upgrade(alembic_cfg, "020")
        inspector = inspect(engine)
        index_names = {idx["name"] for idx in inspector.get_indexes("portal_audit_log")}
        assert "idx_portal_audit_device_ts" in index_names
        assert "idx_portal_audit_agent_ts" in index_names

    def test_portal_device_unique_constraint(self, engine, alembic_cfg):
        command.upgrade(alembic_cfg, "020")
        inspector = inspect(engine)
        uqs = inspector.get_unique_constraints("portal_devices")
        uq_names = {uq["name"] for uq in uqs}
        assert "uq_portal_device_workspace_slug" in uq_names

    def test_portal_enums_exist(self, engine, alembic_cfg):
        command.upgrade(alembic_cfg, "020")
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT typname FROM pg_type WHERE typname IN ('portal_device_status', 'portal_audit_source')"
            ))
            found = {row[0] for row in result}
        assert PORTAL_ENUMS == found


class TestMigrationDown:
    def test_downgrade_removes_tables(self, alembic_cfg, engine):
        command.upgrade(alembic_cfg, "020")
        command.downgrade(alembic_cfg, "019")
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        for table in PORTAL_TABLES:
            assert table not in existing, f"Table '{table}' still present after downgrade"

    def test_downgrade_removes_enums(self, alembic_cfg, engine):
        command.upgrade(alembic_cfg, "020")
        command.downgrade(alembic_cfg, "019")
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT typname FROM pg_type WHERE typname IN ('portal_device_status', 'portal_audit_source')"
            ))
            found = {row[0] for row in result}
        assert found == set(), f"Enums still present after downgrade: {found}"
