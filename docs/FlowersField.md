# FlowersField — HiveFlows fédéré & décentralisé

> *Le miel d'une seule ruche nourrit une famille. Le champ de fleurs nourrit un écosystème.*
> *Les abeilles ne butinent pas une fleur. Elles butinent un champ.*

---

## 0. TL;DR

**Vision.** Faire évoluer HiveFlows d'une plateforme **mono-hôte** (un serveur, plusieurs clients) vers un **réseau d'instances privées coopérantes** (des *nodes*, chacune hébergée par un utilisateur chez lui, sur un VPS, un NAS, un laptop), reliées par un **hub léger** (`hiveflows.com`) qui gère identité, découverte et relais. Chaque node garde la souveraineté de ses données, mais peut **partager** : workspaces, agents, fichiers, et — plus excitant — **ressources LLM privées** (Ollama, vLLM, GPU locale).

**Métaphore.** Le **hub** = la place du marché, le registre des ruches. Les **nodes** = les ruches, chacune propriétaire de son miel. Les **agents** = les abeilles qui butinent d'un champ à l'autre.

**Architecture choisie : hybride fédérée.** Ni pur-P2P (trop complexe, trop fragile), ni pur-central (à quoi bon si tout vit chez `hiveflows.com` ?). Le hub fait l'équivalent du *Discord login + server directory*. Le contenu, le compute, les agents vivent **sur les nodes**.

**Composants déjà en place qui servent de base :**
- **Portal** — tunnel WebSocket inverse, traverse NAT/firewall
- **remote-fs** — synchro de fichiers inter-nodes (watchdog + WS + vector clocks)
- **Beekeeper** — control plane Docker isolé
- **Activity Bus** — Redis pub/sub déjà en place pour le nerveux local

Tout le reste se construit par-dessus ces primitives.

---

## 1. Pourquoi faire ça ? (La proposition de valeur)

Avant d'évaluer le *comment*, on évalue le *pourquoi*. Si la réponse est floue, le projet meurt à la première difficulté technique.

### 1.1 Pour l'utilisateur final

| Douleur actuelle | Ce que FlowersField résout |
|------------------|----------------------------|
| "Mes conversations et données vivent sur *ton* serveur" | Tu peux héberger ta propre instance, tu possèdes ta DB, tes fichiers, tes logs |
| "J'ai un GPU à la maison qui dort la nuit" | Tu peux exposer ton Ollama à tes amis / ton équipe, en contrôlant qui et quoi |
| "Je paye OpenAI + Anthropic + Groq même pour des trucs triviaux" | Tu peux router vers un LLM privé d'un pair de confiance (marketplace / barter / gratuit) |
| "Je veux collaborer avec X sans centraliser chez un SaaS" | Un workspace peut vivre entre deux nodes, comme Matrix / XMPP mais pour agents |
| "Si HiveFlows.com tombe, tout s'arrête" | Les nodes continuent de fonctionner localement, avec reconnexion directe peer-to-peer dès que le hub revient |
| "Mes agents sont enfermés dans un silo" | Un agent peut *visiter* un workspace sur un autre node, comme un invité |

### 1.2 Pour le projet HiveFlows

- **Défensivité** : le modèle fédéré est copié difficilement par un acteur centralisé (Discord/OpenAI). C'est **l'asymétrie stratégique**.
- **Distribution du coût** : chaque node paye son CPU/GPU/stockage ; le hub reste maigre.
- **Organique** : la croissance ressemble à email/Matrix/Mastodon plutôt qu'à un SaaS — moins de pic, plus de durabilité.
- **Pitch** : « Tu n'achètes pas un produit, tu rejoins un réseau. »

### 1.3 Honest check — pour qui ce n'est **pas** fait

- L'utilisateur casual qui veut juste "parler à un chatbot" n'a aucun besoin de fédération.
- Les entreprises qui veulent un SaaS turnkey ne voudront pas gérer leur node.
- Le modèle se justifie pour : **power-users**, **devs**, **communautés**, **équipes tech**, **gens soucieux de privacy**, **fournisseurs de compute marginal**.

C'est donc **une couche optionnelle** — HiveFlows reste utilisable en mono-node pour tout le reste.

