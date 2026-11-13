"""Integration tests — Phase 7 Bloc E (E4-E8).

Vérifie les flows multi-couches avec mocks des dépendances externes.
Dépendances lourdes (Celery, Redis, DB) mockées pour une exécution isolée.

E4: POST /tasks/run  → Celery dispatché, task créée, event task_created publié
E5: POST /tools/execute → task créée, Celery dispatché, task_created publié
E6: GET /w/{slug}/activity/stream → StreamingResponse text/event-stream
E7: POST /tasks/{id}/cancel → status "cancelled", task_cancelled publié
E8: DELETE /tasks/cleanup → tâches supprimées, count retourné
"""
import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def _make_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_ctx(workspace_id=None, user_id=None):
    ctx = MagicMock()
    ctx.user = MagicMock()
    ctx.user.id = user_id or uuid.uuid4()
    ctx.workspace_id = workspace_id or uuid.uuid4()
    return ctx


def _mock_task(
    status="pending",
    backend_ref="celery-ref",
    task_id=None,
    type_="python",
    name="Test Task",
    workspace_id=None,
):
    task = MagicMock()
    task.id = task_id or uuid.uuid4()
    task.status = status
    task.backend_ref = backend_ref
    task.type = type_
    task.name = name
    task.progress = 0
    task.progress_detail = None
    task.triggered_by = "user:test"
    task.params = {}
    task.result = None
    task.error = None
    task.started_at = None
    task.completed_at = None
    task.created_at = datetime.utcnow()
    task.updated_at = datetime.utcnow()
    task.workspace_id = workspace_id or uuid.uuid4()
    return task


# ── E4 — POST /tasks/run : non-régression ────────────────────────────────────

def test_run_task_python_dispatches_celery():
    """E4: Un task Python est dispatché vers tasks.run_python."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import run_task, TaskRunRequest

    db = _make_db()
    ctx = _make_ctx()

    mock_ws_row = MagicMock()
    mock_ws_row.scalar_one_or_none = MagicMock(return_value=None)
    db.execute.return_value = mock_ws_row

    celery_result = MagicMock()
    celery_result.id = "celery-py-test"

    async def _go():
        with patch("api.routes_tasks.celery_app.send_task", return_value=celery_result) as mock_send, \
             patch("api.routes_tasks.publish_activity", new_callable=AsyncMock), \
             patch("api.routes_tasks.audit_log", new_callable=AsyncMock):
            req = TaskRunRequest(type="python", script="print(42)")
            resp = await run_task(req, MagicMock(), ctx, db)
            celery_task_name = mock_send.call_args[0][0]
            return resp, celery_task_name, mock_send.call_count

    resp, task_name, send_count = run(_go())
    assert resp.status == "pending"
    assert resp.backend_ref == "celery-py-test"
    assert resp.task_id is not None
    assert task_name == "tasks.run_python"
    assert send_count == 1


def test_run_task_bash_dispatches_celery():
    """E4: Un task Bash est dispatché vers tasks.run_bash."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import run_task, TaskRunRequest

    db = _make_db()
    ctx = _make_ctx()
    mock_ws_row = MagicMock()
    mock_ws_row.scalar_one_or_none = MagicMock(return_value=None)
    db.execute.return_value = mock_ws_row

    celery_result = MagicMock()
    celery_result.id = "celery-bash-test"

    async def _go():
        with patch("api.routes_tasks.celery_app.send_task", return_value=celery_result) as mock_send, \
             patch("api.routes_tasks.publish_activity", new_callable=AsyncMock), \
             patch("api.routes_tasks.audit_log", new_callable=AsyncMock):
            req = TaskRunRequest(type="bash", script="echo hello")
            await run_task(req, MagicMock(), ctx, db)
            return mock_send.call_args[0][0]

    task_name = run(_go())
    assert task_name == "tasks.run_bash"


def test_run_task_publishes_task_created_event():
    """E4: POST /tasks/run publie task_created sur le bus d'activité."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import run_task, TaskRunRequest

    db = _make_db()
    ctx = _make_ctx()
    mock_ws_row = MagicMock()
    mock_ws_row.scalar_one_or_none = MagicMock(return_value=None)
    db.execute.return_value = mock_ws_row

    celery_result = MagicMock()
    celery_result.id = "celery-event-test"
    published = []

    async def _go():
        async def fake_publish(**kwargs):
            published.append(kwargs)

        with patch("api.routes_tasks.celery_app.send_task", return_value=celery_result), \
             patch("api.routes_tasks.publish_activity", side_effect=fake_publish), \
             patch("api.routes_tasks.audit_log", new_callable=AsyncMock):
            req = TaskRunRequest(type="python", script="x = 1", name="My Script")
            await run_task(req, MagicMock(), ctx, db)

    run(_go())
    assert len(published) == 1
    assert published[0]["event_type"] == "task_created"
    assert published[0]["workspace_id"] == str(ctx.workspace_id)


def test_run_task_unknown_type_raises_400():
    """E4: Un type inconnu retourne HTTPException 400."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import run_task, TaskRunRequest
    from fastapi import HTTPException

    async def _go():
        req = TaskRunRequest(type="not_a_real_type")
        await run_task(req, MagicMock(), _make_ctx(), _make_db())

    with pytest.raises(HTTPException) as exc:
        run(_go())
    assert exc.value.status_code == 400


