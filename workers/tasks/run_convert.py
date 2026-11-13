"""Celery task — convertit un fichier d'un format à un autre via ConvertFileTool.

S'appuie sur le skill `tools/fs/convert_file.py` chargé depuis /skills.
Publie des événements task_progress / task_done / task_error sur le bus d'activité.
"""
import logging
import os
import sys

from celery_app import app
from activity_bus import publish_activity

logger = logging.getLogger(__name__)

SKILLS_ROOT = os.environ.get("SKILLS_PATH", "/skills")


@app.task(bind=True, name="tasks.run_convert")
def run_convert(
    self,
    input_file: str,
    workspace_dir: str,
    output_format: str,
    output_file: str | None = None,
    workspace_id: str | None = None,
    db_task_id: str | None = None,
):
    """Convertit input_file (chemin relatif workspace) au format output_format.

    Si output_file est fourni (relatif workspace), il est utilisé comme destination ;
    sinon le skill choisit automatiquement un chemin à côté de la source.
    """
    source = "system"
    task_id_short = (self.request.id or "?")[:8]
    tool_name = "convert_file"

    logger.info(
        f"[{task_id_short}] START convert_file "
        f"file={input_file} → {output_format} ws={workspace_id}"
    )

    if SKILLS_ROOT not in sys.path:
        sys.path.insert(0, SKILLS_ROOT)

    try:
        from tools.fs.convert_file import ConvertFileInput, ConvertFileTool
    except ImportError as e:
        err = f"Failed to import ConvertFileTool: {e}"
        logger.error(err)
        publish_activity(workspace_id, "task_error", source,
                         {"error": err, "tool": tool_name}, task_id=db_task_id)
        raise

    rel_input = input_file.lstrip("/")
    full_input = os.path.join(workspace_dir, rel_input)
    if not os.path.exists(full_input):
        err = f"Fichier introuvable : '{input_file}'"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(workspace_id, "task_error", source,
                         {"error": err, "tool": tool_name}, task_id=db_task_id)
        raise FileNotFoundError(err)

    full_output = None
    if output_file:
        full_output = os.path.join(workspace_dir, output_file.lstrip("/"))

    self.update_state(state="PROGRESS",
                      meta={"current": 10, "total": 100,
                            "detail": f"Conversion → {output_format}…"})
    publish_activity(workspace_id, "task_progress", source,
                     {"current": 10, "total": 100,
                      "detail": f"Conversion → {output_format}…"},
                     task_id=db_task_id)

    tool = ConvertFileTool()
    result = tool.run(ConvertFileInput(
        input_path=full_input,
        output_format=output_format,
        output_path=full_output,
    ))

    if result.status == "error":
        err = result.error or "Erreur de conversion inconnue"
        logger.error(f"[{task_id_short}] {err}")
        publish_activity(workspace_id, "task_error", source,
                         {"error": err, "tool": tool_name}, task_id=db_task_id)
        raise RuntimeError(err)

    abs_output = result.data.get("output_path", "")
    if abs_output.startswith(workspace_dir):
        rel_output = abs_output[len(workspace_dir):].lstrip("/")
    else:
        rel_output = os.path.basename(abs_output)

    payload = {
        "tool": tool_name,
        "input_file": input_file,
        "output_file": rel_output,
        "input_format": result.data.get("input_format"),
        "output_format": result.data.get("output_format"),
        "size_bytes": result.data.get("size_bytes"),
    }

    self.update_state(state="PROGRESS", meta={"current": 100, "total": 100})
    publish_activity(workspace_id, "task_done", source, payload, task_id=db_task_id)
    logger.info(f"[{task_id_short}] DONE ✓ → {rel_output}")
    return payload