---

## 2. Gros plan : les 7 problèmes à résoudre

On ne peut pas tout concevoir d'un coup. Mais il faut au minimum avoir une réponse *crédible* à chaque problème avant de coder.

1. **Identité** — Comment un node prouve qui il est, et comment un user prouve qui il est ?
2. **Découverte** — Comment les nodes trouvent les autres nodes ?
3. **Transport** — Comment deux nodes se parlent à travers NAT/firewall ?
4. **Synchronisation d'état** — Chat, workspaces, fichiers, mémoire agent : qui est la source de vérité ?
5. **Routage de compute** — Comment un agent d'un node utilise un LLM d'un autre node ?
6. **Gouvernance** — Qui a le droit de faire quoi ? Comment limiter l'abus ?
7. **Gestion de l'offline** — Que se passe-t-il quand un node disparaît, revient, diverge ?

On aborde chaque problème ci-dessous.

---

## 3. Architecture proposée (couches)

```
┌────────────────────────────────────────────────────────────┐
│  L4 — GOVERNANCE                                            │
│  Trust levels • Quotas • Policy • Audit • Reputation        │
├────────────────────────────────────────────────────────────┤
│  L3 — FEDERATED SERVICES                                    │
│  LLM mesh • Agent mesh • Shared workspaces • Skill registry │
├────────────────────────────────────────────────────────────┤
│  L2 — SYNC                                                  │
│  Files (remote-fs) • Chat log (CRDT) • Memory • Checkpoints │
├────────────────────────────────────────────────────────────┤
│  L1 — TRANSPORT                                             │
│  Portal (WS tunnel) • Typed event framing • Signed frames   │
├────────────────────────────────────────────────────────────┤
│  L0 — DIRECTORY (hub: hiveflows.com)                        │
│  OAuth • Node registry • Peer discovery • Optional relay    │
└────────────────────────────────────────────────────────────┘
```

Chaque couche utilise la couche du dessous. Chaque couche peut être remplacée si besoin.

---

## 4. L0 — Directory & Identity (hub `hiveflows.com`)

### 4.1 Rôle du hub

Le hub est **volontairement minimaliste**. Plus il est maigre, plus l'écosystème est résilient.

Ce que le hub **fait** :
- Auth OAuth2 (Google, GitHub, Apple) → un `user_id` global unique
- Registre des **nodes** : chaque node qui s'enregistre reçoit un `node_id` + une **clé publique ed25519** associée
- Annuaire **user → nodes** : « patrick vit sur `node-patrick-home` et `node-patrick-vps` »
- **Peer discovery** : API `GET /peers?user=bob` renvoie la liste des nodes publiques de Bob
- **Relais éphémère** : une boîte à messages chiffrés (comme Signal) pour un node offline — le hub stocke des enveloppes opaques, **il ne peut pas les lire**
- **Révocation** : un user peut révoquer un node compromis

Ce que le hub **ne fait pas** :
- Ne voit **aucune** conversation, aucun fichier, aucune donnée agent (en clair)
- Ne relaie pas le trafic normal — ça reste node-à-node
- Ne tient pas les clés API LLM (elles vivent sur le node)
- Ne fait pas tourner d'agents

> **Principe** : le hub peut tomber une journée, **les nodes continuent de fonctionner**. Les nodes ayant déjà découvert leurs pairs gardent les adresses en cache et se reconnectent directement.

### 4.2 Modèle d'identité

```
user_id (global, hub)     ─┐
                            ├─→ identifie une personne
user_slug (handle unique)  ─┘

node_id (global, hub)      ─┐
node_pubkey (ed25519)       ├─→ identifie une instance
node_slug (friendly name)   ─┘   (ex: "patrick-home", "equipe-alpha-vps")

agent_id (local au node)   ─→ identifie un agent (home node = son propriétaire humain)
workspace_id (local au node home) ─→ identifie un workspace
```

**Adressage fédéré** (inspiration Matrix/ActivityPub) :

