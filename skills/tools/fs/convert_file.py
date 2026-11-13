"""
convert_file.py — Convertisseur de formats de fichiers

Conversions supportées :
  pdf      → txt | markdown | html
  markdown → html | txt
  html     → txt | markdown | pdf
  docx     → markdown
  epub     → txt | markdown
  json     ↔ yaml ↔ xml
"""
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolOutput

# ---------------------------------------------------------------------------
# Mapping : extension source → formats de sortie acceptés
# ---------------------------------------------------------------------------

SUPPORTED: dict[str, list[str]] = {
    "pdf":      ["txt", "markdown", "html"],
    "md":       ["html", "txt"],
    "markdown": ["html", "txt"],
    "html":     ["txt", "markdown", "pdf"],
    "htm":      ["txt", "markdown", "pdf"],
    "docx":     ["markdown"],
    "epub":     ["txt", "markdown"],
    "json":     ["yaml", "xml"],
    "yaml":     ["json", "xml"],
    "yml":      ["json", "xml"],
    "xml":      ["json", "yaml"],
}

OUTPUT_EXT: dict[str, str] = {
    "txt":      ".txt",
    "markdown": ".md",
    "html":     ".html",
    "pdf":      ".pdf",
    "json":     ".json",
    "yaml":     ".yaml",
    "xml":      ".xml",
}

OutputFormat = Literal["txt", "markdown", "html", "pdf", "json", "yaml", "xml"]


# ---------------------------------------------------------------------------
# Input schema
# ---------------------------------------------------------------------------

class ConvertFileInput(BaseModel):
    input_path: str = Field(description="Chemin absolu vers le fichier source")
    output_format: OutputFormat = Field(
        description=(
            "Format de sortie désiré. "
            "Valeurs: txt | markdown | html | pdf | json | yaml | xml"
        )
    )
    output_path: str | None = Field(
        default=None,
        description="Chemin de sortie (auto-généré dans /tmp/converted/ si absent)",
    )


# ---------------------------------------------------------------------------
# Convertisseurs
# ---------------------------------------------------------------------------

def _auto_output_path(input_path: Path, output_format: str) -> Path:
    out_dir = input_path.parent
    stem = input_path.stem
    ext = OUTPUT_EXT[output_format]
    candidate = out_dir / (stem + ext)
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = out_dir / f"{stem}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


# --- PDF ---

def _pdf_to_txt(src: Path) -> str:
    from pdfminer.high_level import extract_text
    return extract_text(str(src))


def _pdf_to_html(src: Path) -> str:
    from io import StringIO
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    buf = StringIO()
    with open(src, "rb") as f:
        extract_text_to_fp(f, buf, output_type="html", laparams=LAParams())
    return buf.getvalue()


def _pdf_to_markdown(src: Path) -> str:
    # pdfminer n'a pas de sortie native markdown ; on extrait le texte brut.
    # Le texte brut est un sous-ensemble valide de Markdown.
    text = _pdf_to_txt(src)
    return text


# --- Markdown ---

def _md_to_html(src: Path) -> str:
    import markdown as md_lib
    text = src.read_text(encoding="utf-8")
    body = md_lib.markdown(text, extensions=["tables", "fenced_code"])
    return (
        "<!DOCTYPE html>\n<html><head>"
        '<meta charset="utf-8"/></head>'
        f"<body>\n{body}\n</body></html>"
    )


def _md_to_txt(src: Path) -> str:
    from bs4 import BeautifulSoup
    import markdown as md_lib
    text = src.read_text(encoding="utf-8")
    html = md_lib.markdown(text)
    return BeautifulSoup(html, "html.parser").get_text(separator="\n")


# --- HTML ---

def _html_to_txt(src: Path) -> str:
    from bs4 import BeautifulSoup
    html = src.read_text(encoding="utf-8")
    return BeautifulSoup(html, "html.parser").get_text(separator="\n")


def _html_to_markdown(src: Path) -> str:
    import markdownify
    html = src.read_text(encoding="utf-8")
    return markdownify.markdownify(html, heading_style="ATX")


def _html_to_pdf(src: Path, dest: Path) -> None:
    import weasyprint
    weasyprint.HTML(filename=str(src)).write_pdf(str(dest))


# --- DOCX ---

def _docx_to_markdown(src: Path) -> str:
    import mammoth
    with open(src, "rb") as f:
        result = mammoth.convert_to_markdown(f)
    return result.value


# --- EPUB ---

def _epub_to_html_chunks(src: Path) -> list[str]:
    import ebooklib
    from ebooklib import epub
    book = epub.read_epub(str(src))
    chunks = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        content = item.get_content()
        if content:
            chunks.append(content.decode("utf-8", errors="replace"))
    return chunks


def _epub_to_txt(src: Path) -> str:
    from bs4 import BeautifulSoup
    chunks = _epub_to_html_chunks(src)
    parts = []
    for html in chunks:
        parts.append(BeautifulSoup(html, "html.parser").get_text(separator="\n"))
    return "\n\n".join(parts)


def _epub_to_markdown(src: Path) -> str:
    import markdownify
    chunks = _epub_to_html_chunks(src)
    parts = []
    for html in chunks:
        parts.append(markdownify.markdownify(html, heading_style="ATX"))
    return "\n\n---\n\n".join(parts)


