from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, Field
from datetime import datetime
import json

# --- Output standard ---

class ToolStatus(str):
    OK = "ok"
    ERROR = "error"

class ToolOutput(BaseModel):
    status: str = "ok"           # "ok" | "error"
    tool_name: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    data: dict[str, Any] = {}    # payload spécifique au tool
    error: str | None = None

    def to_llm_string(self) -> str:
        """Ce que le LLM voit comme retour du tool."""
        if self.status == "error":
            return f"ERROR: {self.error}"
        return json.dumps(self.data, ensure_ascii=False, indent=2)

# --- Base class ---

InputT = TypeVar("InputT", bound=BaseModel)

class BaseTool(ABC, Generic[InputT]):
    name: str
    description: str
    input_schema: type[BaseModel]

    @abstractmethod
    def run(self, params: InputT) -> ToolOutput:
        ...

    def run_from_dict(self, d: dict) -> ToolOutput:
        """Pour appel depuis automatismes / CLI sans LLM."""
        params = self.input_schema(**d)
        return self.run(params)

    def run_from_json(self, json_str: str) -> ToolOutput:
        params = self.input_schema.model_validate_json(json_str)
        return self.run(params)

    def as_langchain_tool(self):
        """Retourne un StructuredTool LangChain."""
        from langchain_core.tools import StructuredTool
        tool_instance = self

        def _run(**kwargs) -> str:
            params = self.input_schema(**kwargs)
            output = tool_instance.run(params)
            return output.to_llm_string()

        return StructuredTool.from_function(
            func=_run,
            name=self.name,
            description=self.description,
            args_schema=self.input_schema,
        )