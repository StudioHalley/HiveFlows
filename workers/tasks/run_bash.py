import os
import subprocess
import uuid

from celery_app import app
from activity_bus import publish_activity

DEFAULT_TIMEOUT = int(os.getenv("TASK_TIMEOUT", "120"))
WORKSPACES_ROOT = "/workspaces"


@app.task(bind=True, name="tasks.run_bash")
def run_bash_script(
    self,
    script: str,
    timeout: int | None = None,
    workspace_id: str | None = None,
    db_task_id: str | None = None,
):
    """Execute a Bash script in an isolated workspace directory."""
    timeout = timeout or DEFAULT_TIMEOUT
    ws_id = workspace_id or str(uuid.uuid4())
    work_dir = os.path.join(WORKSPACES_ROOT, ws_id)
    os.makedirs(work_dir, exist_ok=True)

    script_path = os.path.join(work_dir, "script.sh")
    with open(script_path, "w") as f:
        f.write(script)
    os.chmod(script_path, 0o755)

    try:
        result = subprocess.run(
            ["bash", script_path],
            capture_output=True, text=True,
            timeout=timeout, cwd=work_dir,
        )
        data = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "workspace_id": ws_id,
        }
        publish_activity(workspace_id, "task_done", "system", data, task_id=db_task_id)
        return data
    except subprocess.TimeoutExpired:
        data = {
            "stdout": "", "stderr": f"Script timed out after {timeout}s",
            "returncode": -1, "workspace_id": ws_id,
        }
        publish_activity(workspace_id, "task_error", "system",
                         {"error": data["stderr"]}, task_id=db_task_id)
        return data
