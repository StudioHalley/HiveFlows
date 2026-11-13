"""Human Tool Registry — tools invocables par l'humain depuis le file manager.

Chaque tool déclare :
  - son key unique
  - ses paramètres (ToolParam) avec types select/autocomplete/text/number/checkbox
  - son mode UI : "inline" (popover léger) ou "modal" (modal dédié)
  - s'il est épinglé dans le menu contextuel (pinned)
  - son groupe optionnel (ex: "fabric")
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import settings

# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class ToolParam:
    key: str
    type: str          # "select" | "autocomplete" | "text" | "number" | "checkbox"
    label: str
    options: list | None = None
    options_endpoint: str | None = None   # pour type="autocomplete"
    default: Any = None


@dataclass
class HumanTool:
    key: str
    name: str
    description: str
    icon: str
    file_extensions: list[str]   # vide = tous les fichiers
    params: list[ToolParam]
    executor: str                # "celery" | "sync"
    ui: str                      # "inline" | "modal"
    pinned: bool = True
    group: str | None = None


# ── Registry ───────────────────────────────────────────────────────────────────

_registry: dict[str, HumanTool] = {}


def register_tool(tool: HumanTool) -> None:
    _registry[tool.key] = tool


def get_tool(key: str) -> HumanTool | None:
    return _registry.get(key)


def get_available_tools(file_ext: str | None = None) -> list[HumanTool]:
    """Retourne les tools compatibles avec l'extension de fichier donnée."""
    tools = list(_registry.values())
    if file_ext:
        ext = file_ext.lower()
        if not ext.startswith("."):
            ext = "." + ext
        tools = [t for t in tools if not t.file_extensions or ext in t.file_extensions]
    return tools


# ── Execution helper ───────────────────────────────────────────────────────────

_TOOL_TO_PATTERN: dict[str, str] = {
    "fabric_extract_wisdom": "extract_wisdom",
    "fabric_summarize": "summarize",
    "fabric_extract_references": "extract_references",
}


