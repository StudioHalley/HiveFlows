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