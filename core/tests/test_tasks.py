"""Tests — Task model & routes_tasks schemas (Phase 7 Bloc A12)."""
import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError


# ── Model field defaults ──────────────────────────────────────────────────────

def test_task_model_fields():
    """Task model has all required fields with correct types."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from models.task import Task

    t = Task.__table__
    col_names = {c.name for c in t.columns}

    # New fields
    assert "name" in col_names
    assert "progress" in col_names
    assert "progress_detail" in col_names
    assert "triggered_by" in col_names
    assert "backend_ref" in col_names
    assert "params" in col_names
    assert "started_at" in col_names
    assert "completed_at" in col_names

    # Old fields removed
    assert "celery_id" not in col_names
    assert "payload" not in col_names

    # Kept fields
    assert "id" in col_names
    assert "type" in col_names
    assert "status" in col_names
    assert "result" in col_names
    assert "error" in col_names
    assert "user_id" in col_names
    assert "workspace_id" in col_names
    assert "created_at" in col_names
    assert "updated_at" in col_names


def test_task_progress_default():
    """progress column has a Python-level default of 0."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from models.task import Task

    progress_col = Task.__table__.c["progress"]
    assert progress_col.default.arg == 0


# ── Pydantic schemas ──────────────────────────────────────────────────────────

def test_task_run_request_valid():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import TaskRunRequest

    req = TaskRunRequest(type="python", script="print('hello')")
    assert req.type == "python"
    assert req.name is None
    assert req.params is None


def test_task_run_request_with_name():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import TaskRunRequest

    req = TaskRunRequest(type="translate_file", name="Translate README", params={"target_lang": "fr-fr"})
    assert req.name == "Translate README"
    assert req.params == {"target_lang": "fr-fr"}


def test_task_read_schema():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import TaskRead

    now = datetime.utcnow()
    t = TaskRead(
        task_id=str(uuid.uuid4()),
        name="Run Python Script",
        type="python",
        status="success",
        progress=100,
        progress_detail=None,
        triggered_by="user:abc",
        backend_ref="celery-uuid-123",
        params={"script": "print(1)"},
        result={"output": "1\n"},
        error=None,
        started_at=now,
        completed_at=now,
        created_at=now,
        updated_at=now,
    )
    assert t.status == "success"
    assert t.progress == 100
    assert t.backend_ref == "celery-uuid-123"


def test_task_progress_schema():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import TaskProgress

    p = TaskProgress(task_id=str(uuid.uuid4()), status="running", progress=42, progress_detail="Step 3/7")
    assert p.progress == 42
    assert p.progress_detail == "Step 3/7"


def test_cleanup_response_schema():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import CleanupResponse

    r = CleanupResponse(deleted=5)
    assert r.deleted == 5


# ── Status values ─────────────────────────────────────────────────────────────

def test_task_run_response_has_backend_ref():
    """TaskRunResponse uses backend_ref instead of celery_id."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import TaskRunResponse

    r = TaskRunResponse(task_id=str(uuid.uuid4()), backend_ref="celery-xyz", status="pending")
    assert r.backend_ref == "celery-xyz"
    assert not hasattr(r, "celery_id")


def test_cancelled_is_valid_status():
    """'cancelled' est un status valide dans TaskRead."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tasks import TaskRead

    now = datetime.utcnow()
    t = TaskRead(
        task_id=str(uuid.uuid4()), name=None, type="bash",
        status="cancelled", progress=0, progress_detail=None,
        triggered_by="user:x", backend_ref="ref", params=None,
        result=None, error=None, started_at=now, completed_at=now,
        created_at=now, updated_at=now,
    )
    assert t.status == "cancelled"


# ── D21 — GET /tools/available filtre par extension ───────────────────────────

def test_human_tools_registry_has_all_tools():
    """Le registre contient les 5 tools Phase 7 Bloc D."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import _registry

    expected_keys = {
        "translate_file",
        "fabric_extract_wisdom",
        "fabric_summarize",
        "fabric_extract_references",
        "fabric_run",
    }
    assert expected_keys.issubset(set(_registry.keys())), \
        f"Missing tools: {expected_keys - set(_registry.keys())}"


def test_get_available_tools_filter_by_ext():
    """get_available_tools filtre correctement par extension."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import get_available_tools

    # .md — tous les tools (translate + fabric)
    tools_md = get_available_tools(".md")
    keys_md = {t.key for t in tools_md}
    assert "translate_file" in keys_md
    assert "fabric_extract_wisdom" in keys_md

    # .csv — seulement translate_file (fabric ne supporte pas .csv)
    tools_csv = get_available_tools(".csv")
    keys_csv = {t.key for t in tools_csv}
    assert "translate_file" in keys_csv
    assert "fabric_extract_wisdom" not in keys_csv

    # .pdf — aucun tool
    tools_pdf = get_available_tools(".pdf")
    assert tools_pdf == []