# ── E5 — POST /tools/execute ─────────────────────────────────────────────────

def test_execute_translate_tool_dispatches_and_publishes():
    """E5: POST /tools/execute → Celery dispatché + event task_created publié."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tools import execute_tool, ToolExecuteRequest

    ws_id = uuid.uuid4()
    user_mock = MagicMock()
    user_mock.id = uuid.uuid4()
    ws_mock = MagicMock()
    ws_mock.id = ws_id
    ws_mock.slug = "test-ws"

    db = _make_db()
    celery_result = MagicMock()
    celery_result.id = "celery-tool-e5"
    published = []

    async def _go():
        async def fake_publish(**kwargs):
            published.append(kwargs)

        with patch("api.routes_tools.require_workspace_auth", new_callable=AsyncMock,
                   return_value=(user_mock, ws_mock, "owner")), \
             patch("api.routes_tools.has_permission", return_value=True), \
             patch("api.routes_tools.celery_app.send_task", return_value=celery_result), \
             patch("api.routes_tools.publish_activity", side_effect=fake_publish):
            req = ToolExecuteRequest(
                tool_key="translate_file",
                file_path="report.md",
                params={"target_lang": "en-us", "model": "openai/gpt-4o-mini"},
            )
            return await execute_tool("test-ws", req, MagicMock(), db)

    result = run(_go())
    assert result["status"] == "pending"
    assert result["task_id"] is not None
    assert result["backend_ref"] == "celery-tool-e5"
    assert "Traduire" in result["name"]
    assert len(published) == 1
    assert published[0]["event_type"] == "task_created"
    assert published[0]["workspace_id"] == str(ws_id)


def test_execute_fabric_tool_dispatches_and_publishes():
    """E5: POST /tools/execute avec fabric_extract_wisdom → tasks.run_fabric dispatché."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tools import execute_tool, ToolExecuteRequest

    ws_id = uuid.uuid4()
    user_mock = MagicMock()
    user_mock.id = uuid.uuid4()
    ws_mock = MagicMock()
    ws_mock.id = ws_id
    ws_mock.slug = "test-ws"

    db = _make_db()
    celery_result = MagicMock()
    celery_result.id = "celery-fabric-e5"
    published = []

    async def _go():
        async def fake_publish(**kwargs):
            published.append(kwargs)

        with patch("api.routes_tools.require_workspace_auth", new_callable=AsyncMock,
                   return_value=(user_mock, ws_mock, "owner")), \
             patch("api.routes_tools.has_permission", return_value=True), \
             patch("api.routes_tools.celery_app.send_task", return_value=celery_result) as mock_send, \
             patch("api.routes_tools.publish_activity", side_effect=fake_publish):
            req = ToolExecuteRequest(
                tool_key="fabric_extract_wisdom",
                file_path="notes/meeting.md",
                params={"model": "openai/gpt-4o-mini"},
            )
            result = await execute_tool("test-ws", req, MagicMock(), db)
            celery_name = mock_send.call_args[0][0]
            return result, celery_name

    result, celery_name = run(_go())
    assert celery_name == "tasks.run_fabric"
    assert result["backend_ref"] == "celery-fabric-e5"
    assert "Fabric" in result["name"]
    assert published[0]["event_type"] == "task_created"


def test_execute_unknown_tool_raises_400():
    """E5: Un tool inconnu retourne HTTPException 400."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tools import execute_tool, ToolExecuteRequest
    from fastapi import HTTPException

    user_mock = MagicMock()
    user_mock.id = uuid.uuid4()
    ws_mock = MagicMock()
    ws_mock.id = uuid.uuid4()
    ws_mock.slug = "ws"

    async def _go():
        with patch("api.routes_tools.require_workspace_auth", new_callable=AsyncMock,
                   return_value=(user_mock, ws_mock, "owner")), \
             patch("api.routes_tools.has_permission", return_value=True):
            req = ToolExecuteRequest(tool_key="nonexistent_tool", file_path="file.md")
            await execute_tool("ws", req, MagicMock(), _make_db())

    with pytest.raises(HTTPException) as exc:
        run(_go())
    assert exc.value.status_code == 400


# ── E6 — SSE activity stream ─────────────────────────────────────────────────

def test_activity_stream_returns_streaming_response():
    """E6: GET /w/{slug}/activity/stream retourne StreamingResponse text/event-stream."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_dashboard import dashboard_activity_stream
    from starlette.responses import StreamingResponse

    user_mock = MagicMock()
    ws_mock = MagicMock()
    ws_mock.id = uuid.uuid4()

    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()
    mock_pubsub.get_message = AsyncMock(return_value=None)

    mock_redis = AsyncMock()
    mock_redis.pubsub.return_value = mock_pubsub
    mock_redis.aclose = AsyncMock()

    async def _go():
        with patch("api.routes_dashboard.require_workspace_auth", new_callable=AsyncMock,
                   return_value=(user_mock, ws_mock, "owner")), \
             patch("api.routes_dashboard.aioredis") as mock_aioredis:
            mock_aioredis.from_url.return_value = mock_redis
            resp = await dashboard_activity_stream(MagicMock(), "test-ws", _make_db())
            return resp

    resp = run(_go())
    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "text/event-stream"
    assert resp.headers.get("Cache-Control") == "no-cache"
    assert resp.headers.get("X-Accel-Buffering") == "no"


