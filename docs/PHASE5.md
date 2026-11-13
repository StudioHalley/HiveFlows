# HiveFlows — Phase 5 : Multi-Provider LLM & Credential Vault

> Objectif : permettre a chaque agent d'utiliser un provider LLM different (OpenRouter, Anthropic, Google, Ollama) avec des credentials chiffrees et une resolution automatique.

---

## 1. Perimetre

### Dans le scope
- LLM Factory : `create_llm(provider, model, api_key)` retourne le bon `BaseChatModel`
- Coffre-fort de credentials (chiffrement Fernet en DB)
- Configuration provider par agent (avec fallback workspace → global → env)
- Dashboard : page de gestion des providers
- Retro-compatibilite avec OPENROUTER_API_KEY existant

### Hors scope
- Channels de messagerie (Phase 8)
- Beekeeper / Apps / Docker control (Phase 6)
- Monitoring / Observabilite (Phase 9)

### Pourquoi c'est fondamental maintenant
Les Bees (Phase 7) vont chainer des skills et des apps. Certains agents auront besoin de modeles rapides et pas chers (GPT-4o-mini), d'autres de modeles puissants (Claude Opus). Un Bee de traduction n'a pas besoin du meme modele qu'un Bee de raisonnement. Sans multi-provider, toute la plateforme est verrouilee sur un seul backend LLM.

---

## 2. Decisions Techniques

| Decision | Choix | Justification |
|----------|-------|---------------|
| Factory | `create_llm()` avec match/case sur provider | Un seul point d'entree, extensible |
| Providers Phase 5 | OpenRouter (existant), Anthropic, Google, Ollama | LangChain a des packages pour chacun |
| Credentials | Table `provider_credentials` chiffree Fernet | Pas de cles en clair en DB |
| Cle de chiffrement | Env var `CREDENTIAL_ENCRYPTION_KEY` | Une seule cle maitre, rotation possible |
| Resolution | Agent → Workspace → Global → .env | Cascade claire, retro-compatible |
| Fallback | Optionnel, champ `fallback_llm_config` | Si provider down, essaye le suivant |

---

## 3. Schema de Donnees

### Nouvelle table `provider_credentials`

```sql
CREATE TABLE provider_credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID REFERENCES workspaces(id) ON DELETE CASCADE,  -- NULL = global (site-admin)
    provider        VARCHAR(50) NOT NULL,        -- 'openrouter', 'anthropic', 'google', 'ollama'
    display_name    VARCHAR(200),                -- "Ma cle Anthropic prod"
    credential_data TEXT NOT NULL,               -- JSON chiffre (api_key, base_url, project_id, etc.)
    is_default      BOOLEAN DEFAULT false,       -- cle par defaut pour ce provider dans le workspace
    created_by      UUID REFERENCES users(id) NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(workspace_id, provider, is_default)
);
```

### Modification table `agent_configs`

```sql
ALTER TABLE agent_configs
    ADD COLUMN llm_provider VARCHAR(50),                   -- 'openrouter', 'anthropic', 'google', 'ollama'
    ADD COLUMN llm_model VARCHAR(200),                     -- 'claude-sonnet-4-20250514', 'gpt-4o', etc.
    ADD COLUMN llm_config JSONB DEFAULT '{}',              -- temperature, max_tokens, etc.
    ADD COLUMN provider_credential_id UUID REFERENCES provider_credentials(id),
    ADD COLUMN fallback_llm_config JSONB;                  -- {"provider": "openrouter", "model": "gpt-4o-mini"}
```

### Migrations Alembic
- `014_provider_credentials.py` — table provider_credentials
- `015_agent_llm_config.py` — colonnes LLM sur agent_configs

---

## 4. LLM Factory

```python
# core/agents/llm_factory.py

from langchain_core.language_models import BaseChatModel

def create_llm(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs,
) -> BaseChatModel:
    """Cree le bon ChatModel selon le provider."""
    match provider:
        case "openrouter":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url or "https://openrouter.ai/api/v1",
                **kwargs,
            )
        case "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model, api_key=api_key, **kwargs)
        case "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, **kwargs)
        case "ollama":
            from langchain_ollama import ChatOllama
            return ChatOllama(
                model=model,
                base_url=base_url or "http://ollama:11434",
                **kwargs,
            )
        case _:
            raise ValueError(f"Provider LLM inconnu: {provider}")
```

### Resolution de la cle API (cascade)

```python
# core/agents/credential_resolver.py

async def resolve_credential(
    agent_config: AgentConfig,
    workspace_id: str,
    db: AsyncSession,
) -> tuple[str, str, str | None]:
    """Retourne (provider, api_key, base_url) en suivant la cascade."""

    # 1. Credential specifique a l'agent
    if agent_config.provider_credential_id:
        cred = await get_credential(db, agent_config.provider_credential_id)
        return cred.provider, decrypt(cred.credential_data), cred.base_url

    # 2. Credential par defaut du workspace
    provider = agent_config.llm_provider or "openrouter"
    cred = await get_default_credential(db, workspace_id, provider)
    if cred:
        return provider, decrypt(cred.credential_data), cred.base_url

    # 3. Credential globale (site-admin, workspace_id=NULL)
    cred = await get_default_credential(db, None, provider)
    if cred:
        return provider, decrypt(cred.credential_data), cred.base_url

    # 4. Fallback .env (retro-compat Phase 1-4)
    from core.config import settings
    return "openrouter", settings.openrouter_api_key, "https://openrouter.ai/api/v1"
```

### Chiffrement (Fernet)

```python
# core/credential_vault.py

from cryptography.fernet import Fernet
from core.config import settings

_fernet = Fernet(settings.credential_encryption_key.encode())

def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
```

