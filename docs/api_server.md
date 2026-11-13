# API Server — endpoint OpenAI-compatible par workspace

> Chaque workspace expose une API compatible OpenAI. N'importe quelle app qui sait parler à
> OpenAI (SDK officiel, LangChain, OpenWebUI, Continue.dev, Fabric, curl…) peut donc parler
> aux **agents HiveFlows** : le champ `model` de la requête désigne un agent du workspace,
> et le proxy résout le vrai provider, la vraie clé et le vrai modèle via le Credential Vault.

- **Page dashboard** : `/dashboard/w/{slug}/api-server` (lien « API Server » du workspace ; rôle owner/admin)
- **Code** : [core/api/routes_llm_proxy.py](../core/api/routes_llm_proxy.py)
- **Tests** : [core/tests/test_llm_proxy.py](../core/tests/test_llm_proxy.py)

---

## 1. Vue d'ensemble

```
App tierce (externe)                 App Beekeeper (interne, ex: Fabric)
  token = clé API hf_...               token = workspace_id (injecté par manifest)
        │                                     │
        ▼                                     ▼
   POST {BASE}/internal/v1/{workspace_id}/chat/completions   (core)
        │
        │  1. _authorize()  → interne (IP de confiance) ou clé API hf_
        │  2. model = agent_id → AgentConfig du workspace (fallback: agent défaut)
        │  3. resolve_credential() → provider + clé + base_url (Credential Vault)
        ▼
   Provider réel (OpenRouter) ── réponse (streamée ou non) renvoyée telle quelle
```

Deux cas d'usage :

| Cas | Base URL | Token |
|---|---|---|
| **Interne** — apps du workspace (Beekeeper) | `http://core:8000/internal/v1/{workspace_id}` | `{workspace_id}` (UUID) |
| **Externe** — bridge vers l'extérieur | `{OAUTH_REDIRECT_BASE_URL}/internal/v1/{workspace_id}` | clé API du workspace (`hf_…`) |

---

## 2. Authentification — deux modes

Le Bearer token (`Authorization: Bearer <token>`) est validé ainsi :

1. **Token == `workspace_id`** (mode interne, « intra on trust ») :
   accepté **seulement si l'IP source de la connexion** appartient à un réseau de confiance.
   Par défaut : `172.16.0.0/12` (réseaux Docker) et `127.0.0.0/8`. Configurable via
   `LLM_PROXY_TRUSTED_NETWORKS` dans `.env` (liste CIDR séparée par virgules).
   C'est le mode utilisé par les apps installées via le Beekeeper — leur manifest injecte
   automatiquement les variables (ex. `apps/fabric/manifest.yaml`) :

   ```yaml
   env:
     LLM_BASE_URL: "http://core:8000/internal/v1/{workspace_id}"
     LLM_API_KEY: "{workspace_id}"
   ```

2. **Token == clé API du workspace** (`hf_…`, mode externe) :
   accepté depuis n'importe où. La clé est vérifiée par hash SHA-256 contre la table
   `api_keys` (clé **active** et appartenant **au workspace de l'URL**). `last_used_at`
   est mis à jour à chaque requête — visible dans le dashboard.

   Les clés se génèrent et se révoquent depuis la page **API Server** du workspace
   (ou la page manage, section « Clés API »). La valeur complète n'est affichée
   **qu'une seule fois**, à la création.

Notes de sécurité :
- La détection interne/externe se base sur l'**IP de la connexion TCP** (`request.client`),
  jamais sur `X-Forwarded-For` (non falsifiable par un client externe).
- Conséquence : une requête faite **depuis l'hôte Docker lui-même** (ex. `curl localhost:8002`)
  arrive avec une IP de gateway Docker (172.x) et est donc considérée interne. C'est cohérent
  avec le modèle « intra on trust » : qui a accès à l'hôte a déjà accès à tout.
- Un refus de token workspace depuis une IP externe est loggé (`hiveflows.llm_proxy`, WARNING).
- Révoquer une clé (dashboard) coupe l'accès immédiatement (vérification à chaque requête).

---

## 3. Endpoints

Tous préfixés par `/internal/v1/{workspace_id}` — `{workspace_id}` est l'UUID du workspace
(affiché sur la page API Server).

### `GET /models`

Liste les « modèles » disponibles au sens OpenAI :
- les **agents actifs** du workspace (leur `id` et leur `name`) ;
- des alias OpenAI courants (`gpt-4o`, `gpt-4o-mini`, …) pour compatibilité avec les apps
  qui valident un modèle par défaut au démarrage (ex. Fabric).

### `POST /chat/completions`

Chat Completions standard. Champ `model` = **id d'un agent** du workspace (voir la page
API Server pour la liste). Si le modèle demandé ne correspond à aucun agent → fallback
automatique sur l'agent `is_default` du workspace ; s'il n'y en a pas → 404.