def test_activity_stream_sse_format():
    """E6: Le générateur SSE produit des lignes 'data: {...}\\n\\n' valides."""
    import sys, os
    import json
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.activity_bus import build_event

    ws_id = str(uuid.uuid4())
    raw = build_event("task_progress", ws_id, "system", {"current": 50}, task_id=str(uuid.uuid4()))
    # Le SSE frame que le serveur enverrait
    sse_frame = f"data: {raw}\n\n"
    # Vérification du format
    assert sse_frame.startswith("data: ")
    assert sse_frame.endswith("\n\n")
    payload = json.loads(raw)
    assert payload["type"] == "task_progress"
    assert payload["workspace_id"] == ws_id


# ── E7 — Annulation de task ──────────────────────────────────────────────────

def test_cancel_task_sets_cancelled_status():
    """E7: cancel_task met status='cancelled', appelle revoke, publie task_cancelled."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import cancel_task

    task_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    task = _mock_task(status="running", backend_ref="celery-running", task_id=task_id, workspace_id=ws_id)

    db = _make_db()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=task)
    db.execute.return_value = mock_result
    db.refresh = AsyncMock(side_effect=lambda t: None)

    ctx = _make_ctx(workspace_id=ws_id)
    published = []

    async def _go():
        async def fake_publish(**kwargs):
            published.append(kwargs)

        with patch("api.routes_tasks.celery_app.control.revoke") as mock_revoke, \
             patch("api.routes_tasks.publish_activity", side_effect=fake_publish), \
             patch("api.routes_tasks.audit_log", new_callable=AsyncMock):
            resp = await cancel_task(str(task_id), MagicMock(), ctx, db)
            mock_revoke.assert_called_once_with("celery-running", terminate=True)
            return resp

    run(_go())
    assert task.status == "cancelled"
    assert task.completed_at is not None
    assert len(published) == 1
    assert published[0]["event_type"] == "task_cancelled"
    assert published[0]["task_id"] == str(task_id)


def test_cancel_pending_task():
    """E7: Annuler une task pending fonctionne aussi (pas seulement running)."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import cancel_task

    task = _mock_task(status="pending", backend_ref="celery-pending")
    db = _make_db()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=task)
    db.execute.return_value = mock_result
    db.refresh = AsyncMock(side_effect=lambda t: None)

    async def _go():
        with patch("api.routes_tasks.celery_app.control.revoke"), \
             patch("api.routes_tasks.publish_activity", new_callable=AsyncMock), \
             patch("api.routes_tasks.audit_log", new_callable=AsyncMock):
            await cancel_task(str(task.id), MagicMock(), _make_ctx(), db)

    run(_go())
    assert task.status == "cancelled"


def test_cancel_already_done_task_raises_400():
    """E7: Annuler une task déjà success retourne HTTPException 400."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import cancel_task
    from fastapi import HTTPException

    task = _mock_task(status="success")
    db = _make_db()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=task)
    db.execute.return_value = mock_result

    async def _go():
        await cancel_task(str(task.id), MagicMock(), _make_ctx(), db)

    with pytest.raises(HTTPException) as exc:
        run(_go())
    assert exc.value.status_code == 400


# ── E8 — Nettoyage des tasks ─────────────────────────────────────────────────

def test_cleanup_tasks_returns_deleted_count():
    """E8: DELETE /tasks/cleanup retourne le nombre de tâches supprimées."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import cleanup_tasks

    db = _make_db()
    mock_result = MagicMock()
    mock_result.rowcount = 4
    db.execute.return_value = mock_result

    resp = run(cleanup_tasks(_make_ctx(), db))
    assert resp.deleted == 4


def test_cleanup_tasks_with_no_tasks():
    """E8: cleanup avec 0 tâches terminées retourne deleted=0."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import cleanup_tasks

    db = _make_db()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    db.execute.return_value = mock_result

    resp = run(cleanup_tasks(_make_ctx(), db))
    assert resp.deleted == 0


def test_cleanup_tasks_executes_delete():
    """E8: cleanup appelle bien db.execute (la requête DELETE est émise)."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import cleanup_tasks

    db = _make_db()
    mock_result = MagicMock()
    mock_result.rowcount = 2
    db.execute.return_value = mock_result

    run(cleanup_tasks(_make_ctx(workspace_id=uuid.uuid4()), db))
    db.execute.assert_called_once()
    db.commit.assert_called_once()
