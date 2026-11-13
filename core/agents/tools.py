import logging
import os
import sys
import threading
import time

import httpx
from langchain_core.tools import tool

from config import settings

logger = logging.getLogger("hiveflows")

CORE_BASE_URL = f"http://localhost:{settings.core_port}"
SKILLS_ROOT = os.environ.get("SKILLS_PATH", "/skills")

# ── Skills tools (BaseTool → LangChain StructuredTool) ──────────────────────

def _ensure_skills_path():
    if SKILLS_ROOT not in sys.path:
        sys.path.insert(0, SKILLS_ROOT)


def _load_skills_tools() -> dict:
    """Import BaseTool subclasses from /skills/tools/ and convert to LangChain tools."""
    registry = {}
    _ensure_skills_path()
    try:
        from tools.web.url2markdown import URL2MarkdownTool
        registry["url2markdown"] = URL2MarkdownTool().as_langchain_tool()
    except Exception as e:
        logger.warning(f"Failed to load url2markdown tool: {e}")
    try:
        from tools.fs.convert_file import ConvertFileTool
        registry["convert_file"] = ConvertFileTool().as_langchain_tool()
    except Exception as e:
        logger.warning(f"Failed to load convert_file tool: {e}")
    try:
        from tools.web.get_weather import GetWeatherTool
        registry["get_weather"] = GetWeatherTool().as_langchain_tool()
    except Exception as e:
        logger.warning(f"Failed to load get_weather tool: {e}")
    return registry


SKILLS_TOOLS: dict = _load_skills_tools()


def with_workspace_output_dir(lc_tool, ws_dir: str):
    """Generic: for any LangChain tool with output_dir in its args_schema,
    return a NEW StructuredTool that injects ws_dir as output_dir default.

    We can't just patch args_schema because the original _run closure has
    self.input_schema hardcoded — we must also wrap the func itself.
    """
    from pydantic import Field
    from langchain_core.tools import StructuredTool

    schema = getattr(lc_tool, "args_schema", None)
    if schema is None or "output_dir" not in getattr(schema, "model_fields", {}):
        return lc_tool

    finfo = schema.model_fields["output_dir"]
    desc = getattr(finfo, "description", None) or "Output directory"

    class _WsSchema(schema):  # type: ignore[valid-type]
        output_dir: str = Field(default=ws_dir, description=desc)

    _WsSchema.__name__ = schema.__name__ + "Ws"

    original_func = lc_tool.func

    def _ws_run(**kwargs):
        kwargs["output_dir"] = ws_dir  # always force workspace dir
        return original_func(**kwargs)

    return StructuredTool.from_function(
        func=_ws_run,
        name=lc_tool.name,
        description=lc_tool.description,
        args_schema=_WsSchema,
    )

# ── Agent-to-agent hop tracking (thread-local) ──────────────────────────────

MAX_AGENT_HOPS = 3
_hop_ctx = threading.local()


def _get_hop() -> int:
    return getattr(_hop_ctx, "count", 0)


def _set_hop(n: int):
    _hop_ctx.count = n