def test_get_available_tools_no_filter():
    """Sans filtre, tous les tools sont retournés."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import get_available_tools, _registry

    tools_all = get_available_tools()
    assert len(tools_all) == len(_registry)


# ── D22 — GET /tools/fabric/patterns format ───────────────────────────────────

def test_tool_param_has_required_fields():
    """ToolParam expose key, type, label, options, options_endpoint, default."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import ToolParam

    p = ToolParam(key="target_lang", type="select", label="Langue cible",
                  options=["fr-fr", "en-us"], default="fr-fr")
    assert p.key == "target_lang"
    assert p.type == "select"
    assert p.options == ["fr-fr", "en-us"]
    assert p.default == "fr-fr"
    assert p.options_endpoint is None


def test_human_tool_pinned_and_group():
    """Les tools fabric sont bien marqués pinned=True et group='fabric'."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import _registry

    wisdom = _registry["fabric_extract_wisdom"]
    assert wisdom.pinned is True
    assert wisdom.group == "fabric"
    assert wisdom.ui == "inline"

    fabric_run = _registry["fabric_run"]
    assert fabric_run.pinned is False
    assert fabric_run.group == "fabric"
    assert fabric_run.ui == "modal"


# ── D23 — POST /tools/execute prepare_execution ───────────────────────────────

def test_prepare_execution_translate():
    """prepare_execution retourne le bon task Celery pour translate_file."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import prepare_execution

    ws_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    celery_name, kwargs, label = prepare_execution(
        tool_key="translate_file",
        workspace_id=ws_id,
        workspace_slug="my-ws",
        file_path="documents/report.md",
        params={"target_lang": "en-us", "model": "openai/gpt-4o-mini"},
        task_uuid_str=task_id,
    )

    assert celery_name == "tasks.run_translate"
    assert kwargs["input_file"] == "documents/report.md"
    assert kwargs["target_lang"] == "en-us"
    assert kwargs["model"] == "openai/gpt-4o-mini"
    assert ".hive/outputs/" in kwargs["output_file"]
    assert "en-us" in kwargs["output_file"]
    assert kwargs["db_task_id"] == task_id
    assert kwargs["workspace_id"] == ws_id
    assert "Traduire" in label
    assert "en-us" in label


def test_prepare_execution_fabric_pinned():
    """prepare_execution mappe fabric_extract_wisdom → tasks.run_fabric avec pattern=extract_wisdom."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import prepare_execution

    ws_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    celery_name, kwargs, label = prepare_execution(
        tool_key="fabric_extract_wisdom",
        workspace_id=ws_id,
        workspace_slug="my-ws",
        file_path="notes/meeting.txt",
        params={"model": "deepseek/deepseek-chat-v3.1"},
        task_uuid_str=task_id,
    )

    assert celery_name == "tasks.run_fabric"
    assert kwargs["pattern"] == "extract_wisdom"
    assert kwargs["input_file"] == "notes/meeting.txt"
    assert ".hive/outputs/" in kwargs["output_file"]
    assert "extract_wisdom" in kwargs["output_file"]
    assert f"hive-app-{ws_id}-fabric" == kwargs["container_name"]
    assert kwargs["container_port"] == 8080
    assert "Fabric" in label


def test_prepare_execution_fabric_run_with_pattern():
    """fabric_run avec pattern passé dans params."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import prepare_execution

    ws_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    celery_name, kwargs, label = prepare_execution(
        tool_key="fabric_run",
        workspace_id=ws_id,
        workspace_slug="my-ws",
        file_path="article.md",
        params={"pattern": "analyze_claims", "model": "openai/gpt-4o-mini"},
        task_uuid_str=task_id,
    )

    assert celery_name == "tasks.run_fabric"
    assert kwargs["pattern"] == "analyze_claims"
    assert "analyze_claims" in kwargs["output_file"]


def test_prepare_execution_unknown_tool():
    """prepare_execution lève ValueError pour un tool inconnu."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from services.human_tools import prepare_execution
    import pytest

    with pytest.raises(ValueError, match="non trouvé"):
        prepare_execution(
            tool_key="nonexistent_tool",
            workspace_id="ws-id",
            workspace_slug="ws",
            file_path="file.md",
            params={},
            task_uuid_str=str(uuid.uuid4()),
        )


# ── D24 — ui/pinned/group flags dans la réponse ──────────────────────────────

def test_tool_execute_request_schema():
    """ToolExecuteRequest valide correctement les champs requis."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tools import ToolExecuteRequest

    req = ToolExecuteRequest(
        tool_key="translate_file",
        file_path="docs/readme.md",
        params={"target_lang": "fr-fr"},
    )
    assert req.tool_key == "translate_file"
    assert req.file_path == "docs/readme.md"
    assert req.params["target_lang"] == "fr-fr"


def test_tool_execute_request_default_params():
    """params est optionnel avec un dict vide par défaut."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from api.routes_tools import ToolExecuteRequest

    req = ToolExecuteRequest(tool_key="fabric_summarize", file_path="note.md")
    assert req.params == {}