# --- Données structurées ---

def _load_json(src: Path) -> dict:
    return json.loads(src.read_text(encoding="utf-8"))


def _load_yaml(src: Path) -> dict:
    import yaml
    return yaml.safe_load(src.read_text(encoding="utf-8"))


def _load_xml(src: Path) -> dict:
    import xmltodict
    return xmltodict.parse(src.read_text(encoding="utf-8"))


def _dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _dump_yaml(data: dict) -> str:
    import yaml
    return yaml.dump(data, allow_unicode=True, default_flow_style=False)


def _dump_xml(data: dict) -> str:
    import xmltodict
    return xmltodict.unparse(data, pretty=True)


DATA_LOADERS = {"json": _load_json, "yaml": _load_yaml, "yml": _load_yaml, "xml": _load_xml}
DATA_DUMPERS = {"json": _dump_json, "yaml": _dump_yaml, "xml": _dump_xml}


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ConvertFileTool(BaseTool[ConvertFileInput]):
    name = "convert_file"
    description = (
        "Convertit un fichier d'un format à un autre. "
        "Formats source supportés : pdf, markdown (.md), html, docx, epub, json, yaml, xml. "
        "Formats cible : txt, markdown, html, pdf, json, yaml, xml. "
        "Retourne le chemin du fichier converti."
    )
    input_schema = ConvertFileInput

    def run(self, params: ConvertFileInput) -> ToolOutput:
        try:
            src = Path(params.input_path)
            if not src.exists():
                return ToolOutput(
                    status="error", tool_name=self.name,
                    error=f"Fichier introuvable : {src}",
                )

            src_ext = src.suffix.lstrip(".").lower()
            fmt = params.output_format

            # Vérifier que la conversion est supportée
            allowed = SUPPORTED.get(src_ext, [])
            if fmt not in allowed:
                supported_str = ", ".join(allowed) if allowed else "aucun"
                return ToolOutput(
                    status="error", tool_name=self.name,
                    error=(
                        f"Conversion '{src_ext}' → '{fmt}' non supportée. "
                        f"Formats valides pour .{src_ext} : {supported_str}"
                    ),
                )

            dest = Path(params.output_path) if params.output_path else _auto_output_path(src, fmt)
            dest.parent.mkdir(parents=True, exist_ok=True)

            # --- Dispatch ---

            # PDF
            if src_ext == "pdf" and fmt == "txt":
                dest.write_text(_pdf_to_txt(src), encoding="utf-8")
            elif src_ext == "pdf" and fmt == "markdown":
                dest.write_text(_pdf_to_markdown(src), encoding="utf-8")
            elif src_ext == "pdf" and fmt == "html":
                dest.write_text(_pdf_to_html(src), encoding="utf-8")

            # Markdown
            elif src_ext in ("md", "markdown") and fmt == "html":
                dest.write_text(_md_to_html(src), encoding="utf-8")
            elif src_ext in ("md", "markdown") and fmt == "txt":
                dest.write_text(_md_to_txt(src), encoding="utf-8")

            # HTML
            elif src_ext in ("html", "htm") and fmt == "txt":
                dest.write_text(_html_to_txt(src), encoding="utf-8")
            elif src_ext in ("html", "htm") and fmt == "markdown":
                dest.write_text(_html_to_markdown(src), encoding="utf-8")
            elif src_ext in ("html", "htm") and fmt == "pdf":
                _html_to_pdf(src, dest)

            # DOCX
            elif src_ext == "docx" and fmt == "markdown":
                dest.write_text(_docx_to_markdown(src), encoding="utf-8")

            # EPUB
            elif src_ext == "epub" and fmt == "txt":
                dest.write_text(_epub_to_txt(src), encoding="utf-8")
            elif src_ext == "epub" and fmt == "markdown":
                dest.write_text(_epub_to_markdown(src), encoding="utf-8")

            # Données structurées
            elif src_ext in DATA_LOADERS and fmt in DATA_DUMPERS:
                data = DATA_LOADERS[src_ext](src)
                dest.write_text(DATA_DUMPERS[fmt](data), encoding="utf-8")

            else:
                return ToolOutput(
                    status="error", tool_name=self.name,
                    error=f"Combinaison non implémentée : {src_ext} → {fmt}",
                )

            size = dest.stat().st_size
            return ToolOutput(
                tool_name=self.name,
                data={
                    "input_path":    str(src),
                    "output_path":   str(dest),
                    "input_format":  src_ext,
                    "output_format": fmt,
                    "size_bytes":    size,
                },
            )

        except ImportError as e:
            pkg = str(e).split("'")[-2] if "'" in str(e) else str(e)
            return ToolOutput(
                status="error", tool_name=self.name,
                error=f"Package manquant : {pkg}. Installez-le dans skills/requirements.txt",
            )
        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))


# ---------------------------------------------------------------------------
# CLI : python -m tools.fs.convert_file '{"input_path": "...", "output_format": "txt"}'
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tool = ConvertFileTool()
    result = tool.run_from_json(sys.argv[1])
    print(result.model_dump_json(indent=2))