---

## 5. API Endpoints

### `GET /providers`
Liste les providers configures (credentials masquees).
```json
[
  {"id": "uuid", "provider": "openrouter", "display_name": "OpenRouter prod",
   "is_default": true, "key_preview": "sk-or-...abc",
   "workspace_id": "uuid", "created_at": "2026-04-01T10:00:00Z"},
  {"id": "uuid", "provider": "anthropic", "display_name": "Claude API",
   "is_default": false, "key_preview": "sk-ant-...xyz",
   "workspace_id": "uuid", "created_at": "2026-04-01T10:00:00Z"}
]
```

### `POST /providers`
Ajouter une credential provider.
```json
{
  "provider": "anthropic",
  "display_name": "Ma cle Claude",
  "credentials": {"api_key": "sk-ant-..."},
  "is_default": false
}
```

### `DELETE /providers/{id}`
Supprimer une credential.

### `POST /providers/{id}/test`
Tester une credential (envoie "Hello" au provider, verifie la reponse).
```json
{"status": "ok", "model": "claude-sonnet-4-20250514", "latency_ms": 450}
```

---

## 6. Modification de `build_agent_from_config()`

```python
# Dans core/agents/factory.py

async def build_agent_from_config(config: AgentConfig, workspace_id: str, db: AsyncSession):
    # Resoudre le provider et la cle
    provider, api_key, base_url = await resolve_credential(config, workspace_id, db)

    # Creer le LLM via la factory
    llm = create_llm(
        provider=provider,
        model=config.llm_model or "openai/gpt-4o-mini",
        api_key=api_key,
        base_url=base_url,
        **(config.llm_config or {}),
    )

    # Le reste comme avant : create_deep_agent(model=llm, tools=..., ...)
    ...
```

---

## 7. Dashboard

### Page `/dashboard/w/{workspace}/providers`

- **Liste** des credentials par provider (icone par provider, key masquee, badge "defaut")
- **Formulaire** : select provider, input cle API, nom, toggle defaut
- **Bouton Test** : appel HTMX vers `/providers/{id}/test`, affiche resultat inline
- **Supprimer** : confirmation modale

### Page agent form (modification)

- **Nouveau champ** : select "Provider LLM" (openrouter, anthropic, google, ollama)
- **Nouveau champ** : input "Modele" (avec suggestions par provider)
- **Nouveau champ** : select "Credential" (liste des credentials du workspace pour ce provider)

---

## 8. Plan de Taches

### Bloc A — Credential Vault
```
A1. [x] core/credential_vault.py : encrypt() / decrypt() avec Fernet
A2. [x] Variable CREDENTIAL_ENCRYPTION_KEY dans .env.example + config.py
A3. [x] Modele SQLAlchemy ProviderCredential
A4. [x] Migration Alembic 014_provider_credentials
A5. [x] Test : chiffrer, dechiffrer, verifier round-trip
```

### Bloc B — LLM Factory
```
B1. [x] core/agents/llm_factory.py : create_llm() avec match/case
B2. [x] core/agents/credential_resolver.py : resolve_credential() cascade
B3. [x] pip install langchain-anthropic langchain-google-genai langchain-ollama
B4. [x] Modifier build_agent_from_config() pour utiliser la factory
B5. [x] Migration Alembic 015_agent_llm_config (colonnes sur agent_configs)
B6. [x] Retro-compatibilite : si pas de provider configure, utiliser OPENROUTER_API_KEY
B7. [x] Test : creer un agent par provider, verifier que chacun utilise le bon ChatModel
```

### Bloc C — API & Dashboard
```
C1. [x] Routes CRUD : GET/POST/DELETE /providers + POST /providers/{id}/test
C2. [x] Dashboard : page providers (liste, ajout, test, suppression)
C3. [x] Dashboard : modifier le formulaire agent (champs provider/model/credential)
C4. [ ] Test : ajouter une cle Anthropic via dashboard, creer un agent, chatter (reporter — pas de cle Anthropic)
```

### Bloc D — Finition
```
D1. [x] Fallback LLM optionnel : si provider echoue, essayer fallback_llm_config
D2. [x] Mettre a jour .env.example
D3. [x] Mettre a jour docker-compose (pas de nouveau service, juste deps)
D4. [x] Sync + test sur le serveur
```

---

## 9. Dependances Python (Ajouts)

```
# core/requirements.txt — ajouts Phase 5
langchain-anthropic>=0.3
langchain-google-genai>=2.0
langchain-ollama>=0.3
cryptography>=42.0
```

---

## 10. Configuration (.env — Ajouts)

```env
# Chiffrement des credentials
CREDENTIAL_ENCRYPTION_KEY=change-me-fernet-key-base64

# Fallback retro-compat (Phase 1-4)
# OPENROUTER_API_KEY reste fonctionnel comme fallback
```

---

## 11. Definition de "Phase 5 terminee"

```
1. Ajouter une cle Anthropic via le dashboard
   -> La cle est chiffree en DB, jamais en clair

2. Creer un agent configure sur Anthropic / Claude
   -> build_agent_from_config() utilise ChatAnthropic automatiquement

3. Chatter avec cet agent
   -> La reponse vient bien de Claude (pas d'OpenRouter)

4. Supprimer la cle, chatter a nouveau
   -> L'agent fallback sur OpenRouter via .env (retro-compat)

5. Un agent sans provider configure fonctionne comme avant (Phase 4)
   -> Zero regression
```

---

*Phase 5 reecrite le 3 avril 2026 — extraite de l'ancienne Phase 5 "Expansion".*
*L'ancienne Phase 5 a ete redistribuee en Phases 5-9.*
