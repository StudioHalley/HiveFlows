import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.postgres import PostgresStore
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession

from agents.factory import build_agent_from_config
from config import settings
from database import async_session
from models.agent_config import AgentConfig

logger = logging.getLogger("hiveflows")

_GLOBAL = "__global__"


def _ws_key(workspace_id: str | None) -> str:
    return workspace_id if workspace_id else _GLOBAL


class AgentManager:
    """Manages agent lifecycle: checkpointer init, config registry, agent cache, invocation.

    Cache keys are (workspace_id, agent_id) tuples to support multi-workspace isolation.
    Legacy API key clients use workspace_id=None → "__global__" key.

    Deux pools PostgreSQL :
    - sync (ConnectionPool + PostgresSaver)      → invoke, resume (via asyncio.to_thread)
    - async (AsyncConnectionPool + AsyncPostgresSaver) → astream_events
    """

    def __init__(self):
        self._sync_pool: ConnectionPool | None = None
        self._sync_checkpointer: PostgresSaver | None = None
        self._async_pool: AsyncConnectionPool | None = None
        self._async_checkpointer: AsyncPostgresSaver | None = None
        self._store: PostgresStore | None = None
        # Config keys: (workspace_id_str, agent_id)
        # Agent cache keys: (workspace_id_str, agent_id, frozenset|None) — includes skills fingerprint
        self._sync_agents: dict[tuple, CompiledStateGraph] = {}
        self._async_agents: dict[tuple, CompiledStateGraph] = {}
        self._configs: dict[tuple[str, str], AgentConfig] = {}
        self._configs_by_id: dict[str, AgentConfig] = {}  # secondary index: agent_id -> config

    def setup(self):
        """Initialize sync checkpointer + store at startup (called once, sync)."""
        url = settings.database_url_psycopg

        self._sync_pool = ConnectionPool(url, kwargs={"autocommit": True})
        self._sync_checkpointer = PostgresSaver(self._sync_pool)
        self._sync_checkpointer.setup()

        self._store = PostgresStore(self._sync_pool)
        self._store.setup()

        # Async pool créé mais pas encore ouvert (lazy open à la première utilisation)
        self._async_pool = AsyncConnectionPool(url, open=False)

        logger.info("LangGraph PostgreSQL checkpointer and store initialized.")

    async def _ensure_async_checkpointer(self):
        """Ouvre le pool async et initialise l'AsyncPostgresSaver si nécessaire."""
        if self._async_checkpointer is None:
            await self._async_pool.open()
            self._async_checkpointer = AsyncPostgresSaver(self._async_pool)
            await self._async_checkpointer.setup()

    def _invalidate_agent_cache(self, ws: str, agent_id: str) -> None:
        """Remove all cached agents for (ws, agent_id) regardless of skills key."""
        for cache in (self._sync_agents, self._async_agents):
            to_remove = [k for k in cache if k[0] == ws and k[1] == agent_id]
            for k in to_remove:
                del cache[k]

    def invalidate_workspace_agents(self, workspace_id: str) -> int:
        """Remove all cached agents for a workspace (called when app_instances change).

        Returns the number of cache entries removed.
        """
        ws = _ws_key(workspace_id)
        removed = 0
        for cache in (self._sync_agents, self._async_agents):
            to_remove = [k for k in cache if k[0] == ws]
            for k in to_remove:
                del cache[k]
                removed += 1
        if removed:
            logger.info(f"Agent cache invalidated for workspace {workspace_id} ({removed} entr(ée)s supprimée(s)).")
        return removed

    def register_config(self, config: AgentConfig) -> None:
        """Register or update an agent config. Invalidates both agent caches."""
        ws = _ws_key(str(config.workspace_id) if config.workspace_id else None)
        self._configs[(ws, config.id)] = config
        self._configs_by_id[config.id] = config
        self._invalidate_agent_cache(ws, config.id)
        logger.info(f"Agent config registered: '{config.id}' (workspace={ws}, cache invalidated).")

    def deactivate(self, agent_id: str, workspace_id: str | None = None) -> None:
        """Remove an agent from the registry and both caches."""
        ws = _ws_key(workspace_id)
        self._configs.pop((ws, agent_id), None)
        self._configs_by_id.pop(agent_id, None)
        self._invalidate_agent_cache(ws, agent_id)
        logger.info(f"Agent deactivated: '{agent_id}' (workspace={ws}).")

    def _get_config(self, agent_id: str, workspace_id: str | None = None) -> AgentConfig:
        """Look up config by (workspace_id, agent_id), fallback to by-id index, then __global__."""
        ws = _ws_key(workspace_id)
        config = self._configs.get((ws, agent_id))
        if config:
            return config
        # Fallback: agent may live in another workspace but is linked to this one via workspace_agents
        config = self._configs_by_id.get(agent_id)
        if config:
            return config
        # Fallback: try __global__ (legacy agents loaded without workspace)
        if ws != _GLOBAL:
            config = self._configs.get((_GLOBAL, agent_id))
            if config:
                return config
        raise ValueError(f"Agent inconnu: '{agent_id}'. Vérifier que l'agent existe et est actif.")

    @staticmethod
    def _skills_key(allowed_skills: list[str] | None) -> frozenset[str] | None:
        return frozenset(allowed_skills) if allowed_skills is not None else None

    async def get_agent(
        self,
        agent_id: str,
        workspace_id: str | None = None,
        allowed_skills: list[str] | None = None,
        workspace_slug: str | None = None,
    ) -> CompiledStateGraph:
        """Get or build a cached sync agent (for invoke/resume)."""
        ws = _ws_key(workspace_id)
        sk = self._skills_key(allowed_skills)
        key = (ws, agent_id, sk, workspace_slug)
        if key not in self._sync_agents:
            config = self._get_config(agent_id, workspace_id)
            async with async_session() as db:
                self._sync_agents[key] = await build_agent_from_config(
                    config, self._sync_checkpointer, self._store,
                    allowed_skills=allowed_skills, workspace_slug=workspace_slug,
                    db=db,
                )
        return self._sync_agents[key]

    async def get_async_agent(
        self,
        agent_id: str,
        workspace_id: str | None = None,
        allowed_skills: list[str] | None = None,
        workspace_slug: str | None = None,
    ) -> CompiledStateGraph:
        """Get or build a cached async agent (for astream_events)."""
        await self._ensure_async_checkpointer()
        ws = _ws_key(workspace_id)
        sk = self._skills_key(allowed_skills)
        key = (ws, agent_id, sk, workspace_slug)
        if key not in self._async_agents:
            config = self._get_config(agent_id, workspace_id)
            async with async_session() as db:
                self._async_agents[key] = await build_agent_from_config(
                    config, self._async_checkpointer, self._store,
                    allowed_skills=allowed_skills, workspace_slug=workspace_slug,
                    db=db,
                )
        return self._async_agents[key]

    @staticmethod
    def _thread_id(agent_id: str, workspace_id: str | None, conversation_id: str | None) -> str:
        """Compute LangGraph thread_id. Workspace-scoped if workspace_id set."""
        if workspace_id:
            return f"{workspace_id}:{agent_id}"
        return conversation_id or str(uuid.uuid4())

    async def invoke(
        self,
        agent_id: str,
        message: str,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        allowed_skills: list[str] | None = None,
        workspace_slug: str | None = None,
    ) -> dict:
        """Send a message to an agent and return the response."""
        agent = await self.get_agent(agent_id, workspace_id, allowed_skills, workspace_slug=workspace_slug)

        thread_id = self._thread_id(agent_id, workspace_id, conversation_id)
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        _input = {"messages": [{"role": "user", "content": message}]}
        _config = {"configurable": {"thread_id": thread_id}}
        result = await asyncio.to_thread(agent.invoke, _input, _config)

        ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
        if not ai_messages:
            return {
                "conversation_id": conversation_id,
                "message_id": str(uuid.uuid4()),
                "response": "",
                "tool_calls": [],
                "usage": None,
            }

        last_msg = ai_messages[-1]

        content = last_msg.content
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )

        tool_calls = []
        for m in ai_messages:
            for tc in m.tool_calls:
                tool_calls.append({
                    "name": tc.get("name"),
                    "args": tc.get("args"),
                })

        usage = last_msg.response_metadata.get("usage") if last_msg.response_metadata else None

        return {
            "conversation_id": conversation_id,
            "message_id": last_msg.id or str(uuid.uuid4()),
            "response": content,
            "tool_calls": tool_calls,
            "usage": usage,
        }

    async def astream(
        self,
        agent_id: str,
        message: str,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        allowed_skills: list[str] | None = None,
        workspace_slug: str | None = None,
    ) -> AsyncIterator[dict]:
        """Yield SSE event dicts while streaming an agent response."""
        agent = await self.get_async_agent(agent_id, workspace_id, allowed_skills, workspace_slug=workspace_slug)

        thread_id = self._thread_id(agent_id, workspace_id, conversation_id)
        if not conversation_id:
            conversation_id = str(uuid.uuid4())

        input_data = {"messages": [{"role": "user", "content": message}]}
        config = {"configurable": {"thread_id": thread_id}}

        yield {"event": "message_start", "data": {"conversation_id": conversation_id, "agent_id": agent_id}}

        try:
            async for event in agent.astream_events(input_data, config=config, version="v2"):
                kind = event.get("event", "")

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk is None:
                        continue
                    content = chunk.content
                    if isinstance(content, str) and content:
                        yield {"event": "token", "data": {"content": content}}
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                                yield {"event": "token", "data": {"content": part["text"]}}

                elif kind == "on_tool_start":
                    args = event["data"].get("input", {})
                    if not isinstance(args, dict):
                        args = {"value": str(args)}
                    yield {"event": "tool_call", "data": {"name": event.get("name"), "args": args}}

                elif kind == "on_tool_end":
                    raw = event["data"].get("output", "")
                    output = raw.content if hasattr(raw, "content") else str(raw)
                    yield {"event": "tool_result", "data": {"name": event.get("name"), "output": output[:500]}}

        except Exception as e:
            logger.error(f"Streaming error for agent '{agent_id}': {e!r}", exc_info=True)
            yield {"event": "error", "data": {"message": str(e) or repr(e)}}
            yield {"event": "message_end", "data": {"conversation_id": conversation_id}}
            return

        # Check for HITL interrupt after stream ends
        try:
            state = await asyncio.to_thread(agent.get_state, config)
            if state.interrupts:
                interrupt_val = state.interrupts[0].value
                yield {
                    "event": "interrupt",
                    "data": {
                        "conversation_id": conversation_id,
                        "action_requests": interrupt_val.get("action_requests", []),
                        "review_configs": interrupt_val.get("review_configs", []),
                    },
                }
                return
        except Exception as e:
            logger.warning(f"Could not check interrupt state for '{agent_id}': {e}")

        yield {"event": "message_end", "data": {"conversation_id": conversation_id}}

    async def resume(
        self,
        agent_id: str,
        conversation_id: str,
        decisions: list[dict],
        workspace_id: str | None = None,
        allowed_skills: list[str] | None = None,
        workspace_slug: str | None = None,
    ) -> dict:
        """Resume a HITL-interrupted conversation."""
        from langgraph.types import Command

        agent = await self.get_agent(agent_id, workspace_id, allowed_skills, workspace_slug=workspace_slug)
        thread_id = self._thread_id(agent_id, workspace_id, conversation_id)
        config = {"configurable": {"thread_id": thread_id}}

        result = await asyncio.to_thread(
            agent.invoke,
            Command(resume={"decisions": decisions}),
            config,
        )

        ai_messages = [m for m in result.get("messages", []) if isinstance(m, AIMessage)]
        if not ai_messages:
            return {
                "conversation_id": conversation_id,
                "message_id": str(uuid.uuid4()),
                "response": "",
                "tool_calls": [],
                "usage": None,
            }

        last_msg = ai_messages[-1]

        content = last_msg.content
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )

        tool_calls = []
        for m in ai_messages:
            for tc in m.tool_calls:
                tool_calls.append({"name": tc.get("name"), "args": tc.get("args")})

        usage = last_msg.response_metadata.get("usage") if last_msg.response_metadata else None

        return {
            "conversation_id": conversation_id,
            "message_id": last_msg.id or str(uuid.uuid4()),
            "response": content,
            "tool_calls": tool_calls,
            "usage": usage,
        }


agent_manager = AgentManager()
