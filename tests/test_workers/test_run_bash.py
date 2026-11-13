"""Unit tests for the run_bash worker task."""
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "workers")

# Mock celery_app before importing the task module
mock_celery = MagicMock()
mock_celery.task = lambda *a, **kw: lambda fn: fn
sys.modules["celery_app"] = type(sys)("celery_app")
sys.modules["celery_app"].app = mock_celery

import tasks.run_bash as mod_run_bash  # noqa: E402
from tasks.run_bash import run_bash_script  # noqa: E402


@pytest.fixture(autouse=True)
def use_tmp_workspace(tmp_path):
    orig = mod_run_bash.WORKSPACES_ROOT
    mod_run_bash.WORKSPACES_ROOT = str(tmp_path)
    yield
    mod_run_bash.WORKSPACES_ROOT = orig


class TestRunBashScript:
    def test_echo(self, tmp_path):
        result = run_bash_script(
            None, script="echo hello", workspace_id="test-sh-1",
        )
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "hello"
        assert result["workspace_id"] == "test-sh-1"

    def test_arithmetic(self, tmp_path):
        result = run_bash_script(
            None, script="echo $((2 + 3))", workspace_id="test-sh-2",
        )
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "5"

    def test_multiline(self, tmp_path):
        script = "echo line1\necho line2\necho line3"
        result = run_bash_script(
            None, script=script, workspace_id="test-sh-3",
        )
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "line1\nline2\nline3"

    def test_exit_code_nonzero(self, tmp_path):
        result = run_bash_script(
            None, script="exit 42", workspace_id="test-sh-4",
        )
        assert result["returncode"] == 42

    def test_stderr(self, tmp_path):
        result = run_bash_script(
            None, script="echo error >&2", workspace_id="test-sh-5",
        )
        assert result["returncode"] == 0
        assert "error" in result["stderr"]

    def test_timeout(self, tmp_path):
        result = run_bash_script(
            None, script="sleep 10",
            timeout=1, workspace_id="test-sh-6",
        )
        assert result["returncode"] == -1
        assert "timed out" in result["stderr"]

    def test_auto_generates_workspace_id(self, tmp_path):
        result = run_bash_script(None, script="echo ok")
        assert result["returncode"] == 0
        assert result["workspace_id"]

    def test_env_variables(self, tmp_path):
        script = "export MY_VAR=hello && echo $MY_VAR"
        result = run_bash_script(
            None, script=script, workspace_id="test-sh-7",
        )
        assert result["returncode"] == 0
        assert result["stdout"].strip() == "hello"