def make_translate_tools(workspace_id: str, workspace_dir: str):
    """Factory: retourne translate_text et translate_file pour un workspace.

    Les deux tools sont fire-and-forget — ils dispatche vers Celery et retournent
    immédiatement un task_id. L'utilisateur est notifié via SSE (task_done) quand c'est fini.
    """
    api_key = settings.api_keys_list[0] if settings.api_keys_list else ""
    headers = {"Content-Type": "application/json", "X-HF-API-Key": api_key}

    def _dispatch(task_type: str, params: dict) -> str:
        """Dispatche un task Celery et retourne le task_id."""
        params["workspace_id"] = workspace_id
        try:
            resp = httpx.post(
                f"{CORE_BASE_URL}/tasks/run",
                json={"type": task_type, "params": params},
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                return f"Error starting {task_type}: {resp.text}"
            return resp.json()["task_id"]
        except Exception as e:
            return f"Error: {e}"

    @tool
    def translate_text(
        text: str,
        target_lang: str = "fr-fr",
        model: str = "deepseek/deepseek-chat-v3.1",
    ) -> str:
        """Translate a text to the target language. Handles long texts automatically by splitting into chunks.

        This is asynchronous — returns immediately. A notification will appear in the chat when translation is complete.

        Args:
            text: The text to translate.
            target_lang: Target language code (e.g. fr-fr, en-us, es-es, de-de).
            model: LLM model via OpenRouter (default: deepseek/deepseek-chat-v3.1).

        Returns:
            Confirmation with task_id, or an error message.
        """
        task_id = _dispatch("translate", {"text": text, "target_lang": target_lang, "model": model})
        if task_id.startswith("Error"):
            return task_id
        return f"Translation started (task_id={task_id}). You will be notified when complete."

    @tool
    def translate_file(
        input_file: str,
        output_file: str,
        target_lang: str = "fr-fr",
        model: str = "deepseek/deepseek-chat-v3.1",
    ) -> str:
        """Translate a file in the workspace. Reads input_file, translates it, and writes output_file.

        This is asynchronous — returns immediately. A notification will appear in the chat when translation is complete.

        Args:
            input_file: Source file path relative to workspace root (e.g. "documents/report.txt").
            output_file: Output file path relative to workspace root (e.g. "documents/report_fr.txt").
            target_lang: Target language code (e.g. fr-fr, en-us, es-es, de-de).
            model: LLM model via OpenRouter (default: deepseek/deepseek-chat-v3.1).

        Returns:
            Confirmation with task_id, or an error message.
        """
        task_id = _dispatch(
            "translate_file",
            {
                "input_file": input_file,
                "output_file": output_file,
                "workspace_dir": workspace_dir,
                "target_lang": target_lang,
                "model": model,
            },
        )
        if task_id.startswith("Error"):
            return task_id
        return (
            f"File translation started (task_id={task_id}). "
            f"'{input_file}' → '{output_file}' [{target_lang}]. "
            "You will be notified when complete."
        )

    return translate_text, translate_file


def make_message_agent_tool(workspace_id: str):
    """Factory: returns a message_agent tool bound to a specific workspace.

    Uses thread-local hop counter to enforce max depth across call chains.
    """

    @tool
    def message_agent(agent_id: str, message: str) -> str:
        """Send a message to another agent in the same workspace and return its response.

        Use this to delegate tasks or ask questions to specialized agents.

        Args:
            agent_id: The ID of the target agent (must be in the same workspace).
            message: The message to send to the target agent.

        Returns:
            The target agent's text response, or an error message.
        """
        # Lazy import to avoid circular dependency (manager imports factory imports tools)
        from agents.manager import agent_manager

        # ── F2: hop limit ──
        current_hop = _get_hop()
        if current_hop >= MAX_AGENT_HOPS:
            return (
                f"Error: maximum agent-to-agent hop limit reached ({MAX_AGENT_HOPS}). "
                "Cannot call another agent from this depth."
            )

        # ── F2: same workspace restriction ──
        try:
            config = agent_manager._get_config(agent_id, workspace_id)
        except ValueError:
            return (
                f"Error: agent '{agent_id}' not found in this workspace. "
                "You can only message agents that belong to the same workspace."
            )

        ws_str = str(config.workspace_id) if config.workspace_id else None
        if ws_str != workspace_id:
            return (
                f"Error: agent '{agent_id}' belongs to a different workspace. "
                "Cross-workspace communication is not allowed."
            )

        # ── Invoke with hop tracking ──
        _set_hop(current_hop + 1)
        try:
            logger.info(
                f"message_agent: '{agent_id}' called (workspace={workspace_id}, hop={current_hop + 1}/{MAX_AGENT_HOPS})"
            )
            import asyncio
            result = asyncio.run(agent_manager.invoke(
                agent_id=agent_id,
                message=message,
                workspace_id=workspace_id,
            ))
            return result.get("response", "(no response)")
        except Exception as e:
            logger.error(f"message_agent error calling '{agent_id}': {e!r}")
            return f"Error calling agent '{agent_id}': {e}"
        finally:
            _set_hop(current_hop)

    @tool
    def message_agent_async(agent_id: str, message: str) -> str:
        """Send an asynchronous (fire-and-forget) message to another agent in the same workspace.

        Unlike message_agent, this does NOT wait for a response. Use this when you want
        to notify or trigger another agent without blocking.

        Args:
            agent_id: The ID of the target agent (must be in the same workspace).
            message: The message to send to the target agent.

        Returns:
            A confirmation that the message was queued, or an error message.
        """
        from agents.manager import agent_manager
        import json
        import redis

        # ── Same workspace restriction ──
        try:
            config = agent_manager._get_config(agent_id, workspace_id)
        except ValueError:
            return f"Error: agent '{agent_id}' not found in this workspace."

        ws_str = str(config.workspace_id) if config.workspace_id else None
        if ws_str != workspace_id:
            return f"Error: agent '{agent_id}' belongs to a different workspace."

        # ── Publish to Redis ──
        try:
            r = redis.Redis.from_url(settings.redis_url)
            payload = json.dumps({
                "agent_id": agent_id,
                "message": message,
                "workspace_id": workspace_id,
            })
            r.publish("hiveflows:agent_messages", payload)
            logger.info(f"message_agent_async: queued message for '{agent_id}' (workspace={workspace_id})")
            return f"Message queued for agent '{agent_id}'. It will process asynchronously."
        except Exception as e:
            logger.error(f"message_agent_async Redis error: {e!r}")
            return f"Error queuing message: {e}"

    return message_agent, message_agent_async


def _run_script_impl(script: str, type: str, timeout: int, workspace_id: str | None = None) -> str:
    """Shared implementation for run_script — submits to Celery and polls for result."""
    api_key = settings.api_keys_list[0]
    headers = {"Content-Type": "application/json", "X-HF-API-Key": api_key}

    body: dict = {"type": type, "script": script, "timeout": timeout}
    if workspace_id:
        body["workspace_id"] = workspace_id

    resp = httpx.post(f"{CORE_BASE_URL}/tasks/run", json=body, headers=headers, timeout=10)
    if resp.status_code != 200:
        return f"Error submitting task: {resp.text}"

    task_id = resp.json()["task_id"]

    for _ in range(timeout + 10):
        time.sleep(1)
        poll = httpx.get(f"{CORE_BASE_URL}/tasks/{task_id}", headers=headers, timeout=10)
        data = poll.json()
        if data["status"] == "success":
            result = data.get("result", {})
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            returncode = result.get("returncode", -1)
            if returncode == 0:
                return stdout or "(no output)"
            return f"Script failed (exit {returncode}).\nstdout: {stdout}\nstderr: {stderr}"
        if data["status"] == "failed":
            return f"Task failed: {data.get('error', 'unknown error')}"

    return f"Task {task_id} timed out waiting for result."


@tool
def run_script(script: str, type: str = "python", timeout: int = 120) -> str:
    """Execute a Python or Bash script via the HiveFlows worker system.

    Args:
        script: The script source code to execute.
        type: Either "python" or "bash".
        timeout: Max execution time in seconds (default 120).

    Returns:
        The stdout output of the script, or an error message.
    """
    return _run_script_impl(script, type, timeout)


@tool
def run_skill_script(skill: str, script_name: str, script_args: str = "", timeout: int = 120) -> str:
    """Execute a script from a skill directory via the worker system.

    Args:
        skill: The skill name (directory name under /skills/).
        script_name: The script filename (e.g. "hello.py" or "sysinfo.sh").
        script_args: Optional arguments to pass to the script (space-separated).
        timeout: Max execution time in seconds (default 120).

    Returns:
        The stdout output of the script, or an error message.
    """
    script_path = os.path.join(SKILLS_ROOT, skill, "scripts", script_name)
    if not os.path.isfile(script_path):
        return f"Script not found: {script_path}"

    content = open(script_path, encoding="utf-8").read()
    script_type = "bash" if script_name.endswith(".sh") else "python"

    if script_args and script_type == "python":
        argv_list = script_args.split()
        content = f"import sys; sys.argv = ['script.py'] + {argv_list!r}\n" + content
    elif script_args and script_type == "bash":
        content = f"set -- {script_args}\n" + content

    return _run_script_impl(content, script_type, timeout)


def make_run_tools(workspace_id: str):
    """Factory: retourne run_script et run_skill_script scopés à un workspace.

    Injecte le workspace_id dans les appels POST /tasks/run pour que les tâches
    apparaissent dans le bus d'activité et le dashboard Activity du bon workspace.
    """

    @tool
    def run_script(script: str, type: str = "python", timeout: int = 120) -> str:
        """Execute a Python or Bash script via the HiveFlows worker system.

        Args:
            script: The script source code to execute.
            type: Either "python" or "bash".
            timeout: Max execution time in seconds (default 120).

        Returns:
            The stdout output of the script, or an error message.
        """
        return _run_script_impl(script, type, timeout, workspace_id=workspace_id)

    @tool
    def run_skill_script(skill: str, script_name: str, script_args: str = "", timeout: int = 120) -> str:
        """Execute a script from a skill directory via the worker system.

        Args:
            skill: The skill name (directory name under /skills/).
            script_name: The script filename (e.g. "hello.py" or "sysinfo.sh").
            script_args: Optional arguments to pass to the script (space-separated).
            timeout: Max execution time in seconds (default 120).

        Returns:
            The stdout output of the script, or an error message.
        """
        script_path = os.path.join(SKILLS_ROOT, skill, "scripts", script_name)
        if not os.path.isfile(script_path):
            return f"Script not found: {script_path}"

        content = open(script_path, encoding="utf-8").read()
        script_type = "bash" if script_name.endswith(".sh") else "python"

        if script_args and script_type == "python":
            argv_list = script_args.split()
            content = f"import sys; sys.argv = ['script.py'] + {argv_list!r}\n" + content
        elif script_args and script_type == "bash":
            content = f"set -- {script_args}\n" + content

        return _run_script_impl(content, script_type, timeout, workspace_id=workspace_id)

    return run_script, run_skill_script
