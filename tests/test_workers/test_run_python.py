"""Unit tests for the run_python worker task."""
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "workers")

# Mock celery_app before importing the task module
mock_celery = MagicMock()
mock_celery.task = lambda *a, **kw: lambda fn: fn
sys.modules["celery_app"] = type(sys)("celery_app")
sys.modules["celery_app"].app = mock_celery

import tasks.run_python as mod_run_python  # noqa: E402
from tasks.run_python import run_python_script  # noqa: E402


@pytest.fixture(autouse=True)
def use_tmp_workspace(tmp_path):
    orig = mod_run_python.WORKSPACES_ROOT
    mod_run_python.WORKSPACES_ROOT = str(tmp_path)
    yield
    mod_run_python.WORKSPACES_ROOT = orig


class TestRunPythonScript:
    def test_simple_print(self, tmp_path):
        result = run_python_script(
            None, script="print('hello')", workspace_id="test-py-1",
        )
        assert result["returncode"] == 0
        assert "hello" in result["stdout"]
        assert result["workspace_id"] == "test-py-1"

    def test_math_computation(self, tmp_path):
        result = run_python_script(
            None, script="print(2 ** 10)", workspace_id="test-py-2",
        )
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "1024"

    def test_stderr_on_error(self, tmp_path):
        result = run_python_script(
            None, script="raise ValueError('boom')", workspace_id="test-py-3",
        )
        assert result["returncode"] != 0
        assert "ValueError" in result["stderr"]
        assert "boom" in result["stderr"]

    def test_syntax_error(self, tmp_path):
        result = run_python_script(
            None, script="def broken(", workspace_id="test-py-4",
        )
        assert result["returncode"] != 0
        assert "SyntaxError" in result["stderr"]

    def test_timeout(self, tmp_path):
        result = run_python_script(
            None, script="import time; time.sleep(10)",
            timeout=1, workspace_id="test-py-5",
        )
        assert result["returncode"] == -1
        assert "timed out" in result["stderr"]

    def test_auto_generates_workspace_id(self, tmp_path):
        result = run_python_script(None, script="print('ok')")
        assert result["returncode"] == 0
        assert result["workspace_id"]  # UUID was generated

    def test_multiline_script(self, tmp_path):
        script = "x = 5\ny = 10\nprint(x + y)"
        result = run_python_script(
            None, script=script, workspace_id="test-py-6",
        )
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "15"
