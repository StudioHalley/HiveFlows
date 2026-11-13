"""Tests — LLM Factory (Phase 5 Bloc B7)."""
import pytest
from unittest.mock import patch, MagicMock


def test_create_llm_openrouter():
    mock_llm = MagicMock()
    with patch("langchain_openai.ChatOpenAI", return_value=mock_llm) as mock_cls:
        from agents.llm_factory import create_llm
        result = create_llm("openrouter", "openai/gpt-4o-mini", api_key="sk-or-test")
        mock_cls.assert_called_once_with(
            model="openai/gpt-4o-mini",
            api_key="sk-or-test",
            base_url="https://openrouter.ai/api/v1",
        )
        assert result is mock_llm


def test_create_llm_openrouter_custom_base_url():
    mock_llm = MagicMock()
    with patch("langchain_openai.ChatOpenAI", return_value=mock_llm) as mock_cls:
        from agents.llm_factory import create_llm
        result = create_llm("openrouter", "gpt-4o", api_key="k", base_url="https://proxy.example.com/v1")
        mock_cls.assert_called_once_with(
            model="gpt-4o",
            api_key="k",
            base_url="https://proxy.example.com/v1",
        )


def test_create_llm_anthropic():
    mock_llm = MagicMock()
    with patch("langchain_anthropic.ChatAnthropic", return_value=mock_llm) as mock_cls:
        from agents.llm_factory import create_llm
        result = create_llm("anthropic", "claude-sonnet-4-20250514", api_key="sk-ant-test")
        mock_cls.assert_called_once_with(
            model="claude-sonnet-4-20250514",
            api_key="sk-ant-test",
        )
        assert result is mock_llm


def test_create_llm_google():
    mock_llm = MagicMock()
    with patch("langchain_google_genai.ChatGoogleGenerativeAI", return_value=mock_llm) as mock_cls:
        from agents.llm_factory import create_llm
        result = create_llm("google", "gemini-1.5-pro", api_key="goog-key")
        mock_cls.assert_called_once_with(
            model="gemini-1.5-pro",
            google_api_key="goog-key",
        )
        assert result is mock_llm


def test_create_llm_ollama_default_base_url():
    mock_llm = MagicMock()
    with patch("langchain_ollama.ChatOllama", return_value=mock_llm) as mock_cls:
        from agents.llm_factory import create_llm
        result = create_llm("ollama", "llama3")
        mock_cls.assert_called_once_with(
            model="llama3",
            base_url="http://ollama:11434",
        )


def test_create_llm_ollama_custom_base_url():
    mock_llm = MagicMock()
    with patch("langchain_ollama.ChatOllama", return_value=mock_llm) as mock_cls:
        from agents.llm_factory import create_llm
        create_llm("ollama", "mistral", base_url="http://192.168.1.100:11434")
        mock_cls.assert_called_once_with(
            model="mistral",
            base_url="http://192.168.1.100:11434",
        )


def test_create_llm_unknown_provider():
    from agents.llm_factory import create_llm
    with pytest.raises(ValueError, match="Provider LLM inconnu"):
        create_llm("unknown_provider", "some-model")


def test_create_llm_extra_kwargs():
    """Les kwargs (temperature, max_tokens) sont forwarded au constructeur."""
    mock_llm = MagicMock()
    with patch("langchain_anthropic.ChatAnthropic", return_value=mock_llm) as mock_cls:
        from agents.llm_factory import create_llm
        create_llm("anthropic", "claude-haiku-4-5", api_key="k", temperature=0.2, max_tokens=1000)
        mock_cls.assert_called_once_with(
            model="claude-haiku-4-5",
            api_key="k",
            temperature=0.2,
            max_tokens=1000,
        )
