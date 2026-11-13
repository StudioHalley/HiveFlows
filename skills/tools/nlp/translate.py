"""
Tool de traduction de textes longs.
Découpe le texte en morceaux de taille raisonnable et les traduit.
Compatible avec Fabric (via stdin) ou tout autre outil de traduction.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from tools.base import BaseTool, ToolOutput


# --- Input schema ---

class TranslateInput(BaseModel):
    text: str = Field(description="Texte à traduire")
    target_lang: str = Field(default="fr-fr", description="Code langue cible (ex: fr-fr, en-us, es-es)")
    max_chars: int = Field(default=5000, ge=500, le=10000, description="Taille max des morceaux en caractères")
    provider: Literal["openai", "anthropic", "fabric"] = Field(
        default="openai",
        description="Outil de traduction à utiliser (openai=OpenRouter par défaut)"
    )
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="URL de l'API (défaut: OpenRouter)"
    )
    model: str = Field(
        default="deepseek/deepseek-chat-v3.1",
        description="Modèle à utiliser (défaut: DeepSeek V3 via OpenRouter)"
    )
    api_key: str | None = Field(
        default=None,
        description="API key (si None, utilise OPENROUTER_API_KEY ou OPENAI_API_KEY)"
    )


# --- Output model ---

class TranslateOutput(BaseModel):
    translated_text: str = Field(description="Texte traduit complet")
    chunks_count: int = Field(description="Nombre de morceaux traduits")
    target_lang: str = Field(description="Langue cible utilisée")


# --- Tool ---

class TranslateTool(BaseTool[TranslateInput]):
    name = "translate"
    description = (
        "Traduit un texte long en le découpant en morceaux de taille raisonnable. "
        "Par défaut: OpenRouter avec DeepSeek V3. Supporte aussi Anthropic et Fabric."
    )
    input_schema = TranslateInput

    def _split_into_chunks(self, text: str, max_chars: int) -> list[str]:
        """
        Découpe le texte en morceaux en respectant les paragraphes et phrases.
        Algorithme inspiré du script PowerShell translate-longtext.ps1.
        """
        # Séparation en paragraphes (lignes vides)
        paragraphs = re.split(r'(\r?\n){2,}', text)
        
        chunks = []
        current = ""
        
        def add_chunk(content: str) -> None:
            if content.strip():
                chunks.append(content.strip())
        
        for para in paragraphs:
            # Si on peut ajouter le paragraphe au morceau actuel
            if (len(current) + len(para)) <= max_chars:
                current += para + "\n\n"
                continue
            
            # Si le paragraphe est trop gros → découpe par phrases
            if len(para) > max_chars:
                add_chunk(current)
                current = ""
                
                # Découpe par phrases (fin de phrase suivie d'espace)
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if (len(current) + len(sentence)) <= max_chars:
                        current += sentence + " "
                    else:
                        add_chunk(current)
                        current = sentence + " "
                current += "\n\n"
            else:
                add_chunk(current)
                current = para + "\n\n"
        
        add_chunk(current)
        return chunks

    def _translate_with_fabric(self, chunk: str, target_lang: str) -> str:
        """
        Traduit un morceau avec Fabric via stdin.
        Équivalent à: echo chunk | fabric -p translate -v "lang_code:$target_lang"
        """
        try:
            cmd = ["fabric", "-p", "translate", "-v", f"lang_code:{target_lang}"]
            
            result = subprocess.run(
                cmd,
                input=chunk,
                capture_output=True,
                text=True,
                encoding='utf-8',
                check=True
            )
            return result.stdout.strip()
            
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Erreur Fabric: {e.stderr}")
        except FileNotFoundError:
            raise RuntimeError("Fabric n'est pas installé ou n'est pas dans le PATH")

    def _translate_with_openai(self, params: TranslateInput, chunk: str) -> str:
        """
        Traduit un morceau avec l'API OpenAI/OpenRouter.
        Par défaut: OpenRouter avec DeepSeek V3.
        """
        try:
            import openai
            
            # Récupération de l'API key (priorité: params.api_key > env > erreur)
            api_key = params.api_key
            if not api_key:
                api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
            
            if not api_key:
                raise RuntimeError(
                    "API key manquante. Définissez OPENROUTER_API_KEY ou OPENAI_API_KEY "
                    "en variable d'environnement, ou passez api_key dans les paramètres."
                )
            
            client = openai.OpenAI(
                api_key=api_key,
                base_url=params.base_url
            )
            
            response = client.chat.completions.create(
                model=params.model,
                messages=[
                    {
                        "role": "system",
                        "content": f"Tu es un traducteur professionnel. Traduis le texte suivant en {params.target_lang}. "
                                   f"Conserve le formatage et la structure. Ne traduis que le contenu, pas les balises ou code."
                    },
                    {
                        "role": "user",
                        "content": chunk
                    }
                ],
                temperature=0.3
            )
            
            return response.choices[0].message.content.strip()
            
        except ImportError:
            raise RuntimeError("Le package 'openai' n'est pas installé. Installez-le avec: pip install openai")
        except Exception as e:
            raise RuntimeError(f"Erreur OpenAI/OpenRouter: {str(e)}")

    def _translate_with_anthropic(self, params: TranslateInput, chunk: str) -> str:
        """
        Traduit un morceau avec l'API Anthropic (Claude).
        """
        try:
            import anthropic
            
            # Récupération de l'API key (priorité: params.api_key > env > erreur)
            api_key = params.api_key
            if not api_key:
                api_key = os.environ.get("ANTHROPIC_API_KEY")
            
            if not api_key:
                raise RuntimeError(
                    "API key manquante. Définissez ANTHROPIC_API_KEY "
                    "en variable d'environnement, ou passez api_key dans les paramètres."
                )
            
            # Pour Anthropic, on utilise un modèle par défaut spécifique si le modèle
            # ressemble à un modèle OpenRouter
            model = params.model
            if "deepseek" in model or "/" in model:
                model = "claude-3-5-sonnet-20241022"
            
            client = anthropic.Anthropic(api_key=api_key)
            
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                temperature=0.3,
                system=f"Tu es un traducteur professionnel. Traduis le texte suivant en {params.target_lang}. "
                       f"Conserve le formatage et la structure. Ne traduis que le contenu, pas les balises ou code.",
                messages=[
                    {
                        "role": "user",
                        "content": chunk
                    }
                ]
            )
            
            return response.content[0].text.strip()
            
        except ImportError:
            raise RuntimeError("Le package 'anthropic' n'est pas installé. Installez-le avec: pip install anthropic")
        except Exception as e:
            raise RuntimeError(f"Erreur Anthropic: {str(e)}")

    def run(self, params: TranslateInput) -> ToolOutput:
        try:
            # 1. Découpage en morceaux
            chunks = self._split_into_chunks(params.text, params.max_chars)
            
            if not chunks:
                return ToolOutput(
                    status="error",
                    tool_name=self.name,
                    error="Aucun contenu à traduire (texte vide ou uniquement des espaces)"
                )
            
            # 2. Traduction de chaque morceau selon le provider
            translated_chunks = []
            
            for i, chunk in enumerate(chunks, 1):
                try:
                    if params.provider == "fabric":
                        translated = self._translate_with_fabric(chunk, params.target_lang)
                    elif params.provider == "openai":
                        translated = self._translate_with_openai(params, chunk)
                    elif params.provider == "anthropic":
                        translated = self._translate_with_anthropic(params, chunk)
                    else:
                        raise ValueError(f"Provider non supporté: {params.provider}")
                    
                    translated_chunks.append(translated)
                except Exception as e:
                    return ToolOutput(
                        status="error",
                        tool_name=self.name,
                        error=f"Erreur lors de la traduction du morceau {i}/{len(chunks)}: {str(e)}"
                    )
            
            # 3. Assemblage du résultat final
            final_text = "\n\n".join(translated_chunks)
            
            output_data = TranslateOutput(
                translated_text=final_text,
                chunks_count=len(chunks),
                target_lang=params.target_lang
            )
            
            return ToolOutput(
                tool_name=self.name,
                data=output_data.model_dump()
            )
            
        except Exception as e:
            return ToolOutput(
                status="error",
                tool_name=self.name,
                error=str(e)
            )


# --- CLI usage ---

if __name__ == "__main__":
    import sys
    
    tool = TranslateTool()
    
    # Usage: python -m tools.nlp.translate '{"text": "Hello world", "target_lang": "fr-fr"}'
    if len(sys.argv) > 1:
        result = tool.run_from_json(sys.argv[1])
    else:
        # Lecture depuis stdin
        import json
        input_json = sys.stdin.read()
        result = tool.run_from_json(input_json)
    
    print(result.model_dump_json(indent=2))
