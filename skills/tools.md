 **Pydantic + LangChain's `StructuredTool`**
 avantage d'être compatible avec l'écosystème open source tout en restant utilisable en dehors d'un LLM.

## Le standard de facto : Pydantic schemas + StructuredTool

Le cœur de l'approche :
- **Input** : un `BaseModel` Pydantic (génère automatiquement un JSON Schema pour les LLM)
- **Output** : un autre `BaseModel` standardisé (résultat structuré, sérialisable)
- **Le tool lui-même** : une fonction pure qui accepte l'input model et retourne l'output model

Voici une proposition concrète :

### Structure de projet

```
tools/
├── base.py          # Contrats de base (ToolInput, ToolOutput, BaseTool)
├── web/
│   ├── fetch_url.py
│   └── screenshot.py
├── fs/
│   └── write_file.py
└── __init__.py
```

### `tools/base.py` — le contrat commun

```python
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
        from langchain.tools import StructuredTool
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
```

### `tools/web/fetch_url.py` — exemple concret

```python
import httpx
import hashlib
from pathlib import Path
from pydantic import BaseModel, HttpUrl, Field
from tools.base import BaseTool, ToolOutput

# --- Input schema ---

class FetchUrlInput(BaseModel):
    url: HttpUrl
    output_dir: str = Field(default="/tmp/fetched", description="Dossier de destination")
    filename: str | None = Field(default=None, description="Nom de fichier (auto si absent)")
    timeout_seconds: int = Field(default=30, ge=1, le=120)

# --- Tool ---

class FetchUrlTool(BaseTool[FetchUrlInput]):
    name = "fetch_url"
    description = (
        "Télécharge le contenu d'une URL et le sauvegarde sur disque. "
        "Retourne le chemin du fichier écrit."
    )
    input_schema = FetchUrlInput

    def run(self, params: FetchUrlInput) -> ToolOutput:
        try:
            output_dir = Path(params.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            with httpx.Client(timeout=params.timeout_seconds) as client:
                response = client.get(str(params.url), follow_redirects=True)
                response.raise_for_status()

            # Nom de fichier auto basé sur l'URL
            filename = params.filename or (
                hashlib.md5(str(params.url).encode()).hexdigest()[:8] + ".html"
            )
            file_path = output_dir / filename
            file_path.write_bytes(response.content)

            return ToolOutput(
                tool_name=self.name,
                data={
                    "file_path": str(file_path),
                    "url": str(params.url),
                    "size_bytes": len(response.content),
                    "content_type": response.headers.get("content-type", "unknown"),
                    "status_code": response.status_code,
                }
            )

        except httpx.HTTPStatusError as e:
            return ToolOutput(
                status="error",
                tool_name=self.name,
                error=f"HTTP {e.response.status_code} pour {params.url}"
            )
        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))
```

### Utilisation — les 3 modes

```python
tool = FetchUrlTool()

# --- 1. Appel direct (automatisme / script) ---
output = tool.run_from_dict({
    "url": "https://example.com",
    "output_dir": "/tmp/pages"
})
print(output.data["file_path"])   # /tmp/pages/3f2a1b4c.html

# --- 2. Depuis JSON string (ex: webhook, message queue) ---
output = tool.run_from_json('{"url": "https://example.com"}')

# --- 3. Intégré à LangChain ---
lc_tool = tool.as_langchain_tool()
agent = create_react_agent(llm, tools=[lc_tool], ...)
```

### `__main__` pour usage CLI

```python
# python -m tools.web.fetch_url '{"url": "https://example.com"}'
if __name__ == "__main__":
    import sys
    tool = FetchUrlTool()
    result = tool.run_from_json(sys.argv[1])
    print(result.model_dump_json(indent=2))
```

---

## Pourquoi cette approche est "standard"

| Aspect | Choix | Pourquoi |
|---|---|---|
| **Schéma input** | Pydantic `BaseModel` | Génère JSON Schema automatiquement pour les LLM (OpenAI, Anthropic, LangChain) |
| **Output** | `ToolOutput` uniforme | Un agent peut toujours parser `status` + `data` sans connaître le tool |
| **Compatibilité LLM** | `StructuredTool.from_function` | Compatible LangChain, LangGraph, et indirectement OpenAI function calling |
| **Open source** | Pattern utilisé dans LangChain Hub, AutoGPT, CrewAI | Facilite la réutilisation |

Le point clé : **Pydantic est le pivot**. Il valide les inputs, génère le JSON Schema que les LLM utilisent pour structurer leurs appels, et documente les tools gratuitement via les `Field(description=...)`.