import re
from datetime import datetime

from pydantic import BaseModel, field_validator

_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._@:~-]*$")


class AgentConfigCreate(BaseModel):
    id: str

    @field_validator("id")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("L'ID ne peut pas être vide.")
        if not _SLUG_RE.match(v):
            raise ValueError(
                "L'ID ne doit contenir que des caractères alphanumériques, "
                "tirets, underscores, points, @, :, ~ (pas d'espaces)."
            )
        return v
    name: str
    description: str | None = None
    llm_config: dict
    system_prompt: str | None = None
    tools: list[str] = ["run_script", "run_skill_script"]
    skills: list[str] | None = None  # None = tous les skills disponibles
    subagents: list[dict] = []
    memory_sources: list[str] = []
    interrupt_on: dict = {}
    backend_config: dict = {}
    context_window_size: int = 50


class AgentConfigUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    llm_config: dict | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    subagents: list[dict] | None = None
    memory_sources: list[str] | None = None
    interrupt_on: dict | None = None
    backend_config: dict | None = None
    context_window_size: int | None = None
    is_active: bool | None = None


class AgentConfigRead(BaseModel):
    id: str
    name: str
    description: str | None
    llm_config: dict
    system_prompt: str | None
    is_active: bool
    tools: list
    skills: list | None
    subagents: list
    memory_sources: list
    interrupt_on: dict
    backend_config: dict
    context_window_size: int
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}