- `stream: true` supporté (SSE relayé tel quel depuis le provider).
- Paramètres usuels (`temperature`, `max_tokens`, …) transmis au provider.
- Timeout amont : 300 s.

### `POST /responses`

Compatibilité avec la **Responses API** (SDK OpenAI récents, utilisé par Fabric) :
la requête est convertie en Chat Completions vers le provider, puis la réponse est
reconvertie au format Responses (`output[0].content[0].text`). Streaming supporté.

---

## 4. Résolution agent → provider

1. `model` est cherché comme `AgentConfig.id` **actif** dans le workspace.
2. Introuvable → fallback sur l'agent `is_default=True` actif du workspace.
3. `resolve_credential(agent, workspace)` applique la cascade Phase 5 :
   credential de l'agent → du workspace → global → `.env` (`OPENROUTER_API_KEY`).
4. Le modèle réel envoyé au provider = `agent.llm_model` (ou `DEFAULT_LLM_MODEL`).

⚠️ **Limitation actuelle** : seuls les providers à API OpenAI-compatible sont proxifiables —
aujourd'hui `openrouter` uniquement (`_OPENAI_COMPAT_PROVIDERS`). Un agent configuré sur un
autre provider renvoie 400 avec un message explicite. La page API Server marque ces agents
« non supporté par l'API ».

À noter : le **system prompt, les tools et la mémoire de l'agent ne sont PAS appliqués** par
ce proxy — il route vers le provider/modèle de l'agent, il ne rejoue pas le runtime deepagents.
C'est un choix assumé : l'API Server sert de « passerelle LLM » (l'app tierce gère sa propre
conversation), pas d'API de chat d'agent. Pour parler au *vrai* agent (avec tools/mémoire/HITL),
utiliser `POST /chat` / `POST /chat/stream` du Core.

---

## 5. Exemples

### curl (externe, clé API)

```bash
curl https://beta.hiveflows.com/internal/v1/<WORKSPACE_UUID>/chat/completions \
  -H "Authorization: Bearer hf_xxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "assistant-principal",
    "messages": [{"role": "user", "content": "Bonjour !"}]
  }'
```

### SDK OpenAI (Python)

```python
from openai import OpenAI

client = OpenAI(
    api_key="hf_xxxxxxxxxxxx",
    base_url="https://beta.hiveflows.com/internal/v1/<WORKSPACE_UUID>",
)
resp = client.chat.completions.create(
    model="assistant-principal",
    messages=[{"role": "user", "content": "Bonjour !"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")
```

### App générique (variables d'environnement)

```
OPENAI_API_KEY=hf_xxxxxxxxxxxx
OPENAI_BASE_URL=https://beta.hiveflows.com/internal/v1/<WORKSPACE_UUID>
```

---

## 6. Configuration

| Variable `.env` | Défaut | Rôle |
|---|---|---|
| `LLM_PROXY_TRUSTED_NETWORKS` | `172.16.0.0/12,127.0.0.0/8` | Réseaux (CIDR) où le token workspace_id est accepté |
| `OAUTH_REDIRECT_BASE_URL` | `http://localhost:8002` | Sert de base à l'URL externe affichée sur la page dashboard |

---

## 7. Limites connues / évolutions possibles

- **Providers non-OpenAI** (Anthropic natif, Ollama…) : nécessiterait une traduction de format
  par provider dans le proxy (ou le wrapper OpenAI-compatible d'Ollama).
- **Pas de quotas/rate limiting par clé** : une clé = accès illimité aux agents du workspace.
  À prévoir avant toute ouverture large (cf. Phase 9 / rapport d'état).
- **Pas de scope par agent** : une clé donne accès à tous les agents du workspace. Le champ
  `permissions` de la table `api_keys` existe déjà si on veut scoper plus finement un jour.
- **Comptage de tokens/coûts** : le proxy est le point de passage idéal pour mesurer la
  consommation par workspace/clé (usage est déjà relayé dans les réponses provider).
