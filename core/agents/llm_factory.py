"""LLM Factory — crée le bon BaseChatModel selon le provider.

Usage:
    from agents.llm_factory import create_llm
    llm = create_llm("anthropic", "claude-sonnet-4-20250514", api_key="sk-ant-...")
"""
from langchain_core.language_models import BaseChatModel


def create_llm(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs,
) -> BaseChatModel:
    """Crée le bon ChatModel selon le provider.

    Args:
        provider:  'openrouter' | 'anthropic' | 'google' | 'ollama'
        model:     Identifiant du modèle (ex: 'claude-sonnet-4-20250514', 'gpt-4o')
        api_key:   Clé API (None = var d'env du provider)
        base_url:  URL de base custom (utile pour Ollama ou proxy)
        **kwargs:  Paramètres supplémentaires : temperature, max_tokens, etc.
    """
    match provider:
        case "openrouter":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url or "https://openrouter.ai/api/v1",
                **kwargs,
            )
        case "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model, api_key=api_key, **kwargs)
        case "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, **kwargs)
        case "ollama":
            from langchain_ollama import ChatOllama
            return ChatOllama(
                model=model,
                base_url=base_url or "http://ollama:11434",
                **kwargs,
            )
        case "openai_compatible":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model,
                api_key=api_key or "not-required",
                base_url=base_url,
                **kwargs,
            )
        case _:
            raise ValueError(f"Provider LLM inconnu: {provider!r}. Providers supportés: openrouter, anthropic, google, ollama, openai_compatible")
