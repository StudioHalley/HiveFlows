import logging
import warnings

from pydantic_settings import BaseSettings

_log = logging.getLogger("hiveflows.config")

_INSECURE_JWT_DEFAULT = "change-me-to-a-random-64-char-string"


class Settings(BaseSettings):
    # LLM — OpenRouter
    openrouter_api_key: str = ""
    default_llm_provider: str = "openrouter"
    default_llm_model: str = "openai/gpt-4o-mini"

    # Credential Vault (Phase 5)
    credential_encryption_key: str = ""  # Fernet key — générer avec Fernet.generate_key()

    # Auth — API keys (rétro-compat)
    api_keys: str = ""  # comma-separated

    # LLM Proxy (API Server par workspace) — réseaux depuis lesquels le token
    # workspace_id est accepté tel quel (apps internes). Hors de ces réseaux,
    # une clé API du workspace (hf_...) est exigée.
    llm_proxy_trusted_networks: str = "172.16.0.0/12,127.0.0.0/8"

    # Auth — JWT (Phase 4)
    jwt_secret_key: str = "change-me-to-a-random-64-char-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # OAuth2 (Phase 4 — Bloc H) — leave empty to disable
    oauth_google_client_id: str = ""
    oauth_google_client_secret: str = ""
    oauth_github_client_id: str = ""
    oauth_github_client_secret: str = ""
    oauth_redirect_base_url: str = "http://localhost:8002"

    # PostgreSQL
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "hiveflows"
    postgres_user: str = "hiveflows"
    postgres_password: str = "changeme"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Celery
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # Core
    core_host: str = "0.0.0.0"
    core_port: int = 8000
    log_level: str = "INFO"

    # Workspace filesystem
    workspaces_data_dir: str = "/workspace"

    # Beekeeper (Phase 6)
    beekeeper_url: str = "http://beekeeper:8010"
    beekeeper_api_key: str = "change-me-beekeeper-key"

    # Portal (Phase Portal)
    portal_max_body_bytes: int = 10 * 1024 * 1024  # 10 MB

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_psycopg(self) -> str:
        """Connection string for langgraph-checkpoint-postgres (psycopg driver)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def api_keys_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def oauth_google_enabled(self) -> bool:
        return bool(self.oauth_google_client_id and self.oauth_google_client_secret)

    @property
    def oauth_github_enabled(self) -> bool:
        return bool(self.oauth_github_client_id and self.oauth_github_client_secret)

    @property
    def is_production(self) -> bool:
        """True when not obviously a dev/test environment."""
        return self.log_level.upper() != "DEBUG" and "localhost" not in self.oauth_redirect_base_url

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# Warn about insecure defaults at import time
if settings.jwt_secret_key == _INSECURE_JWT_DEFAULT:
    warnings.warn(
        "JWT_SECRET_KEY is set to the default value. "
        "Set a strong random secret in .env before deploying to production.",
        stacklevel=1,
    )
    _log.warning("JWT_SECRET_KEY is using the default insecure value!")