def prepare_execution(
    tool_key: str,
    workspace_id: str,
    workspace_slug: str,
    file_path: str,
    params: dict,
    task_uuid_str: str,
) -> tuple[str, dict, str]:
    """Prépare le dispatch Celery pour un tool.

    Returns:
        (celery_task_name, celery_kwargs, task_label)
    Raises:
        ValueError si le tool est inconnu ou mal configuré.
    """
    tool = _registry.get(tool_key)
    if not tool:
        raise ValueError(f"Tool '{tool_key}' non trouvé dans le registre.")

    workspace_dir = os.path.join(settings.workspaces_data_dir, workspace_slug)
    stem = Path(file_path).stem
    suffix = Path(file_path).suffix or ".md"

    # ── translate_file ──────────────────────────────────────────────────────
    if tool_key == "translate_file":
        target_lang = params.get("target_lang", "fr-fr")
        model = params.get("model", "deepseek/deepseek-chat-v3.1")
        src_dir = str(Path(file_path).parent)
        output_file = f"{src_dir}/{stem}.{target_lang}{suffix}" if src_dir and src_dir != "." else f"{stem}.{target_lang}{suffix}"
        celery_kwargs = {
            "input_file": file_path,
            "output_file": output_file,
            "workspace_dir": workspace_dir,
            "target_lang": target_lang,
            "model": model,
            "provider": "openai",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": settings.openrouter_api_key,
            "db_task_id": task_uuid_str,
            "workspace_id": workspace_id,
        }
        task_label = f"Traduire {Path(file_path).name} → {target_lang}"
        return "tasks.run_translate", celery_kwargs, task_label

    # ── fabric tools ────────────────────────────────────────────────────────
    if tool_key.startswith("fabric_"):
        pattern = _TOOL_TO_PATTERN.get(tool_key) or params.get("pattern")
        if not pattern:
            raise ValueError(f"Pattern non spécifié pour le tool '{tool_key}'.")

        # agent UUID → proxy résout les credentials via AgentConfig.id
        model = params.get("agent", "")
        output_file = params.get("output_file") or str(
            Path(file_path).parent / f"{stem}_{pattern}{suffix}"
        )
        container_name = f"hive-app-{workspace_id}-fabric"

        celery_kwargs = {
            "pattern": pattern,
            "input_file": file_path,
            "output_file": output_file,
            "workspace_dir": workspace_dir,
            "model": model,
            "container_name": container_name,
            "container_port": 8080,
            "db_task_id": task_uuid_str,
            "workspace_id": workspace_id,
        }
        pattern_label = pattern.replace("_", " ").title()
        task_label = f"Fabric: {pattern_label} — {Path(file_path).name}"
        return "tasks.run_fabric", celery_kwargs, task_label

    # ── piper TTS ───────────────────────────────────────────────────────────
    if tool_key == "piper_speak":
        voice = params.get("voice", "fr_FR-siwis-medium")
        fmt = params.get("format", "mp3")
        speed = float(params.get("speed", 1.0))
        container_name = f"hive-app-{workspace_id}-piper"
        celery_kwargs = {
            "text_path": file_path,
            "workspace_dir": workspace_dir,
            "voice": voice,
            "format": fmt,
            "speed": speed,
            "container_name": container_name,
            "container_port": 8080,
            "db_task_id": task_uuid_str,
            "workspace_id": workspace_id,
        }
        task_label = f"TTS: {Path(file_path).name} → {voice}"
        return "tasks.run_piper", celery_kwargs, task_label

    # ── convert_file ────────────────────────────────────────────────────────
    if tool_key.startswith("convert_"):
        output_format = params.get("output_format")
        if not output_format:
            raise ValueError("output_format est requis pour convert_file.")
        output_file = params.get("output_file") or None
        celery_kwargs = {
            "input_file": file_path,
            "workspace_dir": workspace_dir,
            "output_format": output_format,
            "output_file": output_file,
            "workspace_id": workspace_id,
            "db_task_id": task_uuid_str,
        }
        task_label = f"Convertir {Path(file_path).name} → {output_format}"
        return "tasks.run_convert", celery_kwargs, task_label

    # ── whisper STT ─────────────────────────────────────────────────────────
    if tool_key == "whisper_transcribe":
        model = params.get("model", "small")
        language = params.get("language", "auto")
        fmt = params.get("format", "txt")
        container_name = f"hive-app-{workspace_id}-whisper"
        celery_kwargs = {
            "audio_path": file_path,
            "workspace_dir": workspace_dir,
            "model": model,
            "language": language,
            "format": fmt,
            "container_name": container_name,
            "container_port": 8080,
            "db_task_id": task_uuid_str,
            "workspace_id": workspace_id,
        }
        task_label = f"Transcription: {Path(file_path).name} ({model})"
        return "tasks.run_whisper", celery_kwargs, task_label

    raise ValueError(f"Aucun executor configuré pour le tool '{tool_key}'.")


# ── Tool registrations ─────────────────────────────────────────────────────────

_TEXT_EXTS = [".md", ".txt", ".html", ".json", ".csv", ".rst", ".yaml", ".yml"]
_FABRIC_EXTS = [".md", ".txt", ".rst"]
_AUDIO_EXTS = [".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus", ".webm", ".mp4"]
_LLM_MODELS = ["deepseek/deepseek-chat-v3.1", "openai/gpt-4o-mini", "openai/gpt-4o"]

# Param réutilisable : sélecteur d'agent dynamique pour les tools Fabric
_AGENT_PARAM = ToolParam(
    key="agent", type="select", label="Agent",
    options_endpoint="/tools/agents",
    default=None,
)

# D3 — translate_file
register_tool(HumanTool(
    key="translate_file",
    name="Traduire",
    description="Traduit le fichier dans la langue cible",
    icon="mdi-translate",
    file_extensions=_TEXT_EXTS,
    params=[
        ToolParam(
            key="target_lang", type="select", label="Langue cible",
            options=["fr-fr", "en-us", "es-es", "de-de", "pt-br", "it-it", "ja-jp", "zh-cn"],
            default="fr-fr",
        ),
        ToolParam(
            key="model", type="select", label="Modèle",
            options=_LLM_MODELS, default="deepseek/deepseek-chat-v3.1",
        ),
    ],
    executor="celery",
    ui="inline",
    pinned=True,
    group=None,
))

# D4 — fabric pinned tools
register_tool(HumanTool(
    key="fabric_extract_wisdom",
    name="Extract Wisdom",
    description="Extrait les idées clés, insights et recommandations",
    icon="mdi-star-four-points",
    file_extensions=_FABRIC_EXTS,
    params=[_AGENT_PARAM],
    executor="celery",
    ui="inline",
    pinned=True,
    group="fabric",
))

