# Sprint File Manager — Plan de match

> **Objectif** : Donner aux utilisateurs web un file manager complet pour gérer les fichiers de leur workspace, avec navigation, upload, edition, preview et opérations CRUD — le tout sécurisé et cantonné au répertoire du workspace.

---

## Architecture globale

```
Browser (HTMX + JS vanilla)
    │
    ├── File Manager UI (template Jinja2)
    │   ├── Arbre / liste de fichiers
    │   ├── Breadcrumb navigation
    │   ├── Zone drag-drop upload
    │   ├── Editeur texte (textarea + CodeMirror light)
    │   └── Viewer (texte, markdown, code syntax highlight)
    │
    ▼
FastAPI — routes_files.py (nouveau)
    │   GET  /api/workspaces/{slug}/files          → list dir
    │   GET  /api/workspaces/{slug}/files/download  → download fichier
    │   GET  /api/workspaces/{slug}/files/content   → lire contenu (JSON)
    │   POST /api/workspaces/{slug}/files/upload    → upload (multipart)
    │   POST /api/workspaces/{slug}/files/mkdir     → créer répertoire
    │   POST /api/workspaces/{slug}/files/save      → sauvegarder édition
    │   POST /api/workspaces/{slug}/files/rename    → renommer / déplacer
    │   POST /api/workspaces/{slug}/files/delete    → supprimer
    │
    ▼
workspace_fs.py (existant, à enrichir)
    │   _safe_resolve()      ← sécurité path traversal (existe déjà)
    │   + list_dir()         ← listing enrichi (taille, date, type)
    │   + read_file_bytes()  ← lecture binaire pour download
    │   + write_file_bytes() ← écriture binaire pour upload
    │   + delete_path()      ← suppression fichier ou dossier
    │   + rename_path()      ← renommer / déplacer
    │   + make_dir()         ← créer répertoire
    │
    ▼
/workspace/{slug}/...  (volume Docker)
```

---

## Bloc A — API File Operations (Backend) [x]

**Fichiers touchés** : `core/workspace_fs.py`, nouveau `core/api/routes_files.py`

### A1. Enrichir `workspace_fs.py`

Ajouter les fonctions filesystem (toutes passent par `_safe_resolve` pour la sécurité) :

| Fonction | Description |
|----------|-------------|
| `list_dir(slug, rel_path)` | Retourne liste de `{name, type, size, modified, extension}` |
| `read_file_bytes(slug, rel_path)` | Retourne bytes du fichier (pour download/preview) |
| `write_file_bytes(slug, rel_path, data)` | Ecrit bytes (upload / save) |
| `delete_path(slug, rel_path)` | Supprime fichier ou dossier (récursif) |
| `rename_path(slug, old_path, new_path)` | Renommer ou déplacer |
| `make_dir(slug, rel_path)` | Créer un répertoire |
| `get_file_info(slug, rel_path)` | Métadonnées d'un fichier (taille, type MIME, date) |

**Sécurité** :
- Toutes les opérations passent par `_safe_resolve()` (anti path traversal)
- Interdire les symlinks qui sortent du workspace
- Limiter la taille d'upload (configurable, défaut 50 MB)
- Interdire certaines extensions si besoin (`.exe`, `.sh` exécutables — configurable)

### A2. Créer `routes_files.py`

Nouveau router FastAPI monté sur `/api/workspaces/{slug}/files`.

| Endpoint | Méthode | Params | Permissions | Description |
|----------|---------|--------|-------------|-------------|
| `/` | GET | `?path=` (défaut `.`) | Tous les membres | Lister répertoire |
| `/content` | GET | `?path=` | Tous les membres | Contenu texte (JSON) |
| `/download` | GET | `?path=` | Tous les membres | Download fichier (StreamingResponse) |
| `/upload` | POST | multipart `files[]`, `path` | member+ | Upload un ou plusieurs fichiers |
| `/mkdir` | POST | JSON `{path}` | member+ | Créer répertoire |
| `/save` | POST | JSON `{path, content}` | member+ | Sauvegarder contenu texte |
| `/rename` | POST | JSON `{old_path, new_path}` | member+ | Renommer / déplacer |
| `/delete` | POST | JSON `{path}` | member+ | Supprimer fichier ou dossier |

**Permissions** :
- `viewer` → lecture seule (list, content, download)
- `member`, `admin`, `owner` → toutes les opérations
- Utiliser `require_workspace_auth()` existant + vérification de rôle

### A3. Monter le router dans `main.py`

```python
from core.api.routes_files import router as files_router
app.include_router(files_router)
```

---

## Bloc B — File Manager UI (Page principale) [x]

**Fichiers touchés** : nouveau `core/templates/dashboard/workspaces/files.html`, `core/static/style.css`

### B1. Route dashboard

Ajouter dans `routes_dashboard.py` :

```
GET /dashboard/w/{slug}/files         → page file manager
GET /dashboard/w/{slug}/files/view    → page viewer inline
GET /dashboard/w/{slug}/files/edit    → page éditeur inline
```

