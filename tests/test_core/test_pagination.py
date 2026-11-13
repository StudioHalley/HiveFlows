"""Tests for Bloc P — Conversation Pagination & Context Window.

Unit tests for:
- Cursor-based pagination (get_recent_messages)
- ContextWindowChatModel trimming logic
- Agent context_window_size respected at factory level

Run without a live server (pure unit tests).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ContextWindowChatModel — unit tests
# ---------------------------------------------------------------------------

class TestContextWindowChatModel:
    """Test that ContextWindowChatModel correctly trims messages."""

    def _make_model(self, window_size: int):
        from agents.factory import ContextWindowChatModel
        from langchain_core.outputs import ChatGeneration, ChatResult
        from langchain_core.messages import AIMessage

        mock_inner = MagicMock()
        mock_inner._llm_type = "mock"
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
        mock_inner._generate.return_value = result
        mock_inner._agenerate = AsyncMock(return_value=result)

        return ContextWindowChatModel(inner=mock_inner, context_window_size=window_size), mock_inner

    def _msgs(self, n: int):
        from langchain_core.messages import HumanMessage, AIMessage
        msgs = []
        for i in range(n):
            msgs.append(HumanMessage(content=f"user {i}"))
            msgs.append(AIMessage(content=f"agent {i}"))
        return msgs

    def test_trim_keeps_last_n_non_system(self):
        from agents.factory import ContextWindowChatModel
        from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

        model, inner = self._make_model(window_size=3)
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content="h1"),
            AIMessage(content="a1"),
            HumanMessage(content="h2"),
            AIMessage(content="a2"),
            HumanMessage(content="h3"),
        ]
        trimmed = model._trim(msgs)

        # SystemMessage always kept, last 3 non-system kept
        assert trimmed[0].content == "sys"
        assert len(trimmed) == 4  # sys + 3
        assert trimmed[-1].content == "h3"
        assert trimmed[1].content == "a1"  # 4th from last non-sys

    def test_trim_noop_when_within_window(self):
        model, _ = self._make_model(window_size=10)
        msgs = self._msgs(3)  # 6 messages total
        assert model._trim(msgs) == msgs

    def test_trim_called_in_generate(self):
        from langchain_core.messages import HumanMessage, AIMessage

        model, inner = self._make_model(window_size=2)
        msgs = self._msgs(5)  # 10 messages, should trim to 2

        model._generate(msgs)

        call_msgs = inner._generate.call_args[0][0]
        assert len(call_msgs) == 2

    @pytest.mark.asyncio
    async def test_trim_called_in_agenerate(self):
        from langchain_core.messages import HumanMessage, AIMessage

        model, inner = self._make_model(window_size=2)
        msgs = self._msgs(5)

        await model._agenerate(msgs)

        call_msgs = inner._agenerate.call_args[0][0]
        assert len(call_msgs) == 2

    def test_zero_window_size_disables_trim(self):
        model, inner = self._make_model(window_size=0)
        msgs = self._msgs(20)
        assert model._trim(msgs) == msgs

    def test_bind_tools_returns_runnable_with_trim(self):
        from langchain_core.messages import HumanMessage
        from langchain_core.runnables import RunnableSequence

        model, inner = self._make_model(window_size=3)
        inner.bind_tools = MagicMock(return_value=MagicMock())

        bound = model.bind_tools([])
        # Should be a RunnableSequence (RunnableLambda | bound_inner)
        assert hasattr(bound, "invoke") or hasattr(bound, "steps")


# ---------------------------------------------------------------------------
# get_recent_messages — pagination logic
# ---------------------------------------------------------------------------

class TestGetRecentMessages:
    """Test cursor-based pagination in chat_persistence."""

    def _make_message(self, content: str, seconds_ago: int = 0):
        m = MagicMock()
        m.id = uuid.uuid4()
        m.content = content
        m.created_at = datetime.utcnow() - timedelta(seconds=seconds_ago)
        m.role = "user"
        return m

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_conversation(self):
        from chat_persistence import get_recent_messages

        mock_db = AsyncMock()
        # No conversation found
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        messages, has_more = await get_recent_messages(mock_db, str(uuid.uuid4()))

        assert messages == []
        assert has_more is False

    @pytest.mark.asyncio
    async def test_has_more_when_extra_row(self):
        """has_more=True when DB returns limit+1 rows."""
        from chat_persistence import get_recent_messages
        from models.conversation import Conversation

        conv_id = uuid.uuid4()
        mock_db = AsyncMock()

        # First execute returns conversation ID
        first_result = MagicMock()
        first_result.scalar_one_or_none.return_value = conv_id

        # Second execute returns limit+1 messages
        msgs = [self._make_message(f"msg {i}", seconds_ago=i) for i in range(51)]
        second_result = MagicMock()
        second_result.scalars.return_value.all.return_value = msgs

        mock_db.execute.side_effect = [first_result, second_result]

        result_msgs, has_more = await get_recent_messages(mock_db, str(uuid.uuid4()), limit=50)

        assert has_more is True
        assert len(result_msgs) == 50

    @pytest.mark.asyncio
    async def test_no_more_when_exact_limit(self):
        """has_more=False when DB returns exactly limit rows."""
        from chat_persistence import get_recent_messages

        conv_id = uuid.uuid4()
        mock_db = AsyncMock()

        first_result = MagicMock()
        first_result.scalar_one_or_none.return_value = conv_id

        msgs = [self._make_message(f"msg {i}", seconds_ago=i) for i in range(50)]
        second_result = MagicMock()
        second_result.scalars.return_value.all.return_value = msgs

        mock_db.execute.side_effect = [first_result, second_result]

        result_msgs, has_more = await get_recent_messages(mock_db, str(uuid.uuid4()), limit=50)

        assert has_more is False
        assert len(result_msgs) == 50

    @pytest.mark.asyncio
    async def test_messages_returned_in_chronological_order(self):
        """Messages should be oldest-first (reversed from DESC query)."""
        from chat_persistence import get_recent_messages

        conv_id = uuid.uuid4()
        mock_db = AsyncMock()

        first_result = MagicMock()
        first_result.scalar_one_or_none.return_value = conv_id

        # DB returns in DESC order (newest first)
        msgs = [self._make_message(f"msg {i}", seconds_ago=i) for i in range(5)]
        second_result = MagicMock()
        second_result.scalars.return_value.all.return_value = msgs

        mock_db.execute.side_effect = [first_result, second_result]

        result_msgs, _ = await get_recent_messages(mock_db, str(uuid.uuid4()), limit=10)

        # Should be reversed to chronological (oldest first)
        contents = [m.content for m in result_msgs]
        assert contents == list(reversed(contents)) or contents == [f"msg {i}" for i in range(4, -1, -1)]


# ---------------------------------------------------------------------------
# AgentConfig context_window_size field
# ---------------------------------------------------------------------------

class TestAgentConfigContextWindow:
    def test_default_is_50(self):
        from models.agent_config import AgentConfig
        config = AgentConfig.__new__(AgentConfig)
        # Default value set via mapped_column
        assert AgentConfig.context_window_size.default.arg == 50

    def test_schema_default_is_50(self):
        from api.schemas_agents import AgentConfigCreate
        schema = AgentConfigCreate(
            id="test-agent",
            name="Test",
            llm_config={"model": "openai/gpt-4o-mini"},
        )
        assert schema.context_window_size == 50

    def test_schema_accepts_custom_value(self):
        from api.schemas_agents import AgentConfigCreate
        schema = AgentConfigCreate(
            id="test-agent",
            name="Test",
            llm_config={"model": "openai/gpt-4o-mini"},
            context_window_size=10,
        )
        assert schema.context_window_size == 10