- User : `@patrick`
- User avec désambiguation : `@patrick@hiveflows.com` (si le hub est différent)
- Agent : `@time-traveler@patrick-home` (l'agent `time-traveler` qui vit sur le node `patrick-home`)
- Workspace : `#dev-chat@equipe-alpha-vps`

Dans un workspace partagé, on peut écrire `@time-traveler@patrick-home peux-tu vérifier ce fichier ?` — le message est routé vers le node home de l'agent.

### 4.3 Enregistrement d'un node

1. User installe HiveFlows sur sa machine.
2. Au premier lancement, le node génère une paire ed25519 (`node_privkey` reste local, jamais transmise).
3. User fait OAuth sur hiveflows.com → reçoit un JWT.
4. Le node appelle `POST /nodes/register` avec `{node_pubkey, node_slug, capabilities}` + JWT.
5. Hub valide et émet un `node_token` (JWT signé par le hub) — c'est ce token qui authentifie le node dans toutes les interactions futures.

### 4.4 Anti-patterns à éviter

- ❌ Mettre les clés API LLM user dans le hub → violation de la promesse de souveraineté
- ❌ Stocker l'historique de chat dans le hub → fuite de privacy
- ❌ Dépendre du hub pour parler entre nodes déjà connus → fragilité

---

## 5. L1 — Transport (Portal étendu)

### 5.1 Du Portal actuel au Portal fédéré

**Portal aujourd'hui** : reverse WebSocket, un *device* se connecte au Core et expose un service local (localhost:8080 par exemple). Unidirectionnel dans l'intention (« je rends un service dispo »), bidirectionnel en transport.

**Portal fédéré** : deux nodes (deux instances HiveFlows complètes) établissent une session WebSocket persistante, **signée** par les clés du hub, et s'échangent des **events typés**. Le NAT traversal existe déjà gratuitement (l'un des deux initie, le hub peut jouer le rôle de *rendezvous point* pour la négociation initiale).

### 5.2 Protocole de frame

Chaque message échangé est une enveloppe signée :

```json
{
  "id": "uuid-v7",
  "from": "node-patrick-home",
  "to": "node-equipe-alpha",
  "ts": "2026-05-01T10:00:00Z",
  "type": "CHAT_MSG_APPEND",
  "payload": { ... },
  "sig": "ed25519(...)"
}
```

**Types de frames** (extensible) :

| Catégorie | Exemples |
|-----------|----------|
| Control | `HELLO`, `HEARTBEAT`, `BYE`, `CAPS_QUERY` |
| Sync | `FILE_CHANGED`, `FILE_REQUEST`, `CHAT_APPEND`, `MEMORY_PATCH` |
| Invoke | `LLM_REQUEST`, `LLM_STREAM_CHUNK`, `AGENT_INVOKE`, `TOOL_CALL` |
| Workspace | `WS_INVITE`, `WS_MEMBER_CHANGED`, `WS_STATE_SNAPSHOT` |
| Policy | `RATE_LIMIT`, `DENY`, `QUOTA_UPDATE` |

### 5.3 Modes de connexion