register_tool(HumanTool(
    key="fabric_summarize",
    name="Summarize",
    description="Résume le contenu en points essentiels",
    icon="mdi-text-short",
    file_extensions=_FABRIC_EXTS,
    params=[_AGENT_PARAM],
    executor="celery",
    ui="inline",
    pinned=True,
    group="fabric",
))

register_tool(HumanTool(
    key="fabric_extract_references",
    name="Extract References",
    description="Extrait toutes les références et citations",
    icon="mdi-format-quote-close",
    file_extensions=_FABRIC_EXTS,
    params=[_AGENT_PARAM],
    executor="celery",
    ui="inline",
    pinned=True,
    group="fabric",
))

# Piper TTS
register_tool(HumanTool(
    key="piper_speak",
    name="Synthèse vocale",
    description="Convertit ce fichier texte en audio (TTS)",
    icon="mdi-account-voice",
    file_extensions=[".md", ".txt", ".rst"],
    params=[
        ToolParam(
            key="voice", type="autocomplete", label="Voix",
            options_endpoint="/api/w/{slug}/tools/piper/voices",
            default="fr_FR-siwis-medium",
        ),
        ToolParam(
            key="format", type="select", label="Format",
            options=["mp3", "wav"],
            default="mp3",
        ),
        ToolParam(
            key="speed", type="number", label="Vitesse",
            default=1.0,
        ),
    ],
    executor="celery",
    ui="inline",
    pinned=True,
    group="audio",
))

# Whisper STT
register_tool(HumanTool(
    key="whisper_transcribe",
    name="Transcrire",
    description="Transcrit ce fichier audio en texte (STT)",
    icon="mdi-microphone-message",
    file_extensions=_AUDIO_EXTS,
    params=[
        ToolParam(
            key="model", type="select", label="Modèle",
            options=["tiny", "base", "small", "medium"],
            default="small",
        ),
        ToolParam(
            key="language", type="select", label="Langue",
            options=["auto", "fr", "en", "de", "es", "it", "pt", "ja", "zh"],
            default="auto",
        ),
        ToolParam(
            key="format", type="select", label="Format",
            options=["txt", "json", "srt", "vtt"],
            default="txt",
        ),
    ],
    executor="celery",
    ui="inline",
    pinned=True,
    group="audio",
))

# Convert file — un tool par groupe d'extensions source, avec les formats cibles valides
_CONVERT_GROUPS: list[tuple[str, list[str], list[str]]] = [
    ("pdf",      [".pdf"],            ["txt", "markdown", "html"]),
    ("markdown", [".md", ".markdown"], ["html", "txt"]),
    ("html",     [".html", ".htm"],   ["txt", "markdown", "pdf"]),
    ("docx",     [".docx"],           ["markdown"]),
    ("epub",     [".epub"],           ["txt", "markdown"]),
    ("json",     [".json"],           ["yaml", "xml"]),
    ("yaml",     [".yaml", ".yml"],   ["json", "xml"]),
    ("xml",      [".xml"],            ["json", "yaml"]),
]

for _src, _exts, _formats in _CONVERT_GROUPS:
    register_tool(HumanTool(
        key=f"convert_{_src}",
        name="Convertir",
        description=f"Convertit ce fichier .{_src} vers un autre format",
        icon="mdi-file-swap-outline",
        file_extensions=_exts,
        params=[
            ToolParam(
                key="output_format", type="select", label="Format de sortie",
                options=_formats, default=_formats[0],
            ),
            ToolParam(
                key="output_file", type="text", label="Fichier de sortie (optionnel)",
                default=None,
            ),
        ],
        executor="celery",
        ui="inline",
        pinned=True,
        group="convert",
    ))

# D5 — fabric_run générique (tous les patterns, modal)
register_tool(HumanTool(
    key="fabric_run",
    name="Fabric…",
    description="Exécute n'importe quel pattern Fabric (~200 patterns)",
    icon="mdi-flash",
    file_extensions=_FABRIC_EXTS,
    params=[
        ToolParam(
            key="pattern", type="autocomplete", label="Pattern",
            options_endpoint="/api/w/{slug}/tools/fabric/patterns",
            default=None,
        ),
        ToolParam(
            key="output_file", type="text", label="Fichier de sortie",
            default=None,
        ),
    ],
    executor="celery",
    ui="modal",
    pinned=False,
    group="fabric",
))
