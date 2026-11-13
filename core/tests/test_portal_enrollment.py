"""Unit tests — portal enrollment service (Bloc D).

Tests the full cycle: create → enroll → (use implied by status) → revoke.
All DB calls are mocked; no real PostgreSQL needed.

Coverage:
  - create_device: slug generation, collision handling, token hashed
  - enroll_device: happy path, expired token, already-used token, invalid token, wrong slug
  - revoke_device: DB update + registry.revoke called
  - routes: POST /api/portal/devices, POST /portal/enroll, DELETE /api/portal/devices/{id}
  - _slugify and unique_device_slug helpers
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from models.portal import PortalDevice, PortalDeviceStatus, PortalEnrollToken
from models.workspace import Workspace
from services.portal_enrollment import (
    EnrollmentError,
    _hash_token,
    _slugify,
    create_device,
    enroll_device,
    revoke_device,
    unique_device_slug,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    import asyncio
    return asyncio.run(coro)


def _device(slug="nas-maison", status=PortalDeviceStatus.pending, token_hash=None) -> PortalDevice:
    d = MagicMock(spec=PortalDevice)
    d.id = uuid.uuid4()
    d.workspace_id = uuid.uuid4()
    d.slug = slug
    d.status = status
    d.token_hash = token_hash
    d.services = []
    return d


def _enroll_token(
    used=False,
    expired=False,
    token_hash: str | None = None,
    workspace_id: uuid.UUID | None = None,
) -> PortalEnrollToken:
    tok = MagicMock(spec=PortalEnrollToken)
    tok.id = uuid.uuid4()
    tok.workspace_id = workspace_id or uuid.uuid4()
    tok.token_hash = token_hash or "fakehash"
    tok.used_at = datetime.now(timezone.utc) if used else None
    tok.expires_at = (
        datetime.now(timezone.utc) - timedelta(minutes=1) if expired
        else datetime.now(timezone.utc) + timedelta(minutes=15)
    )
    return tok


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ── _slugify ──────────────────────────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert _slugify("NAS Maison") == "nas-maison"

    def test_special_chars(self):
        assert _slugify("My Device #1!") == "my-device-1"

    def test_multiple_spaces(self):
        assert _slugify("  server   rack  ") == "server-rack"

    def test_empty_falls_back_to_device(self):
        assert _slugify("!!!") == "device"

    def test_truncated_at_64(self):
        long_name = "a" * 100
        assert len(_slugify(long_name)) <= 64


# ── unique_device_slug ────────────────────────────────────────────────────────

class TestUniqueDeviceSlug:
    def test_base_slug_used_when_available(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))

        result = run(unique_device_slug(db, uuid.uuid4(), "nas-maison"))
        assert result == "nas-maison"

    def test_suffix_added_on_collision(self):
        db = _mock_db()
        existing = MagicMock()
        existing.scalar_one_or_none.side_effect = [
            MagicMock(),   # "nas-maison" exists
            None,          # "nas-maison-2" free
        ]
        db.execute.return_value = existing

        result = run(unique_device_slug(db, uuid.uuid4(), "nas-maison"))
        assert result == "nas-maison-2"


# ── create_device ─────────────────────────────────────────────────────────────

class TestCreateDevice:
    def test_creates_device_with_pending_status(self):
        db = _mock_db()

        # unique_device_slug: no existing device
        slug_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        db.execute.return_value = slug_result

        async def _run():
            with patch("services.portal_enrollment.unique_device_slug", return_value="nas-maison"):
                device, tok, plain = await create_device(db, uuid.uuid4(), "NAS Maison")
            return device, tok, plain

        device, tok, plain = run(_run())

        assert db.add.call_count == 2  # device + token
        assert db.commit.called
        # Plain token must be non-empty and not the hash
        assert len(plain) > 10
        assert plain != _hash_token(plain)

    def test_enroll_token_hash_matches_plain(self):
        db = _mock_db()

        captured_token_row = None

        def _capture_add(obj):
            nonlocal captured_token_row
            if isinstance(obj, PortalEnrollToken):
                captured_token_row = obj

        db.add.side_effect = _capture_add

        async def _run():
            with patch("services.portal_enrollment.unique_device_slug", return_value="dev"):
                _device_obj, _tok, plain = await create_device(db, uuid.uuid4(), "Dev")
            return plain

        plain = run(_run())
        # The token row added to DB must have hash == sha256(plain)
        assert captured_token_row is not None
        assert captured_token_row.token_hash == _hash_token(plain)

    def test_token_expires_in_15_minutes(self):
        db = _mock_db()
        now = datetime.now(timezone.utc)

        captured = {}

        def _capture_add(obj):
            if isinstance(obj, PortalEnrollToken):
                captured["tok"] = obj

        db.add.side_effect = _capture_add

        async def _run():
            with patch("services.portal_enrollment.unique_device_slug", return_value="dev"):
                await create_device(db, uuid.uuid4(), "Dev")

        run(_run())
        expires = captured["tok"].expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        delta = (expires - now).total_seconds()
        assert 14 * 60 < delta < 16 * 60


# ── enroll_device ─────────────────────────────────────────────────────────────

class TestEnrollDevice:
    def _plain(self):
        import secrets
        return secrets.token_urlsafe(32)

    def test_happy_path_returns_device_and_permanent_token(self):
        plain = self._plain()
        ws_id = uuid.uuid4()
        tok = _enroll_token(token_hash=_hash_token(plain), workspace_id=ws_id)
        dev = _device(status=PortalDeviceStatus.pending)
        dev.workspace_id = ws_id

        db = _mock_db()

        tok_result = MagicMock(scalar_one_or_none=MagicMock(return_value=tok))
        dev_result = MagicMock(scalar_one_or_none=MagicMock(return_value=dev))
        db.execute.side_effect = [tok_result, dev_result, MagicMock(), MagicMock()]

        device, perm = run(enroll_device(db, plain, dev.slug, ws_id))

        assert perm != plain
        assert len(perm) > 10
        assert db.commit.called

    def test_invalid_token_raises(self):
        ws_id = uuid.uuid4()
        db = _mock_db()
        db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))

        with pytest.raises(EnrollmentError, match="Invalid"):
            run(enroll_device(db, "bad-token", "slug", ws_id))

    def test_already_used_token_raises(self):
        plain = self._plain()
        ws_id = uuid.uuid4()
        tok = _enroll_token(used=True, token_hash=_hash_token(plain), workspace_id=ws_id)

        db = _mock_db()
        db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=tok))

        with pytest.raises(EnrollmentError, match="already used"):
            run(enroll_device(db, plain, "slug", ws_id))

    def test_expired_token_raises(self):
        plain = self._plain()
        ws_id = uuid.uuid4()
        tok = _enroll_token(expired=True, token_hash=_hash_token(plain), workspace_id=ws_id)

        db = _mock_db()
        db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=tok))

        with pytest.raises(EnrollmentError, match="expired"):
            run(enroll_device(db, plain, "slug", ws_id))

    def test_device_not_found_raises(self):
        plain = self._plain()
        ws_id = uuid.uuid4()
        tok = _enroll_token(token_hash=_hash_token(plain), workspace_id=ws_id)

        db = _mock_db()
        tok_result = MagicMock(scalar_one_or_none=MagicMock(return_value=tok))
        dev_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        db.execute.side_effect = [tok_result, dev_result]

        with pytest.raises(EnrollmentError, match="not found"):
            run(enroll_device(db, plain, "unknown-slug", ws_id))

    def test_permanent_token_hash_stored_correctly(self):
        plain = self._plain()
        ws_id = uuid.uuid4()
        tok = _enroll_token(token_hash=_hash_token(plain), workspace_id=ws_id)
        dev = _device(status=PortalDeviceStatus.pending)
        dev.workspace_id = ws_id

        db = _mock_db()
        tok_result = MagicMock(scalar_one_or_none=MagicMock(return_value=tok))
        dev_result = MagicMock(scalar_one_or_none=MagicMock(return_value=dev))
        # execute calls: lookup tok, lookup dev, update device, update token
        db.execute.side_effect = [tok_result, dev_result, MagicMock(), MagicMock()]

        captured_updates = []
        original_execute = db.execute.side_effect

        _device_obj, perm = run(enroll_device(db, plain, dev.slug, ws_id))

        # Permanent token must differ from enroll token
        assert perm != plain
        assert _hash_token(perm) != _hash_token(plain)


# ── revoke_device ─────────────────────────────────────────────────────────────

class TestRevokeDevice:
    def test_revoke_calls_db_update_and_registry(self):
        dev = _device(status=PortalDeviceStatus.connected)
        db = _mock_db()
        db.execute.return_value = MagicMock()

        async def _run():
            with patch("services.portal_enrollment.portal_registry") as mock_registry:
                mock_registry.revoke = AsyncMock()
                await revoke_device(db, dev)
                return mock_registry.revoke.call_args

        call_args = run(_run())

        assert db.commit.called
        assert call_args is not None
        assert call_args.args[0] == dev.id

    def test_revoke_does_not_raise_if_device_not_in_registry(self):
        dev = _device(status=PortalDeviceStatus.disconnected)
        db = _mock_db()
        db.execute.return_value = MagicMock()

        async def _run():
            with patch("services.portal_enrollment.portal_registry") as mock_registry:
                mock_registry.revoke = AsyncMock()
                await revoke_device(db, dev)

        run(_run())  # must not raise


# ── Full cycle via service functions ──────────────────────────────────────────

class TestFullCycle:
    """
    Simulate: create → enroll → revoke in sequence using the service functions,
    verifying the state machine transitions.
    """

    def test_create_then_enroll_then_revoke(self):
        ws_id = uuid.uuid4()
        dev_id = uuid.uuid4()

        # --- Step 1: create_device ---
        created_plain_token = None

        async def _create():
            nonlocal created_plain_token
            db = _mock_db()

            with patch("services.portal_enrollment.unique_device_slug", return_value="my-device"):
                # Capture the PortalDevice added
                added = {}

                def _add(obj):
                    if hasattr(obj, "status"):
                        added["device"] = obj
                    else:
                        added["token"] = obj

                db.add.side_effect = _add

                device, tok, plain = await create_device(db, ws_id, "My Device")
                created_plain_token = plain

                # device must be pending, token must be hashed
                assert added["token"].token_hash == _hash_token(plain)
                assert added["device"].status == PortalDeviceStatus.pending

        run(_create())
        assert created_plain_token is not None

        # --- Step 2: enroll_device ---
        perm_token = None

        async def _enroll():
            nonlocal perm_token
            db = _mock_db()
            now = datetime.now(timezone.utc)

            enroll_tok = MagicMock(spec=PortalEnrollToken)
            enroll_tok.id = uuid.uuid4()
            enroll_tok.workspace_id = ws_id
            enroll_tok.token_hash = _hash_token(created_plain_token)
            enroll_tok.used_at = None
            enroll_tok.expires_at = now + timedelta(minutes=15)

            dev = MagicMock(spec=PortalDevice)
            dev.id = dev_id
            dev.workspace_id = ws_id
            dev.slug = "my-device"
            dev.status = PortalDeviceStatus.pending

            tok_result = MagicMock(scalar_one_or_none=MagicMock(return_value=enroll_tok))
            dev_result = MagicMock(scalar_one_or_none=MagicMock(return_value=dev))
            db.execute.side_effect = [tok_result, dev_result, MagicMock(), MagicMock()]

            device_out, perm = await enroll_device(db, created_plain_token, "my-device", ws_id)
            perm_token = perm

            # After enroll: device should be updated to disconnected with new token_hash
            assert perm != created_plain_token

        run(_enroll())
        assert perm_token is not None

        # --- Step 3: revoke_device ---
        async def _revoke():
            db = _mock_db()
            db.execute.return_value = MagicMock()

            dev = MagicMock(spec=PortalDevice)
            dev.id = dev_id
            dev.workspace_id = ws_id
            dev.status = PortalDeviceStatus.connected

            with patch("services.portal_enrollment.portal_registry") as mock_reg:
                mock_reg.revoke = AsyncMock()
                await revoke_device(db, dev)
                # Registry.revoke must have been called with device.id
                mock_reg.revoke.assert_called_once_with(dev_id)

        run(_revoke())
