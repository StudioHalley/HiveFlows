"""
Playwright browser tool — client pour un serveur Playwright distant (Docker).

Configuration du serveur (priorité décroissante):
    1. Paramètre ws_endpoint au constructeur : PlaywrightBrowserTool(ws_endpoint="ws://host:3000")
    2. Variable d'environnement PLAYWRIGHT_WS_ENDPOINT
    3. Défaut : ws://playwright:3000

Actions disponibles:
    screenshot   — capture d'écran PNG d'une URL
    get_text     — texte visible de la page (sans balises HTML)
    get_html     — HTML brut de la page
    click        — clic sur un sélecteur CSS
    fill         — remplir un champ + optionnellement soumettre
    evaluate     — exécuter du JavaScript et retourner le résultat
"""

import base64
import os
from pathlib import Path
from typing import Literal, Any

from pydantic import BaseModel, HttpUrl, Field
from tools.base import BaseTool, ToolOutput

DEFAULT_WS_ENDPOINT = os.environ.get("PLAYWRIGHT_WS_ENDPOINT", "ws://playwright:3000")

# --- Input schema ---

class PlaywrightInput(BaseModel):
    url: HttpUrl = Field(description="URL de la page cible")
    action: Literal["screenshot", "get_text", "get_html", "click", "fill", "evaluate"] = Field(
        description=(
            "Action à effectuer : "
            "'screenshot' (PNG), "
            "'get_text' (texte visible), "
            "'get_html' (HTML brut), "
            "'click' (clic sur sélecteur), "
            "'fill' (remplir un champ), "
            "'evaluate' (exécuter JS)"
        )
    )

    # Commun — navigation
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = Field(
        default="load",
        description="Événement attendu avant d'agir sur la page",
    )
    timeout_ms: int = Field(
        default=30_000, ge=1000, le=120_000,
        description="Timeout navigation en millisecondes",
    )

    # Action: screenshot
    screenshot_path: str | None = Field(
        default=None,
        description="Chemin de sauvegarde du PNG (auto si absent). Action: screenshot",
    )
    full_page: bool = Field(
        default=False,
        description="Capturer la page entière (scroll). Action: screenshot",
    )

    # Action: click / fill
    selector: str | None = Field(
        default=None,
        description="Sélecteur CSS de l'élément cible. Actions: click, fill",
    )

    # Action: fill
    value: str | None = Field(
        default=None,
        description="Texte à saisir dans le champ. Action: fill",
    )
    submit: bool = Field(
        default=False,
        description="Appuyer sur Entrée après la saisie. Action: fill",
    )

    # Action: evaluate
    script: str | None = Field(
        default=None,
        description="Code JavaScript à exécuter (doit retourner une valeur sérialisable). Action: evaluate",
    )

    # Viewport
    viewport_width: int = Field(default=1280, ge=320, le=3840)
    viewport_height: int = Field(default=800, ge=240, le=2160)


# --- Tool ---

class PlaywrightBrowserTool(BaseTool[PlaywrightInput]):
    name = "playwright_browser"
    description = (
        "Contrôle un navigateur Chromium headless via un serveur Playwright distant. "
        "Permet de faire des screenshots, lire le contenu d'une page (texte/HTML), "
        "cliquer sur des éléments, remplir des formulaires, et exécuter du JavaScript. "
        "Idéal pour les pages qui nécessitent JavaScript pour s'afficher (SPA, dashboards)."
    )
    input_schema = PlaywrightInput

    def __init__(self, ws_endpoint: str | None = None):
        self.ws_endpoint = ws_endpoint or DEFAULT_WS_ENDPOINT

    def run(self, params: PlaywrightInput) -> ToolOutput:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ToolOutput(
                status="error",
                tool_name=self.name,
                error="Package 'playwright' manquant. Ajouter au requirements.txt.",
            )

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect(ws_endpoint=self.ws_endpoint)
                try:
                    page = browser.new_page(
                        viewport={"width": params.viewport_width, "height": params.viewport_height}
                    )
                    page.goto(
                        str(params.url),
                        wait_until=params.wait_until,
                        timeout=params.timeout_ms,
                    )

                    result = self._dispatch(page, params)
                finally:
                    browser.close()

            return ToolOutput(tool_name=self.name, data=result)

        except Exception as e:
            return ToolOutput(status="error", tool_name=self.name, error=str(e))

    # --- Actions ---

    def _dispatch(self, page: Any, params: PlaywrightInput) -> dict:
        if params.action == "screenshot":
            return self._screenshot(page, params)
        elif params.action == "get_text":
            return self._get_text(page, params)
        elif params.action == "get_html":
            return self._get_html(page, params)
        elif params.action == "click":
            return self._click(page, params)
        elif params.action == "fill":
            return self._fill(page, params)
        elif params.action == "evaluate":
            return self._evaluate(page, params)
        else:
            raise ValueError(f"Action inconnue: {params.action}")

    def _screenshot(self, page: Any, params: PlaywrightInput) -> dict:
        if params.screenshot_path:
            path = Path(params.screenshot_path)
        else:
            import hashlib
            slug = hashlib.md5(str(params.url).encode()).hexdigest()[:8]
            path = Path("/tmp/screenshots") / f"{slug}.png"

        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=params.full_page)

        # Retourner aussi le PNG en base64 pour les LLM multimodaux
        png_bytes = path.read_bytes()
        return {
            "file_path": str(path),
            "url": str(params.url),
            "size_bytes": len(png_bytes),
            "base64": base64.b64encode(png_bytes).decode(),
            "full_page": params.full_page,
        }

    def _get_text(self, page: Any, params: PlaywrightInput) -> dict:
        text = page.inner_text("body")
        return {
            "url": str(params.url),
            "text": text,
            "length": len(text),
        }

    def _get_html(self, page: Any, params: PlaywrightInput) -> dict:
        html = page.content()
        title = page.title()
        return {
            "url": str(params.url),
            "title": title,
            "html": html,
            "length": len(html),
        }

    def _click(self, page: Any, params: PlaywrightInput) -> dict:
        if not params.selector:
            raise ValueError("'selector' requis pour l'action click")
        page.click(params.selector, timeout=params.timeout_ms)
        return {
            "url": str(params.url),
            "selector": params.selector,
            "clicked": True,
            "current_url": page.url,
        }

    def _fill(self, page: Any, params: PlaywrightInput) -> dict:
        if not params.selector:
            raise ValueError("'selector' requis pour l'action fill")
        if params.value is None:
            raise ValueError("'value' requis pour l'action fill")

        page.fill(params.selector, params.value, timeout=params.timeout_ms)
        if params.submit:
            page.press(params.selector, "Enter")
            page.wait_for_load_state(params.wait_until, timeout=params.timeout_ms)

        return {
            "url": str(params.url),
            "selector": params.selector,
            "value": params.value,
            "submitted": params.submit,
            "current_url": page.url,
        }

    def _evaluate(self, page: Any, params: PlaywrightInput) -> dict:
        if not params.script:
            raise ValueError("'script' requis pour l'action evaluate")
        result = page.evaluate(params.script)
        return {
            "url": str(params.url),
            "script": params.script,
            "result": result,
        }


# --- CLI ---
# python -m tools.web.playwright_browser '{"url": "https://example.com", "action": "screenshot"}'
if __name__ == "__main__":
    import sys
    tool = PlaywrightBrowserTool()
    output = tool.run_from_json(sys.argv[1])
    print(output.model_dump_json(indent=2))