1. **Direct** — node A → node B (WS inverse, l'un des deux sort vers l'Internet public, l'autre derrière NAT). Fonctionne tant qu'**un** des deux est joignable publiquement (via VPS, port forwarding, Cloudflare tunnel).
2. **Via hub relay** — si les deux nodes sont derrière NAT, le hub relaie **des enveloppes chiffrées** (E2E entre nodes, hub aveugle).
3. **Offline store-and-forward** — si B est hors-ligne, A dépose l'enveloppe au hub, B la récupère à son réveil. TTL configurable (24h par défaut).

### 5.4 Redis — ne pas le bridger

> Question initiale : *Comment deux serveurs Redis se bridgent ?*

**Réponse honnête : on ne devrait pas les bridger.** Redis est le **système nerveux local** d'un node (pub/sub, Celery broker, heartbeats). L'exposer à un pair :
- casse l'encapsulation (chaque node verrait les events internes des autres)
- multiplie le trafic inutilement (heartbeats, métriques)
- crée un couplage fort (version, schéma, sécurité)

**Le bon pattern** : chaque Redis reste local. On définit un **Inter-Node Event Bus** qui est une **traduction curatée** d'events locaux vers des frames typées sur le Portal, et vice-versa. Un adaptateur `NodeBridge` fait le mapping :

```python
# Côté node A
redis.pubsub("workspace:42:chat") → filter/transform → portal.send(CHAT_APPEND to node B)

# Côté node B
portal.on("CHAT_APPEND from A") → redis.publish("workspace:42:chat", ...)
```

Avantages : typé, sécurisé, versionnable, observable, et chaque côté peut évoluer son Redis indépendamment.

---

## 6. L2 — Synchronisation d'état

C'est **le cœur technique**. On a plusieurs natures de données à synchroniser, chacune avec sa stratégie.

### 6.1 Fichiers — `remote-fs` (déjà conçu)

Stratégie : vector clocks + eventual consistency + LWW avec fichiers `.conflict`. **Déjà couvert dans `remote-fs-spirit.md`.** On garde.

### 6.2 Chat log — append-only avec Hybrid Logical Clock (HLC)

Un chat est par nature **append-only** (on n'édite pas les messages passés, au pire on les soft-delete). Ça tombe bien : c'est le cas le plus facile à synchroniser.

- Chaque message a un `(hlc_timestamp, node_id, local_seq)` → ordre total déterministe
- Replay : un node B rejoint un workspace → demande `GET /chat/workspace/42?since=<hlc>` au home node
- Edits/deletes → un nouveau message-event (`EDIT`, `DELETE`) qui référence le précédent
- Pagination infinie : déjà dans Phase 4, compatible

**Trap à éviter** : essayer d'implémenter un vrai CRDT riche (Yjs) pour le chat. Surkill. Le chat est simple, append-only, un HLC suffit.

### 6.3 Mémoire agent — checkpoint + delta

LangGraph a déjà un système de **checkpointer PostgreSQL**. La mémoire d'un agent est une séquence de checkpoints.

Pour fédérer :
- La mémoire d'un agent vit sur son **home node** (celui du user propriétaire).
- Quand un agent "visite" un workspace d'un autre node, il garde sa mémoire chez lui et communique via RPC (voir §7.2).
- Si un workspace **entier** est répliqué sur plusieurs nodes : le LangGraph Store par `namespace=workspace:42` est lui-même répliqué via patchs (append-only sur la table de mémoires).

### 6.4 Workspace — un concept "location-aware"

Chaque workspace a un **home node** (celui qui l'a créé). Les autres nodes peuvent :

- **Accéder à distance** (mode *guest*) — pas de copie locale, tout passe par le home node via Portal. Simple, mais si home node offline → workspace indisponible.
- **Répliquer** (mode *replica*) — copie locale synchronisée. Résilient, mais conflits à gérer.

On commence par **mode guest** (simple à implémenter), on ajoute **replica** plus tard pour les usages collaboratifs intensifs.

### 6.5 Le piège des secrets

Un workspace peut référencer des credentials (clés API LLM, tokens). **Ces secrets ne quittent jamais leur node d'origine**. Si l'agent tourne sur le node A et utilise une clé OpenAI du node B → c'est l'agent qui **migre** (ou mieux : **appelle** le node B via RPC pour que B fasse la requête LLM avec sa clé locale). Voir §7.3 sur le LLM mesh.

---

## 7. L3 — Services fédérés

### 7.1 LLM Mesh (la feature killer)

**Vision** : un agent du node A demande à un LLM → le node A connaît un ensemble de **LLM endpoints** publiés par ses pairs (ou loués, ou publics) → il choisit et route.

#### 7.1.1 Publication d'un LLM

Un node annonce ses capacités LLM au hub :

```yaml
# config.yaml d'un node
llm_endpoints:
  - name: "llama-3-home"
    provider: "ollama"
    base_url: "http://localhost:11434"
    models: ["llama-3.1-70b", "qwen-2.5-coder"]
    visibility: "friends"      # private | friends | public
    quota:
      tokens_per_day: 1_000_000
      concurrent_requests: 2
    pricing:
      mode: "free"             # free | credits | barter
```

Le hub maintient un **catalogue fédéré** :
```
GET /mesh/llm?model=llama-3.1-70b&visibility=accessible-to(me)
→ [{ node: "patrick-home", quota_left, latency_ms, pricing }, ...]
```

#### 7.1.2 Routage d'une requête LLM

Déjà, HiveFlows utilise une **API compatible OpenAI** (`ChatOpenAI + base_url`). C'est notre point de levier. Un `LLMRouter` dans le core :

1. L'agent du node A demande `"llama-3.1-70b"`.
2. Le router consulte d'abord les providers **locaux** (Ollama local, OpenRouter via clé locale).
3. Si besoin / si préférence, il consulte le **mesh** → trouve que node B offre ce modèle.
4. Il ouvre un frame `LLM_REQUEST` sur le Portal vers B.
5. Node B reçoit, valide les quotas/permissions, forward à son Ollama local.
6. Réponse streamée via `LLM_STREAM_CHUNK` → node A → agent.

C'est **transparent pour l'agent** : l'API reste OpenAI-compatible, le mesh est un détail d'infra.

#### 7.1.3 Sécurité & abuse

- **Tokens de session courts** : chaque requête LLM est authentifiée par un JWT signé par le user caller, validé par le hub.
- **Quotas hard** : le node qui héberge compte les tokens consommés par caller.
- **Kill switch** : le propriétaire peut couper un endpoint à tout moment (event `LLM_REVOKE`).
- **Audit log** : chaque requête fédérée est loggée sur les **deux** nodes.

#### 7.1.4 Pricing / barter (Phase ultérieure)

- **Mode free** : les amis / la famille, pas de compte, juste des quotas.
- **Mode credits** : ledger simple dans le hub (« Patrick a prêté 1M tokens à Alice → +1M crédits »). Purement comptable, non monétaire.
- **Mode monétaire** : plus tard, hors scope V1. Attention aux frictions légales.

### 7.2 Agent Mesh

Un agent appartient à un user → son **home node** est celui de son user. Quand l'agent est invité dans un workspace d'un autre node :

- **Option 1 (Remote control, préférée)** — l'agent tourne sur son home node. Les events du workspace (`CHAT_APPEND`, etc.) sont forwardés via Portal. L'agent répond, ses messages reviennent. Pro : mémoire/secrets/tools restent chez home. Con : latence, dépend de home node online.
- **Option 2 (Delegation)** — une copie "légère" de l'agent s'exécute sur le node invité, mais appelle home node pour les tools privés (fichiers, LLM, secrets). Plus complexe, reporté.

**Règle V1** : *Option 1*. Un agent est toujours "quelque part". Si son home node est offline, l'agent est **temporairement indisponible** dans tous les workspaces distants (avec un indicateur UI clair).

### 7.3 Shared Workspaces

Un workspace partagé entre nodes A (home) et B (guest) :

- Membres A + B, chacun identifié par leur `@slug@node`
- Chat : synchronisé par append avec HLC (§6.2)
- Fichiers : synchronisés par remote-fs (§6.1)
- Agents : chacun reste sur son home, communique via Portal
- État (topic, réglages, perms) : une `workspace_state` sérialisée, mise à jour via événements `WS_PATCH` idempotents

**Un workspace a toujours un home node**. Le home node est :
- La source de vérité pour les réglages globaux (qui est membre, quelles perms)
- Le point de coordination pour les nouveaux arrivants
- Le responsable des backups

Si home node offline → les guests voient le workspace en **read-only** (ils lisent leur cache local) jusqu'à retour.

### 7.4 Skill Marketplace fédéré (Phase lointaine)

Les skills sont des artefacts (dossiers avec scripts). On peut imaginer un registre décentralisé où chaque node peut publier et installer des skills d'autres nodes. **Hors scope V1**, mais l'architecture le permet naturellement.

---

## 8. L4 — Gouvernance

### 8.1 Niveaux de confiance

Chaque user/node paire a un **trust level** configuré par l'opérateur du node :

- `blocked` — refus total
- `public` — peut voir les endpoints publics (LLM gratuits, skills open), pas de DM, pas d'invitation workspace
- `acquaintance` — peut inviter dans un workspace, pas d'accès LLM par défaut
- `friend` — accès LLM partagés, invite libre, remote-fs autorisé
- `family` — tout, plus mémoire partagée optionnelle

Chaque interaction inter-nodes est filtrée par ce niveau. Par défaut : `public`.

### 8.2 Quotas & rate limiting

Par `(peer_node, endpoint_type)` :
- Requêtes/sec
- Tokens/jour
- Bandwidth/jour
- Concurrent streams

Tous configurables, tous audités.

### 8.3 Audit log

Chaque frame entrante/sortante est loggée avec `(ts, from, to, type, size, allowed/denied, reason)`. Permet de repérer l'abus et les anomalies.

### 8.4 Révocation

Si un node est compromis :
1. User déclare la compromission au hub → `POST /nodes/{id}/revoke`
2. Hub diffuse aux pairs actifs un frame `NODE_REVOKED`
3. Les pairs coupent les sessions, invalident le pubkey
4. User crée un nouveau node avec une nouvelle paire de clés

---

## 9. Gestion de l'offline (cas par cas)

### 9.1 Scénario A — Pair offline temporaire

- Queue locale de frames destinées au pair (limite : 10k frames, 100MB)
- Retry exponentiel
- Au retour du pair (frame `HELLO`), replay de la queue
- Si la queue déborde → on bascule sur **hub relay** (enveloppes stockées 24h)

### 9.2 Scénario B — Node home d'un workspace offline

- Les guests voient le workspace en read-only (cache local)
- Aucun nouveau message n'est persisté (les drafts restent en attente)
- Un indicateur UI clair : "Home node offline, returning in X"
- Si le home node est offline > 7 jours : option de **reparenter** le workspace sur un autre node (élection à la majorité des membres, ou choix manuel du owner)

### 9.3 Scénario C — Hub offline

- Les nodes perdent la capacité de découvrir **de nouveaux** pairs
- Les pairs **déjà connus** continuent de se parler directement (adresses en cache)
- Le OAuth est cassé pour les nouveaux signups
- Les nodes existants continuent de fonctionner : leurs `node_token` ont une durée de vie longue (30j) et sont cachés

### 9.4 Scénario D — Partition réseau / split brain

- Chaque moitié du réseau continue localement
- Au retour à la normale : remote-fs gère les fichiers (LWW + .conflict), chat gère via HLC, workspace_state via événements idempotents
- Un merge humain peut être nécessaire pour les .conflict

---

## 10. Évaluation honnête des risques

| Risque | Probabilité | Sévérité | Mitigation |
|--------|-------------|----------|------------|
| Complexité explosive, dev impossible à maintenir | **Haute** | Critique | **Phasage strict, V1 minimal**, réutiliser Portal + remote-fs, feature flag |
| CRDT mal implémenté → corruption de chat | Moyenne | Haute | Append-only + HLC (simple), tests fuzzing, pas de vrai CRDT tant que pas nécessaire |
| Fuite de secrets via frame mal scopée | Moyenne | Critique | Audit de chaque type de frame, E2E par défaut, secrets jamais sérialisés |
| Abuse du LLM mesh (DDoS, mining) | Haute | Moyenne | Quotas hard, trust levels, kill switch, audit |
| User installe un node, ne sait pas le maintenir | **Haute** | Moyenne | Auto-update, healthcheck UI, onboarding guidé, option managed-hosting |
| Hub devient un SPOF pratique malgré la décentralisation | Moyenne | Haute | Cache agressif côté node, fonctionnement dégradé sans hub, support multi-hub (futur) |
| Schéma de frames devient incompatible entre versions | Haute | Haute | Versioning explicite (`"v": 1`), graceful degradation, negotiation dans `HELLO` |
| Latence cross-node rend l'UX désagréable | Moyenne | Moyenne | Streaming agressif, préfetch, cache local, UI qui montre la provenance |
| Conflits légaux (GDPR, cross-border data) | Basse | Haute | Chaque node est responsable juridiquement de ses données ; le hub est aveugle (argument de défense) |
| Abandon par l'utilisateur (« trop de friction ») | **Haute** | Critique | Mono-node doit rester trivial ; fédération = opt-in, pas de dégradation si pas utilisée |

**Risque dominant** : **complexité**. La mitigation principale est **phasage brutal** (voir §12).

---

## 11. Angles morts identifiés (questions ouvertes)

Ces questions **ne sont pas résolues**. Elles doivent être tranchées avant ou pendant le dev.

1. **Identité de workspace cross-node** — si un user est à la fois sur son node perso et invité sur un autre, comment son agent "home" se retrouve dans un workspace chez un tiers ? Probablement via la réplique `user_id` hub-global.

2. **Garbage collection des frames offline** — que fait-on des enveloppes en hub-relay après TTL ? Purge silencieuse ? Notification au sender ? Compromise : TTL 7j + notification.

3. **Migration de workspace** — un user veut déménager son workspace du node A au node B. Process manuel ou outil ? V2.

4. **Cas des LLM non-OpenAI-compatible** — un agent Claude (Anthropic natif) appelé via mesh demande une ré-adaptation. Contourner en imposant le wrapper OpenAI-compatible au niveau du mesh.

5. **Versioning cross-node des agents** — si un agent V1 sur node A interagit avec un node B qui a des hooks V2, risque d'incompatibilité. Besoin d'un schema registry au niveau hub ? Ou negotiation par connection ?

6. **Observabilité fédérée** — chaque node a ses logs. Comment débug un problème qui traverse 3 nodes ? Probablement un tracing ID (UUID) propagé dans chaque frame + agrégation best-effort côté user.

7. **Découverte de pairs sans hub** — mDNS pour LAN ? DHT pour WAN ? Kademlia-style ? Nice-to-have, V3+.

8. **Agent qui "butine" activement** — un agent qui, pour accomplir une tâche, appelle spontanément 4 LLMs différents sur 3 nodes différents. UX et coût : comment rendre ça visible à l'utilisateur ?

9. **Conflits entre replicas d'un workspace** — si deux replicas divergent sur la liste des membres, comment résoudre ? Home node reste la source de vérité ; cette question devient triviale tant qu'on garde un home node.

10. **Le cas "hub down + node A veut joindre un nouveau node B"** — sans hub, pas d'échange de clés publiques. Solution : QR code / pairing code manuel (comme Matrix SAS verification). Pragmatique, robuste, hors-ligne.

---

## 12. Roadmap (phasée honnêtement)

Phases **FF** (FlowersField), qui démarrent **après** la Phase 9 actuelle de HiveFlows. Chacune indépendamment livrable, chacune ajoutant une couche de capacité.

### Phase FF-0 — Préparation (en partie déjà fait)
- [x] Portal (WS tunnel) — déjà en prod
- [x] remote-fs (file sync) — en cours
- [ ] Extraction d'un module `node_identity` : ed25519 keypair, stockage sécurisé, signature/verif
- [ ] Framework de frames typées (schema, versioning, signature) — utilisé localement d'abord
- [ ] Feature flag global `federation_enabled`

### Phase FF-1 — Directory & Identity (hub léger)
- [ ] Déploiement `hiveflows.com` (mono-container, SQLite suffit au début)
- [ ] OAuth Google/GitHub
- [ ] `POST /nodes/register`, `GET /peers`, `POST /nodes/{id}/revoke`
- [ ] `node_token` JWT + rotation
- [ ] Dashboard hub minimal (voir ses nodes enregistrés)

### Phase FF-2 — Node-to-node transport
- [ ] Extension Portal : mode "node session" (vs "device session" actuel)
- [ ] Handshake `HELLO` avec negotiation de capabilities + version
- [ ] Protocole de frames signées (JSON + `sig`)
- [ ] `NodeBridge` : traduction Redis ↔ frames, testé avec 2 nodes en local
- [ ] Hub relay optionnel pour nodes derrière NAT

### Phase FF-3 — LLM Mesh
- [ ] `LLMEndpointRegistry` côté node + publication au hub
- [ ] `LLMRouter` dans le Core : choix local vs mesh
- [ ] Streaming `LLM_REQUEST`/`LLM_STREAM_CHUNK`
- [ ] Quotas + audit
- [ ] UI : "paramètres LLM mesh" + indicateur de provenance

### Phase FF-4 — Shared Workspaces (mode guest)
- [ ] `WS_INVITE` cross-node
- [ ] Chat log HLC + replication
- [ ] Workspace state patchs idempotents
- [ ] UI : indicateur "home node: X"

### Phase FF-5 — Agent Mesh (remote control)
- [ ] Adressage `@slug@node` dans le chat parser
- [ ] `AGENT_INVOKE` frame + RPC LangGraph remote
- [ ] Mémoire agent reste sur home node
- [ ] UI : voir d'où vient un agent

### Phase FF-6 — Governance
- [ ] Trust levels par pair
- [ ] Rate limit configurable
- [ ] Audit log fédéré
- [ ] Révocation

### Phase FF-7 — Resilience & Replicas
- [ ] Replica mode pour workspaces critiques
- [ ] Store-and-forward via hub relay
- [ ] Reparenting manuel de workspaces
- [ ] Pairing code offline

### Phase FF-8 — Ecosystem
- [ ] Skill marketplace fédéré
- [ ] Credits ledger (si la communauté le demande)
- [ ] mDNS / DHT discovery
- [ ] Multi-hub (fédération de hubs)

**Jalon V1 utilisable** : FF-0 → FF-3 (fichier + LLM mesh + identité). C'est déjà **énormément de valeur** : un user peut héberger son node, partager son Ollama avec ses amis, et synchroniser ses fichiers. Le reste est bonus.

---

## 13. Critères de succès

1. **Mono-node inchangé** : un utilisateur qui n'active jamais la fédération n'a pas un octet de différence dans son expérience.
2. **Install d'un node = 10 minutes** : un `docker compose up` + OAuth dans le dashboard. Pas plus.
3. **Un workspace peut être partagé entre deux nodes derrière NAT** sans configuration réseau manuelle.
4. **Un agent sur node A utilise un Ollama sur node B** de façon transparente, avec audit des deux côtés.
5. **Hub down → nodes continuent de fonctionner** pour tous les pairs déjà découverts.
6. **Un node compromis peut être révoqué en < 5 minutes**, sans impact sur les autres.
7. **Zero surprise budget** : aucun agent ne consomme des tokens LLM distants sans que le user l'ait validé explicitement (au moins une fois par endpoint).

---

## 14. Questions ouvertes à trancher avec Patrick

- [ ] **Nom du projet** : "FlowersField" reste en interne, ou devient public (marketing) ?
- [ ] **Le hub = service hébergé par Patrick, ou open source self-hostable ?** (bifurcation majeure : B2C SaaS vs OSS communautaire)
- [ ] **Identité** : on part sur ed25519 ou on accepte des certificats TLS mutuels ?
- [ ] **Stratégie de monétisation du hub** (si B2C) : free + paid tier ? Quotas hub-relay ?
- [ ] **Compat légal** : tos du hub sur "on ne lit pas vos données" → vrai engagement ?
- [ ] **Beta-testeurs** : la landing page actuelle sert-elle à recruter spécifiquement des early nodes ?
- [ ] **Priorité FF-2 vs FF-3** : commence-t-on par le transport (FF-2) "pour rien" ou directement par une feature utilisateur (FF-3, LLM mesh) en l'imbriquant ?

---

## 15. Conclusion — l'abeille et le champ

HiveFlows mono-node est une **ruche**. Elle produit du miel. Elle nourrit son propriétaire.

FlowersField est le **champ**. Il ne crée pas le miel — il rend possible que les ruches voisines coopèrent, se visitent, se prêtent de la nourriture quand l'une est à sec.

**Ce n'est pas un projet "décentralisé" par idéologie.** C'est un projet fédéré par **pragmatisme** : aligner la topologie du logiciel avec la topologie des humains (amis, équipes, communautés). Un hub central pour l'essentiel social (identité, découverte). Des nodes privés pour l'essentiel technique (données, compute, LLM). Des abeilles qui circulent.

**Le vrai test.** Si, dans 18 mois, Patrick et 5 amis font tourner chacun leur HiveFlows à la maison, partagent trois workspaces et un Ollama 70B qui tourne sur le GPU de l'un d'eux, et que ça marche sans qu'ils y pensent — le projet est un succès.

Le reste, c'est de l'ingénierie patiente. Couche par couche. Phase par phase. Une abeille à la fois.

🐝🌻
