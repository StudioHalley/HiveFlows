import requests
import hashlib
import re
from pathlib import Path
from pydantic import BaseModel, HttpUrl, Field
from tools.base import BaseTool, ToolOutput
import trafilatura

# --- Input schema ---

class URL2MarkdownInput(BaseModel):
    url: HttpUrl = Field(description="URL de la page à convertir en Markdown")
    output_dir: str = Field(default="/tmp/markdown", description="Dossier de destination")
    filename: str | None = Field(default=None, description="Nom du fichier .md (auto si absent)")
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    include_links: bool = Field(default=True, description="Inclure les liens dans le Markdown")
    include_images: bool = Field(default=True, description="Inclure les images dans le Markdown")

# --- Tool ---

class URL2MarkdownTool(BaseTool[URL2MarkdownInput]):
    name = "url2markdown"
    description = (
        "Extrait le contenu principal d'une page web (article, sans menus ni navigation) "
        "et le sauvegarde en fichier Markdown."
    )
    input_schema = URL2MarkdownInput

    def run(self, params: URL2MarkdownInput) -> ToolOutput:
        try:
            output_dir = Path(params.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Télécharger la page (fallback verify=False si SSL échoue)
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            }
            try:
                response = requests.get(
                    str(params.url),
                    headers=headers,
                    timeout=params.timeout_seconds,
                    allow_redirects=True,
                )
                response.raise_for_status()
            except requests.exceptions.SSLError:
                response = requests.get(
                    str(params.url),
                    headers=headers,
                    timeout=params.timeout_seconds,
                    allow_redirects=True,
                    verify=False,
                )
                response.raise_for_status()

            html = response.text

            # Extraire le contenu principal en Markdown via trafilatura
            markdown_content = trafilatura.extract(
                html,
                output_format="markdown",
                include_links=params.include_links,
                include_images=params.include_images,
                include_tables=True,
                favor_precision=True,
                url=str(params.url),
            )

            if not markdown_content:
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error=f"Aucun contenu principal extrait de {params.url}",
                )

            # Extraire le titre via trafilatura metadata
            metadata = trafilatura.extract_metadata(html, default_url=str(params.url))
            title = metadata.title if metadata and metadata.title else None

            # Ajouter le titre en en-tête si disponible
            if title:
                markdown_content = f"# {title}\n\n{markdown_content}"

            # Nom de fichier auto basé sur le titre ou l'URL
            if params.filename:
                filename = params.filename
            elif title:
                # Slugifier le titre pour en faire un nom de fichier
                slug = re.sub(r'[^\w\s-]', '', title.lower())
                slug = re.sub(r'[\s_]+', '-', slug).strip('-')
                filename = slug[:80] + ".md"
            else:
                filename = hashlib.md5(str(params.url).encode()).hexdigest()[:8] + ".md"

            file_path = output_dir / filename
            file_path.write_text(markdown_content, encoding="utf-8")

            return ToolOutput(
                tool_name=self.name,
                data={
                    "file_path": str(file_path),
                    "url": str(params.url),
                    "title": title,
                    "size_bytes": len(markdown_content.encode("utf-8")),
                    "filename": filename,
                    "content": markdown_content,
                },
            )

        except requests.exceptions.HTTPError as e:
            return ToolOutput(
                status="error",
                tool_name=self.name,
                error=f"HTTP {e.response.status_code} pour {params.url}",
            )
        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))


# --- CLI ---
# python -m tools.web.url2markdown '{"url": "https://example.com"}'
if __name__ == "__main__":
    import sys
    tool = URL2MarkdownTool()
    result = tool.run_from_json(sys.argv[1])
    print(result.model_dump_json(indent=2))