### B2. Template `files.html`

Layout principal du file manager :

```
┌─────────────────────────────────────────────────┐
│  Breadcrumb : workspace / dossier / sous-dossier│
│  [Nouveau dossier] [Upload] [Vue: Liste|Grille] │
├─────────────────────────────────────────────────┤
│                                                  │
│  ☐  📁 documents/          —     2026-03-25     │
│  ☐  📁 scripts/            —     2026-03-24     │
│  ☐  📄 readme.md          2.4K   2026-03-26     │
│  ☐  📄 config.json        1.1K   2026-03-25     │
│  ☐  🖼️ logo.png           45K    2026-03-20     │
│                                                  │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │
│  Glisser des fichiers ici pour les uploader      │
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │
│                                                  │
├─────────────────────────────────────────────────┤
│  Sélection: 2 fichiers  [Supprimer] [Déplacer]  │
└─────────────────────────────────────────────────┘
```

**Fonctionnalités UI** :
- **Breadcrumb** cliquable pour naviguer dans l'arborescence
- **Liste triable** par nom, taille, date
- **Checkboxes** pour sélection multiple
- **Double-clic** sur dossier → naviguer dedans
- **Double-clic** sur fichier → ouvrir le viewer/éditeur
- **Clic droit / menu contextuel** → Renommer, Télécharger, Supprimer, Déplacer
- **Barre d'actions** en haut : Nouveau dossier, Upload, Toggle vue (liste/grille)
- **Barre de sélection** en bas : apparaît quand des fichiers sont cochés
- **Zone drag-drop** : zone visuelle en bas (ou overlay sur toute la page au drag)

### B3. Styles CSS

Ajouter au `style.css` existant les classes :
- `.file-manager` — conteneur principal
- `.file-breadcrumb` — navigation breadcrumb
- `.file-toolbar` — barre d'actions
- `.file-list` — tableau de fichiers
- `.file-row` — ligne fichier (hover, selected states)
- `.file-icon` — icone par type (dossier, texte, image, code, etc.)
- `.file-dropzone` — zone drag-drop (avec état actif)
- `.file-selection-bar` — barre d'actions de sélection
- `.file-context-menu` — menu contextuel clic droit
- `.file-grid` — vue grille alternative

---

## Bloc C — Upload avec Progress Bar [x]

**Fichiers touchés** : nouveau `core/static/js/file-manager.js`

### C1. Drag & Drop

- Event listeners sur la zone dropzone ET sur toute la page (overlay)
- Au `dragenter` → afficher overlay "Déposer les fichiers ici"
- Au `drop` → collecter les fichiers, lancer l'upload
- Support du bouton "Upload" classique (input file hidden)

### C2. Upload avec XHR (pour progress)

```javascript
function uploadFiles(files, targetPath) {
    const formData = new FormData();
    formData.append('path', targetPath);
    files.forEach(f => formData.append('files', f));

    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = (e) => {
        const pct = Math.round((e.loaded / e.total) * 100);
        updateProgressBar(pct);
    };
    xhr.onload = () => refreshFileList();
    xhr.open('POST', `/api/workspaces/${slug}/files/upload`);
    xhr.send(formData);
}
```

### C3. UI Progress

- Barre de progression animée (style cyberpunk du projet)
- Nom du fichier en cours
- Upload multiple : progress global + liste des fichiers
- Toast de confirmation quand terminé

---

## Bloc D — Viewer de fichiers [x]

**Fichiers touchés** : `files.html` (modal ou panel latéral), `file-manager.js`

### D1. Détection du type de fichier

Par extension :
| Type | Extensions | Action |
|------|-----------|--------|
| Texte | `.txt`, `.log`, `.csv` | Afficher brut |
| Markdown | `.md` | Rendu HTML (marked.js) |
| Code | `.py`, `.js`, `.json`, `.yaml`, `.html`, `.css`, `.sh`, `.sql` | Syntax highlighting (highlight.js) |
| Image | `.png`, `.jpg`, `.gif`, `.svg`, `.webp` | Afficher `<img>` |
| PDF | `.pdf` | `<iframe>` ou lien download |
| Autre | `*` | Proposer le téléchargement |

### D2. Panel viewer

- Ouvre dans un panel/modal sur la même page (pas de navigation)
- Header : nom du fichier, taille, boutons [Editer] [Télécharger] [Fermer]
- Corps : contenu rendu selon le type
- Pour les gros fichiers texte (>100KB) : charger les premiers 5000 lignes + "Charger la suite"

### D3. Librairies JS (CDN)

- **highlight.js** — coloration syntaxique (thème sombre)
- **marked.js** — rendu Markdown
- Pas de build step, chargement CDN direct

---

## Bloc E — Editeur de fichiers texte [x]

**Fichiers touchés** : `files.html`, `file-manager.js`

### E1. Editeur simple

