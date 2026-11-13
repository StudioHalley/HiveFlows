"""Agent factory — construit un CompiledStateGraph depuis un AgentConfig DB."""
import logging
import os
from typing import Any, AsyncIterator, Iterator

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Checkpointer
from pydantic import ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from agents.llm_factory import create_llm
from agents.credential_resolver import resolve_credential
from agents.skill_loader import build_skills_prompt, load_skills
from agents.tools import SKILLS_TOOLS, make_message_agent_tool, make_run_tools, make_translate_tools, with_workspace_output_dir, run_script, run_skill_script
from config import settings
from workspace_fs import ensure_workspace_dir
from models.agent_config import AgentConfig


class ContextWindowChatModel(BaseChatModel):
    """Wraps a chat model and trims to context_window_size non-system messages.

    Keeps all SystemMessages + the last `context_window_size` non-system messages.
    The full conversation history remains in the LangGraph checkpointer; only
    what the LLM sees at each step is trimmed.
    """

    inner: Any  # BaseChatModel — Any to avoid Pydantic cross-validation issues
    context_window_size: int = 50

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _trim(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        if self.context_window_size <= 0:
            return messages
        sys_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        other = [m for m in messages if not isinstance(m, SystemMessage)]
        return sys_msgs + other[-self.context_window_size:]

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self.inner._generate(self._trim(messages), stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        return await self.inner._agenerate(self._trim(messages), stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> Iterator:
        yield from self.inner._stream(self._trim(messages), stop=stop, run_manager=run_manager, **kwargs)

    async def _astream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> AsyncIterator:
        async for chunk in self.inner._astream(self._trim(messages), stop=stop, run_manager=run_manager, **kwargs):
            yield chunk

    @property
    def _llm_type(self) -> str:
        return f"context_window_{self.inner._llm_type}"

    def bind_tools(self, tools, **kwargs):
        # Delegate bind_tools to inner (it knows the API format), then prepend trimmer
        bound = self.inner.bind_tools(tools, **kwargs)
        return RunnableLambda(self._trim) | bound

_TOOL_REGISTRY = {
    "run_script": run_script,
    "run_skill_script": run_skill_script,
    **SKILLS_TOOLS,
}

logger = logging.getLogger("hiveflows")

DEFAULT_PROMPT_PATH = "agents/prompts/default.txt"
_default_prompt_cache: str | None = None


def _get_default_prompt() -> str:
    global _default_prompt_cache
    if _default_prompt_cache is None:
        _default_prompt_cache = open(DEFAULT_PROMPT_PATH).read()
    return _default_prompt_cache


def _make_backend(
    agent_id: str,
    backend_config: dict,
    store: BaseStore | None = None,
    workspace_slug: str | None = None,
):
    """Factory de backend pour un agent, configurable depuis la DB.

    backend_config examples:
        {}                                                    → StateBackend (défaut, éphémère)
        {"type": "filesystem", "root_dir": "/agent-workspace"} → FilesystemBackend (persistant)

    Si workspace_slug est fourni → FilesystemBackend vers /workspace/{slug}/
    (les tools deepagents read_file/write_file écrivent directement dans le bon répertoire).

    Routes ajoutées si store disponible :
        /memories/ → StoreBackend (persistant, cross-conversation, isolé par agent_id)
    """
    # Workspace override: force filesystem backend scoped to workspace dir
    if workspace_slug:
        ws_root = os.path.join(settings.workspaces_data_dir, workspace_slug)
        ensure_workspace_dir(workspace_slug)
        backend_type = "filesystem"
        root_dir = ws_root
        virtual = True  # Virtual paths anchored to workspace root + traversal protection
    else:
        backend_type = backend_config.get("type", "state")
        root_dir = backend_config.get("root_dir", "/agent-workspace")
        virtual = True

    def factory(rt):
        # Sélection du backend de base selon la config DB
        if backend_type == "filesystem":
            default_backend = FilesystemBackend(root_dir=root_dir, virtual_mode=virtual)
        else:
            default_backend = StateBackend(rt)

        # Ajouter la route /memories/ vers le StoreBackend si un store est disponible
        if store is not None:
            store_backend = StoreBackend(
                rt,
                namespace=lambda ctx: (agent_id, "memories"),
            )
            return CompositeBackend(
                default=default_backend,
                routes={"/memories/": store_backend},
            )
        return default_backend

    return factory


async def build_agent_from_config(
    config: AgentConfig,
    checkpointer: Checkpointer,
    store: BaseStore | None = None,
    allowed_skills: list[str] | None = None,
    workspace_slug: str | None = None,
    db: AsyncSession | None = None,
) -> CompiledStateGraph:
    """Construit un agent deepagents depuis une AgentConfig chargée depuis la DB.

    allowed_skills: restriction RBAC (WorkspaceMember.allowed_skills).
        None = pas de restriction, [] = aucun skill, ["x"] = seulement "x".
        Intersection avec config.skills pour le résultat final.

    db: session async pour résoudre les credentials provider (optionnel — fallback env si None).
    """
    # --- LLM ---
    # Résoudre provider + clé API via la cascade (agent → workspace → global → env)
    if db is not None:
        provider, api_key, base_url = await resolve_credential(config, config.workspace_id, db)
    else:
        # Fallback sans DB (tests, agents legacy sans provider configuré)
        provider = getattr(config, "llm_provider", None) or "openrouter"
        api_key = settings.openrouter_api_key
        base_url = "https://openrouter.ai/api/v1"

    # Modèle : llm_model > llm_config["model"] > default
    llm_model = getattr(config, "llm_model", None) or config.llm_config.get("model", settings.default_llm_model)
    # Exclure 'model' et 'provider' — ils sont passés explicitement à create_llm.
    # Les agents legacy en DB ont llm_config = {"provider": "openrouter", "model": "..."}.
    _RESERVED = {"model", "provider"}
    llm_kwargs = {k: v for k, v in (getattr(config, "llm_config", None) or {}).items() if k not in _RESERVED}

    _base_llm = create_llm(provider=provider, model=llm_model, api_key=api_key, base_url=base_url, **llm_kwargs)

    # D1 — Fallback LLM optionnel : si provider échoue, essayer fallback_llm_config
    fallback_cfg = getattr(config, "fallback_llm_config", None)
    if fallback_cfg and isinstance(fallback_cfg, dict) and fallback_cfg.get("provider"):
        _fb_provider = fallback_cfg["provider"]
        _fb_model = fallback_cfg.get("model", settings.default_llm_model)
        try:
            _fb_llm = create_llm(provider=_fb_provider, model=_fb_model)
            _base_llm = _base_llm.with_fallbacks([_fb_llm])
            logger.info(f"Agent '{config.id}': fallback LLM configuré → {_fb_provider}/{_fb_model}")
        except Exception as e:
            logger.warning(f"Agent '{config.id}': impossible de créer le fallback LLM ({_fb_provider}): {e!r}")

    context_window_size = getattr(config, "context_window_size", 50) or 50
    llm = ContextWindowChatModel(inner=_base_llm, context_window_size=context_window_size)

    # --- Skills ---
    # config.skills == None  → charger tous les skills
    # config.skills == []    → aucun skill
    # config.skills == ["x"] → seulement "x"
    # allowed_skills intersects with config.skills for RBAC filtering
    effective_filter = config.skills
    if allowed_skills is not None:
        if effective_filter is None:
            effective_filter = allowed_skills
        else:
            effective_filter = [s for s in effective_filter if s in allowed_skills]
    skills = load_skills(filter_names=effective_filter)
    skills_prompt = build_skills_prompt(skills)

    # --- System prompt ---
    base_prompt = config.system_prompt or _get_default_prompt()
    system_prompt = base_prompt + skills_prompt

    # --- Tools ---
    # tools: []  or None → default (run_script + run_skill_script if skills exist)
    # tools: ["run_script"]        → only run_script
    # tools: ["none"] or unknown   → no tools → simple LLM graph (bypasses deepagents)
    no_tools_mode = False
    ws_slug = workspace_slug or getattr(config, '_workspace_slug', None)
    ws_uuid = str(config.workspace_id) if config.workspace_id else None

    if config.tools:
        _selected = [_TOOL_REGISTRY[t] for t in config.tools if t in _TOOL_REGISTRY]
        if run_skill_script in _selected and not skills:
            _selected.remove(run_skill_script)
        if _selected:
            tools = _selected
        else:
            no_tools_mode = True  # ["none"] or unrecognized → plain LLM
            tools = []
    else:
        tools = [run_script]
        if skills:
            tools.append(run_skill_script)

    # ── Workspace-scoped run_script/run_skill_script ──────────────────────────
    # Replace global tools with workspace-scoped versions so tasks are tracked
    # in the correct workspace Activity dashboard.
    if ws_uuid and not no_tools_mode:
        _ws_run_script, _ws_run_skill_script = make_run_tools(ws_uuid)
        tools = [
            _ws_run_script if (t is run_script or getattr(t, 'name', '') == 'run_script') else
            (_ws_run_skill_script if (t is run_skill_script or getattr(t, 'name', '') == 'run_skill_script') else t)
            for t in tools
        ]

    # ── App tools (apps backend installées dans le workspace) ──
    # Chargés avant les workspace-scoped overrides pour bénéficier de with_workspace_output_dir.
    if ws_uuid and db is not None and not no_tools_mode:
        from agents.app_tool_registry import get_app_tools
        app_tools = await get_app_tools(ws_uuid, db)
        tools.extend(app_tools)
        if app_tools:
            logger.info(f"Agent '{config.id}': {len(app_tools)} app tool(s) chargés depuis les apps")

    # ── Workspace-scoped tool overrides ──
    # ws_slug → file paths (slug-named dirs); ws_uuid → agent-to-agent lookup
    if not no_tools_mode:
        if ws_slug:
            # Generic: inject workspace root as output_dir default for any tool that supports it
            ws_out = os.path.join(settings.workspaces_data_dir, ws_slug)
            tools = [with_workspace_output_dir(t, ws_out) for t in tools]

        if ws_uuid:
            # message_agent tool (agent-to-agent, F1) — uses UUID for workspace key comparison
            msg_tool, msg_async_tool = make_message_agent_tool(ws_uuid)
            tools.append(msg_tool)
            tools.append(msg_async_tool)

        if ws_uuid and ws_slug:
            # translate tools — fire-and-forget Celery tasks with SSE notification
            ws_dir = os.path.join(settings.workspaces_data_dir, ws_slug)
            translate_text_tool, translate_file_tool = make_translate_tools(ws_uuid, ws_dir)
            tools.append(translate_text_tool)
            tools.append(translate_file_tool)

    logger.info(
        f"Building agent '{config.id}' — model={llm_model}, "
        f"backend={config.backend_config.get('type', 'state') if config.backend_config else 'state'}, "
        f"skills={len(skills)}, subagents={len(config.subagents or [])}, "
        f"memory={config.memory_sources or None}, store={'yes' if store else 'no'}, "
        f"interrupt_on={bool(config.interrupt_on)}, no_tools={no_tools_mode}"
    )

    # Mode sans tools : simple graph LangGraph sans bind_tools (deepagents requiert function calling)
    if no_tools_mode:
        sys_msg = SystemMessage(content=system_prompt) if system_prompt else None

        async def _call_model(state: MessagesState):
            msgs = ([sys_msg] + state["messages"]) if sys_msg else state["messages"]
            # Trim to context window (system messages always kept)
            if context_window_size > 0:
                non_sys = [m for m in msgs if not isinstance(m, SystemMessage)]
                sys_only = [m for m in msgs if isinstance(m, SystemMessage)]
                msgs = sys_only + non_sys[-context_window_size:]
            # astream() → LangGraph émet des on_chat_model_stream events (ainvoke ne stream pas)
            chunks = [c async for c in _base_llm.astream(msgs)]
            response = chunks[0] if chunks else None
            for c in chunks[1:]:
                response = response + c
            return {"messages": [response] if response else []}

        graph = StateGraph(MessagesState)
        graph.add_node("model", _call_model)
        graph.set_entry_point("model")
        graph.add_edge("model", END)
        return graph.compile(checkpointer=checkpointer)

    # --- SubAgents ---
    subagents_cfg = config.subagents or []

    # --- Interrupt on ---
    interrupt_on = config.interrupt_on or None

    # --- Memory (AGENTS.md cross-conversation) ---
    memory = config.memory_sources if config.memory_sources else None

    # --- Backend (depuis backend_config DB, scopé au workspace si applicable) ---
    backend = _make_backend(
        config.id,
        config.backend_config or {},
        store=store,
        workspace_slug=ws_slug,
    )

    return create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        memory=memory,
        subagents=subagents_cfg if subagents_cfg else None,
        interrupt_on=interrupt_on if interrupt_on else None,
    )
