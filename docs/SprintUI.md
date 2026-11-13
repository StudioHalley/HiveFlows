# Sprint UI — Amélioration UX pré-Phase 5

## Objectif
Fusionner et simplifier l'interface dashboard avant la Phase 5.

---

## Blocs

### Bloc A — Sidebar [x]
- `base.html` : retirer l'entrée "Conversations" du menu
- "Workspaces" reste et pointe vers `/dashboard/workspaces`

### Bloc B — Backend: `GET /dashboard/workspaces` [x]
- Ajouter paramètre `q: str | None = None` (recherche)
- Ajouter `agent_count` par workspace (via subquery sur `WorkspaceAgent`)
- Si `q` : recherche dans les messages de tous les workspaces de l'user
  - Retourner workspaces avec match + dernier message correspondant
- Passer `filter_q`, `view` au template

### Bloc C — Template: `workspaces/list.html` [x]
Refonte complète — écran "workspace unifié" :
- Haut de page : bouton "Nouveau Workspace" + toggle Liste / Tuile (JS, localStorage)
- Barre de recherche (sans dropdown agent) — form GET, rafraîchit le tableau
- **Vue Liste** (défaut) : tableau avec colonnes :
  - Workspace (nom + slug), Rôle, Membres, Agents, bouton Modifier (si owner/admin)
  - Clic sur ligne → `/dashboard/w/{slug}/chat`
  - Si recherche active : colonne "Dernier message correspondant"
- **Vue Tuile** : cards existantes (avec lien chat au click + bouton Modifier → manage)

### Bloc D — Backend: Page de gestion unifiée [x]
- Nouvelle route `GET /dashboard/workspaces/{workspace_id}/manage`
  - Fusionner logique de `workspaces_detail` + `workspaces_edit_form`
  - Accès : membres owner/admin seulement
- `POST /dashboard/workspaces/{workspace_id}/edit` → redirige vers `/manage` après succès
- `GET /dashboard/workspaces/{workspace_id}` → redirige vers `/manage`
- `GET /dashboard/workspaces/{workspace_id}/edit` → redirige vers `/manage`

### Bloc E — Template: `workspaces/manage.html` [x]
Nouvelle page fusionnant detail + form :
- Section **Informations** : affichage + formulaire d'édition (nom, description) si owner/admin
- Section **Membres** : tableau + formulaire d'invitation si owner/admin
- Section **Clés API** : tableau + génération si owner/admin
- Bouton retour → `/dashboard/workspaces`

### Bloc F — Nettoyage [x]
- Vérifier que toutes les redirections fonctionnent
- `workspaces/detail.html` et `workspaces/form.html` restent en place (fallback) mais ne sont plus accédés via le menu

---

## Routes finales

| Route | Action |
|---|---|
| `GET /dashboard/workspaces` | Écran workspace unifié (Liste/Tuile + recherche) |
| `GET /dashboard/workspaces/new` | Form création (inchangé) |
| `POST /dashboard/workspaces/new` | Création (inchangé) |
| `GET /dashboard/workspaces/{id}/manage` | Gestion unifiée (info + membres + API keys) |
| `GET /dashboard/workspaces/{id}` | → redirect vers `/manage` |
| `GET /dashboard/workspaces/{id}/edit` | → redirect vers `/manage` |
| `POST /dashboard/workspaces/{id}/edit` | Sauvegarde → redirect vers `/manage` |