- `<textarea>` plein écran avec numéros de ligne (CSS)
- Font monospace (Share Tech Mono du projet)
- Bouton [Sauvegarder] → `POST /files/save`
- Bouton [Annuler] → retour au viewer
- Indicateur "modifié" (dirty flag)
- Raccourci `Ctrl+S` pour sauvegarder

### E2. Améliorations (optionnel, phase 2)

- Tab → insère espaces (pas de changement de focus)
- Indentation auto
- Rechercher/Remplacer (Ctrl+F)
- Coloration syntaxique en édition (CodeMirror si besoin)

---

## Bloc F — Points d'accès au File Manager [x]

**Fichiers touchés** : templates existants

### F1. Depuis la page manage du workspace

Ajouter un onglet ou section "Fichiers" dans `manage.html` :
```html
<a href="/dashboard/w/{{ ws.slug }}/files" class="btn btn-ghost">
    📁 Fichiers du workspace
</a>
```

### F2. Depuis la liste des workspaces

Ajouter une icône dans `list.html` (colonne actions) :
```html
<a href="/dashboard/w/{{ ws.slug }}/files" title="Fichiers">📁</a>
```

### F3. Depuis le chat

Ajouter un bouton dans la sidebar ou toolbar du chat :
```html
<a href="/dashboard/w/{{ ws.slug }}/files" target="_blank">📁 Fichiers</a>
```

### F4. Sidebar navigation

Ajouter un lien "Fichiers" dans la sidebar du workspace (entre "Conversations" et "Tâches") dans `base.html`.

---

## Bloc G — Sécurité & Permissions [x]

### G1. Sécurité filesystem

- [x] `_safe_resolve()` existe déjà — anti path traversal
- [x] Valider qu'aucun symlink ne sort du workspace (`_safe_resolve` via `os.path.realpath`)
- [x] Limite taille upload (config `MAX_UPLOAD_SIZE`, défaut 50MB)
- [ ] Rate limiting sur les uploads (optionnel — reporté)
- [x] Sanitize les noms de fichiers (`_sanitize_name` dans routes_files.py)

### G2. Permissions par rôle

| Action | viewer | member | admin | owner |
|--------|--------|--------|-------|-------|
| Lister fichiers | ✅ | ✅ | ✅ | ✅ |
| Lire / Télécharger | ✅ | ✅ | ✅ | ✅ |
| Previewer | ✅ | ✅ | ✅ | ✅ |
| Créer dossier | ❌ | ✅ | ✅ | ✅ |
| Upload fichier | ❌ | ✅ | ✅ | ✅ |
| Editer fichier | ❌ | ✅ | ✅ | ✅ |
| Renommer / Déplacer | ❌ | ✅ | ✅ | ✅ |
| Supprimer | ❌ | ✅ | ✅ | ✅ |

Les éléments UI d'écriture sont masqués pour les viewers.

---

## Ordre d'exécution

```
Bloc A  →  API backend + workspace_fs enrichi
   │
Bloc B  →  UI file manager (listing, navigation, CSS)
   │
Bloc C  →  Upload drag-drop + progress bar
   │
Bloc D  →  Viewer (texte, markdown, code, images)
   │
Bloc E  →  Editeur de fichiers texte
   │
Bloc F  →  Points d'accès (manage, list, chat, sidebar)
   │
Bloc G  →  Hardening sécurité + vérification permissions
```

**Chaque bloc est testable indépendamment.** Le bloc A est le fondement, B donne l'UI de base, puis C/D/E ajoutent les fonctionnalités, F les intégrations, G le hardening.

---

## Fichiers à créer

| Fichier | Description |
|---------|-------------|
| `core/api/routes_files.py` | Router FastAPI pour les opérations fichiers |
| `core/templates/dashboard/workspaces/files.html` | Template file manager |
| `core/static/js/file-manager.js` | JavaScript du file manager |

## Fichiers à modifier

| Fichier | Modification |
|---------|-------------|
| `core/workspace_fs.py` | Ajouter fonctions FS enrichies |
| `core/main.py` | Monter le nouveau router |
| `core/api/routes_dashboard.py` | Ajouter routes dashboard fichiers |
| `core/templates/base.html` | Ajouter lien "Fichiers" dans sidebar |
| `core/templates/dashboard/workspaces/list.html` | Ajouter icône fichiers |
| `core/templates/dashboard/workspaces/manage.html` | Ajouter bouton fichiers |
| `core/static/style.css` | Styles file manager |

---

## Estimation par bloc

| Bloc | Complexité | Priorité |
|------|-----------|----------|
| A — API Backend | Moyenne | P0 — Fondation |
| B — UI File Manager | Haute | P0 — Fondation |
| C — Upload + Progress | Moyenne | P1 — Essentiel |
| D — Viewer | Moyenne | P1 — Essentiel |
| E — Editeur | Faible-Moyenne | P2 — Important |
| F — Points d'accès | Faible | P1 — Essentiel |
| G — Sécurité | Faible | P0 — Continu |
