"""Celery task — traduit un texte ou un fichier avec TranslateTool.

Supporte le mode texte (text=) et le mode fichier (input_file= + output_file= + workspace_dir=).
Publie des événements dans le bus d'activité Redis (activity_bus).
"""
import logging
import os
import sys

from celery_app import app
from activity_bus import publish_activity

logger = logging.getLogger(__name__)

SKILLS_ROOT = os.environ.get("SKILLS_PATH", "/skills")


@app.task(bind=True, name="tasks.run_translate")
def run_translate(
    self,
    # Mode texte
    text: str | None = None,
    # Mode fichier
    input_file: str | None = None,
    output_file: str | None = None,
    workspace_dir: str | None = None,
    # Paramètres de traduction
    target_lang: str = "fr-fr",
    provider: str = "openai",
    base_url: str = "https://openrouter.ai/api/v1",
    model: str = "deepseek/deepseek-chat-v3.1",
    api_key: str | None = None,
    max_chars: int = 5000,
    # Contexte pour les notifications
    workspace_id: str | None = None,
    db_task_id: str | None = None,
):
    """Traduit un texte ou un fichier avec suivi de progression et notifications SSE."""
    tool_name = "translate_file" if input_file else "translate_text"
    task_id_short = self.request.id[:8] if self.request.id else "?"
    source = "system"

    logger.info(f"[{task_id_short}] START {tool_name} → lang={target_lang} model={model} ws={workspace_id}")

    # 1. Charger TranslateTool depuis /skills
    if SKILLS_ROOT not in sys.path:
        sys.path.insert(0, SKILLS_ROOT)

    try:
        from tools.nlp.translate import TranslateInput, TranslateTool
    except ImportError as e:
        err = f"Failed to import TranslateTool: {e}"
        logger.error(err)
        publish_activity(workspace_id, "task_error", source, {"error": err, "tool": tool_name}, task_id=db_task_id)
        raise

    # 2. Lire le fichier source si mode fichier
    if input_file:
        if not workspace_dir:
            err = "workspace_dir is required when using input_file"
            publish_activity(workspace_id, "task_error", source, {"error": err, "tool": tool_name}, task_id=db_task_id)
            raise ValueError(err)
        full_input = os.path.join(workspace_dir, input_file.lstrip("/"))
        logger.info(f"[{task_id_short}] reading file: {full_input}")
        try:
            with open(full_input, encoding="utf-8") as f:
                text = f.read()
            logger.info(f"[{task_id_short}] file read: {len(text)} chars")
        except OSError as e:
            err = f"Cannot read input file '{input_file}': {e}"
            publish_activity(workspace_id, "task_error", source, {"error": err, "tool": tool_name}, task_id=db_task_id)
            raise

    if not text or not text.strip():
        err = "No content to translate (empty text)"
        publish_activity(workspace_id, "task_error", source, {"error": err, "tool": tool_name}, task_id=db_task_id)
        return {"status": "error", "error": err}

    # 3. Découper en chunks pour le suivi de progression
    tool = TranslateTool()
    chunks = tool._split_into_chunks(text, max_chars)
    total = len(chunks)
    logger.info(f"[{task_id_short}] {total} chunks × ~{max_chars} chars → {target_lang}")

    self.update_state(state="PROGRESS", meta={"current": 0, "total": total, "tool": tool_name})
    publish_activity(workspace_id, "task_progress", source,
                     {"current": 0, "total": total, "detail": f"0/{total} chunks"}, task_id=db_task_id)

    # 4. Traduire chunk par chunk
    params = TranslateInput(
        text=text, target_lang=target_lang, provider=provider,
        base_url=base_url, model=model, api_key=api_key, max_chars=max_chars,
    )

    translated_chunks = []
    for i, chunk in enumerate(chunks, 1):
        logger.info(f"[{task_id_short}] chunk {i}/{total} — {len(chunk)} chars …")
        t0 = __import__("time").monotonic()
        try:
            if provider == "fabric":
                translated = tool._translate_with_fabric(chunk, target_lang)
            elif provider == "openai":
                translated = tool._translate_with_openai(params, chunk)
            elif provider == "anthropic":
                translated = tool._translate_with_anthropic(params, chunk)
            else:
                raise ValueError(f"Unsupported provider: {provider}")
            elapsed = __import__("time").monotonic() - t0
            logger.info(f"[{task_id_short}] chunk {i}/{total} ✓  ({elapsed:.1f}s)")
            translated_chunks.append(translated)
        except Exception as e:
            err = f"Error translating chunk {i}/{total}: {e}"
            logger.error(f"[{task_id_short}] {err}")
            publish_activity(workspace_id, "task_error", source, {"error": err, "tool": tool_name}, task_id=db_task_id)
            raise RuntimeError(err) from e

        self.update_state(state="PROGRESS", meta={"current": i, "total": total, "tool": tool_name})
        publish_activity(workspace_id, "task_progress", source,
                         {"current": i, "total": total, "detail": f"{i}/{total} chunks"}, task_id=db_task_id)

    # 5. Assembler le résultat
    final_text = "\n\n".join(translated_chunks)

    # 6. Écrire le fichier de sortie si mode fichier
    if output_file and workspace_dir:
        full_output = os.path.join(workspace_dir, output_file.lstrip("/"))
        os.makedirs(os.path.dirname(full_output) or workspace_dir, exist_ok=True)
        with open(full_output, "w", encoding="utf-8") as f:
            f.write(final_text)

    # 7. Notifier la complétion
    result_data: dict = {
        "tool": tool_name, "chunks_count": total,
        "target_lang": target_lang, "output_file": output_file,
    }
    publish_activity(workspace_id, "task_done", source, result_data, task_id=db_task_id)
    logger.info(f"[{task_id_short}] DONE ✓ — {total} chunks, lang={target_lang}, out={output_file or '(text)'}")

    return {
        "translated_text": final_text if not output_file else None,
        "output_file": output_file,
        "chunks_count": total,
        "target_lang": target_lang,
    }
