"""
Tool de traduction de fichiers.
Lit un fichier source, traduit son contenu via TranslateTool, écrit le résultat.
Usage direct (CLI/tests) — pour le mode async Celery, voir core/agents/tools.py.
"""

import os
from pathlib import Path

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolOutput
from tools.nlp.translate import TranslateInput, TranslateTool


class TranslateFileInput(BaseModel):
    input_file: str = Field(description="Chemin vers le fichier source à traduire")
    output_file: str = Field(description="Chemin de sortie pour le fichier traduit")
    target_lang: str = Field(default="fr-fr", description="Code langue cible (ex: fr-fr, en-us)")
    max_chars: int = Field(default=5000, ge=500, le=10000, description="Taille max des chunks")
    provider: str = Field(default="openai", description="Provider: openai, anthropic, fabric")
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="URL de l'API",
    )
    model: str = Field(
        default="deepseek/deepseek-chat-v3.1",
        description="Modèle LLM à utiliser",
    )
    api_key: str | None = Field(default=None, description="API key (optionnel, utilise l'env sinon)")


class TranslateFileOutput(BaseModel):
    output_file: str
    chunks_count: int
    target_lang: str
    input_size: int
    output_size: int


class TranslateFileTool(BaseTool[TranslateFileInput]):
    name = "translate_file"
    description = (
        "Traduit un fichier texte vers une autre langue. "
        "Lit le fichier source, découpe en chunks, traduit via LLM, écrit le fichier de sortie."
    )
    input_schema = TranslateFileInput

    def run(self, params: TranslateFileInput) -> ToolOutput:
        try:
            input_path = Path(params.input_file)
            if not input_path.is_file():
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error=f"Input file not found: {params.input_file}",
                )

            text = input_path.read_text(encoding="utf-8")
            if not text.strip():
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error="Input file is empty",
                )

            # Traduire via TranslateTool
            translate_tool = TranslateTool()
            translate_params = TranslateInput(
                text=text,
                target_lang=params.target_lang,
                max_chars=params.max_chars,
                provider=params.provider,
                base_url=params.base_url,
                model=params.model,
                api_key=params.api_key,
            )
            result = translate_tool.run(translate_params)

            if result.status == "error":
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error=result.error,
                )

            translated_text = result.data["translated_text"]
            chunks_count = result.data["chunks_count"]

            # Écrire le fichier de sortie
            output_path = Path(params.output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(translated_text, encoding="utf-8")

            return ToolOutput(
                tool_name=self.name,
                data=TranslateFileOutput(
                    output_file=params.output_file,
                    chunks_count=chunks_count,
                    target_lang=params.target_lang,
                    input_size=len(text),
                    output_size=len(translated_text),
                ).model_dump(),
            )

        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))


# --- CLI usage ---

if __name__ == "__main__":
    import sys
    import json

    tool = TranslateFileTool()
    if len(sys.argv) > 1:
        result = tool.run_from_json(sys.argv[1])
    else:
        input_json = sys.stdin.read()
        result = tool.run_from_json(input_json)

    print(result.model_dump_json(indent=2))
