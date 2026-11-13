/* HiveFlows — File Manager (Sprint File: Blocs C, D, E) */
/* global hljs, marked */

const FM = (() => {
  let _slug = '';
  let _wsUuid = '';
  let _canWrite = false;
  let _currentPath = '.';
  let _editorPath = null;
  let _editorDirty = false;
  let _viewerPath = null;
  let _viewerExt = '';
  let _renameOldPath = null;
  let _movePath = null;
  let _moveDestPath = '.';
  let _selected = new Set();

  // ── Tools state ───────────────────────────────────────────────────────────
  let _toolMenuEl = null;
  let _toolsCache = {};       // ext → tools[]
  let _popoverTool = null;    // HumanTool currently in popover
  let _popoverPath = '';
  let _fabricPath = '';
  let _fabricExt = '';
  let _fabricPatterns = null; // cached patterns list
  let _fabricSelected = null; // { key, description }
  let _fabricAcIndex = -1;    // autocomplete keyboard index
  let _pendingTaskToasts = {};// taskId → { name, toastText }
  let _activitySource = null;

  // ── INIT ──────────────────────────────────────────────────────────────────

  function init(slug, canWrite, wsUuid) {
    _slug = slug;
    _canWrite = canWrite === true || canWrite === 'true';
    _wsUuid = wsUuid || '';

    // Restore path from URL on load/refresh (e.g. ?path=folder/sub)
    const urlPath = new URLSearchParams(window.location.search).get('path') || '.';
    loadDir(urlPath, { replace: true });

    // Browser back/forward
    window.addEventListener('popstate', (e) => {
      const p = (e.state && e.state.fmPath) ? e.state.fmPath : '.';
      loadDir(p, { pushState: false });
    });

    if (_canWrite) _initDragDrop();
    if (_wsUuid) _connectActivityStream();
  }

  // ── API HELPERS ───────────────────────────────────────────────────────────

  async function _api(method, endpoint, body) {
    const opts = { method, headers: {} };
    if (body && !(body instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    } else if (body) {
      opts.body = body;
    }
    const resp = await fetch(`/api/w/${_slug}${endpoint}`, opts);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    return resp.json();
  }

  // ── DIRECTORY LISTING ─────────────────────────────────────────────────────

  async function loadDir(path, opts = {}) {
    _currentPath = path || '.';
    _selected.clear();
    _updateSelectionBar();

    // Update browser URL so refreshing or sharing the link lands on the right folder
    const urlPath = _currentPath === '.' ? '' : _currentPath;
    const newUrl = urlPath
      ? `${window.location.pathname}?path=${encodeURIComponent(urlPath)}`
      : window.location.pathname;
    if (opts.replace) {
      history.replaceState({ fmPath: _currentPath }, '', newUrl);
    } else if (opts.pushState !== false) {
      history.pushState({ fmPath: _currentPath }, '', newUrl);
    }

    const tbody = document.getElementById('fm-tbody');
    tbody.innerHTML = '<tr><td colspan="5" class="fm-loading">Chargement…</td></tr>';
    try {
      const data = await _api('GET', `/files?path=${encodeURIComponent(_currentPath)}`);
      _renderBreadcrumb(data.breadcrumb);
      _renderEntries(data.entries);
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" class="fm-error">Erreur: ${e.message}</td></tr>`;
    }
  }

  function _renderBreadcrumb(crumbs) {
    const el = document.getElementById('fm-breadcrumb');
    el.innerHTML = crumbs.map((c, i) => {
      const isLast = i === crumbs.length - 1;
      return `<span class="fm-crumb${isLast ? ' active' : ''}"
        ${!isLast ? `onclick="FM.loadDir('${_jsSafe(c.path)}')" style="cursor:pointer;"` : ''}
      >${i === 0 ? '<i class="mdi mdi-home"></i> ' : ''}${_esc(c.name)}</span>`;
    }).join('<span class="fm-crumb-sep">/</span>');
  }

  function _renderEntries(entries) {
    const tbody = document.getElementById('fm-tbody');
    if (!entries.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="fm-empty">Répertoire vide</td></tr>';
      return;
    }
    tbody.innerHTML = entries.map(e => {
      const icon = e.type === 'dir' ? '<i class="mdi mdi-folder"></i>' : _fileIcon(e.extension);
      const size = e.size !== null ? _fmtSize(e.size) : '—';
      const modified = e.modified ? e.modified.slice(0, 16).replace('T', ' ') : '—';
      const escapedPath = _esc(e.path);
      const escapedName = _esc(e.name);
      const safePath = _jsSafe(e.path);
      const rowClick = e.type === 'dir'
        ? `FM.loadDir('${safePath}')`
        : `FM.openViewer('${safePath}')`;
      // Bouton outils "..." pour les fichiers (D10)
      const fileExt = e.extension ? '.' + e.extension : '';
      const toolsBtn = e.type !== 'dir' ? `
        <span class="fm-action-btn fm-tools-btn" onclick="event.stopPropagation();FM.openToolMenu('${safePath}','${_esc(fileExt)}',this)" title="Outils"><i class="mdi mdi-dots-horizontal"></i></span>
      ` : '';
      const actions = _canWrite ? `
        ${toolsBtn}
        <span class="fm-action-btn" onclick="event.stopPropagation();FM.openRename('${safePath}','${escapedName}')" title="Renommer"><i class="mdi mdi-pencil"></i></span>
        <span class="fm-action-btn" onclick="event.stopPropagation();FM.openMove('${safePath}','${escapedName}')" title="Déplacer"><i class="mdi mdi-folder-move-outline"></i></span>
        <a class="fm-action-btn" href="/api/w/${_slug}/files/download?path=${encodeURIComponent(e.path)}" onclick="event.stopPropagation()" title="Télécharger" download><i class="mdi mdi-download"></i></a>
        <span class="fm-action-btn danger" onclick="event.stopPropagation();FM.deleteSingle('${safePath}')" title="Supprimer"><i class="mdi mdi-delete-outline"></i></span>
      ` : `
        ${toolsBtn}
        <a class="fm-action-btn" href="/api/w/${_slug}/files/download?path=${encodeURIComponent(e.path)}" onclick="event.stopPropagation()" title="Télécharger" download><i class="mdi mdi-download"></i></a>
      `;
      return `<tr class="fm-row" data-path="${escapedPath}" onclick="${rowClick}">
        <td onclick="event.stopPropagation()">
          <input type="checkbox" class="fm-checkbox" onchange="FM.toggleSelect('${safePath}', this.checked)" />
        </td>
        <td><span class="fm-icon">${icon}</span> <span class="fm-name">${escapedName}</span></td>
        <td class="mono dim" style="font-size:0.78rem;">${size}</td>
        <td class="mono dim" style="font-size:0.78rem;">${modified}</td>
        <td class="fm-actions" onclick="event.stopPropagation()">${actions}</td>
      </tr>`;
    }).join('');
  }

  // ── VIEWER (Bloc D) ────────────────────────────────────────────────────────

  async function openViewer(path) {
    _viewerPath = path;
    _viewerExt = '.' + (path.split('.').pop() || '').toLowerCase();
    const modal = document.getElementById('fm-viewer-modal');
    const body = document.getElementById('fm-viewer-body');
    const title = document.getElementById('fm-viewer-title');
    const sizeEl = document.getElementById('fm-viewer-size');
    const dlBtn = document.getElementById('fm-viewer-download-btn');
    const editBtn = document.getElementById('fm-viewer-edit-btn');

    title.textContent = _basename(path);
    body.innerHTML = '<div class="fm-loading">Chargement…</div>';
    modal.style.display = 'flex';
    dlBtn.href = `/api/w/${_slug}/files/download?path=${encodeURIComponent(path)}`;

    // Short-circuit for video/audio — stream directly without loading bytes
    const _ext = path.split('.').pop().toLowerCase();
    const _VIDEO_EXTS = ['mp4', 'webm', 'ogv', 'mov', 'avi', 'mkv', 'm4v'];
    const _AUDIO_EXTS = ['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'opus'];
    const streamUrl = `/api/w/${_slug}/files/stream?path=${encodeURIComponent(path)}`;
    if (_VIDEO_EXTS.includes(_ext)) {
      if (editBtn) editBtn.style.display = 'none';
      body.innerHTML = `<div style="padding:16px;text-align:center;">
        <video controls style="max-width:100%;max-height:70vh;border-radius:6px;background:#000;">
          <source src="${streamUrl}" type="video/${_ext === 'mov' ? 'mp4' : _ext}">
          <p class="dim">Votre navigateur ne supporte pas la lecture de ce format.</p>
        </video>
      </div>`;
      return;
    }
    if (_AUDIO_EXTS.includes(_ext)) {
      if (editBtn) editBtn.style.display = 'none';
      body.innerHTML = `<div style="padding:32px;text-align:center;">
        <audio controls style="width:100%;max-width:480px;">
          <source src="${streamUrl}" type="audio/${_ext}">
          <p class="dim">Votre navigateur ne supporte pas la lecture de ce format.</p>
        </audio>
      </div>`;
      return;
    }

    try {
      const data = await _api('GET', `/files/content?path=${encodeURIComponent(path)}`);
      sizeEl.textContent = data.size ? _fmtSize(data.size) : '';

      if (editBtn) {
        editBtn.style.display = data.encoding === 'utf-8' ? '' : 'none';
        editBtn._path = path;
        editBtn._content = data.content;
      }

      // D13 — bouton outils dans le viewer
      const viewerToolsBtn = document.getElementById('fm-viewer-tools-btn');
      if (viewerToolsBtn) {
        viewerToolsBtn.style.display = data.encoding === 'utf-8' ? '' : 'none';
      }

      if (data.encoding === 'binary') {
        const ext = data.extension || '';
        if (['png','jpg','jpeg','gif','svg','webp','bmp','ico'].includes(ext)) {
          body.innerHTML = `<div style="text-align:center;padding:16px;">
            <img src="/api/w/${_slug}/files/download?path=${encodeURIComponent(path)}"
              style="max-width:100%;max-height:70vh;border-radius:4px;" />
          </div>`;
        } else if (ext === 'pdf') {
          body.innerHTML = `<div style="text-align:center;padding:24px;">
            <p class="dim" style="margin-bottom:12px;">Aperçu PDF non disponible dans le navigateur.</p>
            <a class="btn btn-primary btn-sm" href="${dlBtn.href}" download><i class="mdi mdi-download"></i> Télécharger le PDF</a>
          </div>`;
        } else {
          body.innerHTML = `<div style="text-align:center;padding:24px;">
            <p class="dim">Fichier binaire — aperçu non disponible.</p>
            <a class="btn btn-primary btn-sm" href="${dlBtn.href}" download style="margin-top:12px;"><i class="mdi mdi-download"></i> Télécharger</a>
          </div>`;
        }
        return;
      }

      const ext = data.extension || '';
      if (ext === 'md' || ext === 'markdown') {
        body.innerHTML = `<div class="fm-markdown">${marked.parse(data.content || '')}</div>`;
      } else if (_isCode(ext)) {
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.className = `language-${ext}`;
        code.textContent = data.content || '';
        pre.appendChild(code);
        pre.style.cssText = 'margin:0;border-radius:0;background:transparent;';
        body.innerHTML = '';
        body.appendChild(pre);
        hljs.highlightElement(code);
      } else {
        body.innerHTML = `<pre class="fm-plain-text">${_esc(data.content || '')}</pre>`;
      }
    } catch (e) {
      body.innerHTML = `<div class="fm-error">Erreur: ${e.message}</div>`;
    }
  }

  function closeViewer() {
    document.getElementById('fm-viewer-modal').style.display = 'none';
    _viewerPath = null;
  }

  // ── EDITOR (Bloc E) ────────────────────────────────────────────────────────

  function openEditor() {
    const editBtn = document.getElementById('fm-viewer-edit-btn');
    const path = editBtn._path || _viewerPath;
    const content = editBtn._content;
    closeViewer();
    _openEditorWith(path, content);
  }

  function _openEditorWith(path, content) {
    _editorPath = path;
    _editorDirty = false;
    document.getElementById('fm-editor-title').textContent = _basename(path);
    document.getElementById('fm-editor-ta').value = content || '';
    document.getElementById('fm-dirty').style.display = 'none';
    document.getElementById('fm-editor-modal').style.display = 'flex';
    document.getElementById('fm-editor-ta').focus();
  }

  function markDirty() {
    if (!_editorDirty) {
      _editorDirty = true;
      document.getElementById('fm-dirty').style.display = '';
    }
  }

  function editorKeydown(e) {
    if (e.key === 'Tab') {
      e.preventDefault();
      const ta = e.target;
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      ta.value = ta.value.slice(0, start) + '  ' + ta.value.slice(end);
      ta.selectionStart = ta.selectionEnd = start + 2;
      markDirty();
    } else if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      saveFile();
    }
  }

  async function saveFile() {
    if (!_editorPath) return;
    const content = document.getElementById('fm-editor-ta').value;
    const btn = document.getElementById('fm-save-btn');
    btn.disabled = true;
    btn.innerHTML = '<i class="mdi mdi-content-save"></i> Sauvegarde…';
    try {
      await _api('POST', '/files/save', { path: _editorPath, content });
      _editorDirty = false;
      document.getElementById('fm-dirty').style.display = 'none';
      _toast('Fichier sauvegardé.', 'success');
    } catch (e) {
      _toast(`Erreur: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i class="mdi mdi-content-save"></i> Sauvegarder';
    }
  }

  async function confirmCloseEditor() {
    if (_editorDirty && !await showConfirm('Fermer sans sauvegarder', 'Les modifications non sauvegardées seront perdues.', { confirmText: 'Fermer quand même' })) return;
    closeEditor();
  }

  function closeEditor() {
    document.getElementById('fm-editor-modal').style.display = 'none';
    _editorPath = null;
    _editorDirty = false;
  }

  // ── UPLOAD (Bloc C) ────────────────────────────────────────────────────────

  function triggerUpload() {
    document.getElementById('fm-upload-input').click();
  }

  function uploadFromInput(input) {
    if (input.files.length) {
      _uploadFiles(Array.from(input.files));
      input.value = '';
    }
  }

  function _uploadFiles(files) {
    const overlay = document.getElementById('fm-progress-overlay');
    const bar = document.getElementById('fm-progress-bar');
    const pct = document.getElementById('fm-progress-pct');
    const title = document.getElementById('fm-progress-title');
    const filesList = document.getElementById('fm-progress-files');

    overlay.style.display = 'flex';
    bar.style.width = '0%';
    pct.textContent = '0%';
    title.textContent = `Upload de ${files.length} fichier(s)…`;
    filesList.textContent = files.map(f => f.name).join(', ');

    const form = new FormData();
    form.append('path', _currentPath === '.' ? '.' : _currentPath);
    files.forEach(f => form.append('files', f));

    const xhr = new XMLHttpRequest();
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const p = Math.round((e.loaded / e.total) * 100);
        bar.style.width = p + '%';
        pct.textContent = p + '%';
      }
    };
    xhr.onload = () => {
      overlay.style.display = 'none';
      if (xhr.status >= 200 && xhr.status < 300) {
        _toast('Upload réussi !', 'success');
        loadDir(_currentPath);
      } else {
        let msg = 'Erreur upload';
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (_) {}
        _toast(msg, 'error');
      }
    };
    xhr.onerror = () => {
      overlay.style.display = 'none';
      _toast('Erreur réseau lors de l\'upload.', 'error');
    };
    xhr.open('POST', `/api/w/${_slug}/files/upload`);
    xhr.send(form);
  }

  // ── DRAG & DROP ───────────────────────────────────────────────────────────

  function _initDragDrop() {
    let dragCount = 0;
    const overlay = document.getElementById('fm-drag-overlay');
    if (!overlay) return;

    document.addEventListener('dragenter', (e) => {
      if (e.dataTransfer.types.includes('Files')) {
        dragCount++;
        overlay.style.display = 'flex';
      }
    });
    document.addEventListener('dragleave', () => {
      dragCount--;
      if (dragCount <= 0) { dragCount = 0; overlay.style.display = 'none'; }
    });
    document.addEventListener('dragover', (e) => e.preventDefault());
    document.addEventListener('drop', (e) => {
      e.preventDefault();
      dragCount = 0;
      overlay.style.display = 'none';
      const files = Array.from(e.dataTransfer.files);
      if (files.length) _uploadFiles(files);
    });

    const dz = document.getElementById('fm-dropzone');
    if (dz) {
      dz.addEventListener('click', triggerUpload);
      dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('active'); });
      dz.addEventListener('dragleave', () => dz.classList.remove('active'));
      dz.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        dz.classList.remove('active');
        dragCount = 0;
        overlay.style.display = 'none';
        const files = Array.from(e.dataTransfer.files);
        if (files.length) _uploadFiles(files);
      });
    }
  }

  // ── DELETE ────────────────────────────────────────────────────────────────

  async function deleteSingle(path) {
    if (!await showConfirm('Supprimer', `"${_basename(path)}" sera supprimé définitivement.`, { danger: true })) return;
    try {
      await _api('POST', '/files/delete', { path });
      _toast('Supprimé.', 'success');
      loadDir(_currentPath);
    } catch (e) {
      _toast(`Erreur: ${e.message}`, 'error');
    }
  }

  async function deleteSelected() {
    if (!_selected.size) return;
    if (!await showConfirm('Supprimer', `${_selected.size} élément(s) seront supprimés définitivement.`, { danger: true })) return;
    let errors = 0;
    for (const path of _selected) {
      try {
        await _api('POST', '/files/delete', { path });
      } catch (_) { errors++; }
    }
    _toast(errors ? `Supprimé avec ${errors} erreur(s).` : 'Supprimé.', errors ? 'error' : 'success');
    loadDir(_currentPath);
  }

  // ── RENAME ────────────────────────────────────────────────────────────────

  function openRename(path, name) {
    _renameOldPath = path;
    document.getElementById('fm-rename-input').value = name;
    document.getElementById('fm-rename-modal').style.display = 'flex';
    setTimeout(() => document.getElementById('fm-rename-input').select(), 50);
  }

  function closeRename() {
    document.getElementById('fm-rename-modal').style.display = 'none';
    _renameOldPath = null;
  }

  async function submitRename() {
    const newName = document.getElementById('fm-rename-input').value.trim();
    if (!newName || !_renameOldPath) return;
    const dir = _renameOldPath.includes('/') ? _renameOldPath.slice(0, _renameOldPath.lastIndexOf('/') + 1) : '';
    const newPath = dir + newName;
    try {
      await _api('POST', '/files/rename', { old_path: _renameOldPath, new_path: newPath });
      closeRename();
      _toast('Renommé.', 'success');
      loadDir(_currentPath);
    } catch (e) {
      _toast(`Erreur: ${e.message}`, 'error');
    }
  }

  // ── MOVE — treeview ───────────────────────────────────────────────────────

  function openMove(path, name) {
    _movePath = path;
    _moveDestPath = '.';
    document.getElementById('fm-move-filename').textContent = name;
    _updateMoveDestDisplay();
    _treeBuild();
    document.getElementById('fm-move-modal').style.display = 'flex';
  }

  function closeMove() {
    document.getElementById('fm-move-modal').style.display = 'none';
    _movePath = null;
  }

  async function submitMove() {
    if (!_movePath) return;
    const name = _basename(_movePath);
    const newPath = _moveDestPath === '.' ? name : `${_moveDestPath}/${name}`;
    if (newPath === _movePath) { closeMove(); return; }
    try {
      await _api('POST', '/files/rename', { old_path: _movePath, new_path: newPath });
      closeMove();
      _toast('Déplacé.', 'success');
      loadDir(_currentPath);
    } catch (e) {
      _toast(`Erreur: ${e.message}`, 'error');
    }
  }

  function _updateMoveDestDisplay() {
    const el = document.getElementById('fm-move-dest-display');
    if (el) el.textContent = _moveDestPath === '.' ? '/ (racine)' : `/ ${_moveDestPath}`;
  }

  function _treeBuild() {
    const container = document.getElementById('fm-move-tree');
    container.innerHTML = '';
    const rootNode = _treeCreateNode('.', 'root', true);
    container.appendChild(rootNode);
    // Auto-select and expand root
    const rootItem = rootNode.querySelector('.fm-tree-item');
    const rootToggle = rootNode.querySelector('.fm-tree-toggle');
    const rootChildren = rootNode.querySelector('.fm-tree-children');
    rootItem.classList.add('selected');
    rootToggle.classList.add('open');
    rootChildren.style.display = 'block';
    _treeLoadChildren('.', rootChildren, rootToggle);
  }

  function _treeCreateNode(path, name, isRoot) {
    const node = document.createElement('div');
    node.className = 'fm-tree-node';

    const item = document.createElement('div');
    item.className = 'fm-tree-item';

    const toggle = document.createElement('span');
    toggle.className = 'fm-tree-toggle';
    toggle.innerHTML = '<i class="mdi mdi-chevron-right"></i>';

    const children = document.createElement('div');
    children.className = 'fm-tree-children';
    children.style.display = 'none';

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'fm-move-dest';
    radio.className = 'fm-tree-radio';
    if (isRoot) radio.checked = true;

    item.onclick = (e) => { e.stopPropagation(); _treeSelect(path, item, radio); };
    toggle.onclick = (e) => { e.stopPropagation(); _treeExpand(path, toggle, children); };
    radio.onclick = (e) => { e.stopPropagation(); _treeSelect(path, item, radio); };

    const icon = document.createElement('i');
    icon.className = isRoot ? 'mdi mdi-home-outline' : 'mdi mdi-folder';

    const label = document.createElement('span');
    label.textContent = isRoot ? 'root' : name;

    item.appendChild(toggle);
    item.appendChild(radio);
    item.appendChild(icon);
    item.appendChild(label);
    node.appendChild(item);
    node.appendChild(children);
    return node;
  }

  function _treeSelect(path, itemEl, radioEl) {
    document.getElementById('fm-move-tree')
      .querySelectorAll('.fm-tree-item.selected')
      .forEach(el => el.classList.remove('selected'));
    itemEl.classList.add('selected');
    if (radioEl) radioEl.checked = true;
    _moveDestPath = path;
    _updateMoveDestDisplay();
  }

  async function _treeExpand(path, toggleEl, childrenEl) {
    if (toggleEl.classList.contains('open')) {
      childrenEl.style.display = 'none';
      toggleEl.classList.remove('open');
    } else {
      toggleEl.classList.add('open');
      childrenEl.style.display = 'block';
      if (!childrenEl.dataset.loaded) {
        await _treeLoadChildren(path, childrenEl, toggleEl);
      }
    }
  }

  async function _treeLoadChildren(path, childrenEl, toggleEl) {
    childrenEl.innerHTML = '<div class="fm-tree-loading"><i class="mdi mdi-loading mdi-spin"></i></div>';
    try {
      const data = await _api('GET', `/files?path=${encodeURIComponent(path)}`);
      const dirs = data.entries.filter(e => e.type === 'dir');
      childrenEl.innerHTML = '';
      if (dirs.length === 0) {
        toggleEl.classList.add('leaf');
      } else {
        dirs.forEach(dir => {
          childrenEl.appendChild(_treeCreateNode(dir.path, dir.name, false));
        });
      }
      childrenEl.dataset.loaded = '1';
    } catch (e) {
      childrenEl.innerHTML = '<div class="fm-tree-loading" style="color:#ff4466;">Erreur de chargement</div>';
    }
  }

  // ── MKDIR ─────────────────────────────────────────────────────────────────

  function mkdirPrompt() {
    document.getElementById('fm-mkdir-input').value = '';
    document.getElementById('fm-mkdir-modal').style.display = 'flex';
    setTimeout(() => document.getElementById('fm-mkdir-input').focus(), 50);
  }

  function closeMkdir() {
    document.getElementById('fm-mkdir-modal').style.display = 'none';
  }

  async function submitMkdir() {
    const name = document.getElementById('fm-mkdir-input').value.trim();
    if (!name) return;
    const fullPath = _currentPath === '.' ? name : `${_currentPath}/${name}`;
    try {
      await _api('POST', '/files/mkdir', { path: fullPath });
      closeMkdir();
      _toast('Dossier créé.', 'success');
      loadDir(_currentPath);
    } catch (e) {
      _toast(`Erreur: ${e.message}`, 'error');
    }
  }

  // ── NEW FILE ──────────────────────────────────────────────────────────────

  function newFilePrompt() {
    document.getElementById('fm-newfile-input').value = '';
    document.getElementById('fm-newfile-modal').style.display = 'flex';
    setTimeout(() => document.getElementById('fm-newfile-input').focus(), 50);
  }

  function closeNewFile() {
    document.getElementById('fm-newfile-modal').style.display = 'none';
  }

  async function submitNewFile() {
    const name = document.getElementById('fm-newfile-input').value.trim();
    if (!name) return;
    const fullPath = _currentPath === '.' ? name : `${_currentPath}/${name}`;
    try {
      await _api('POST', '/files/save', { path: fullPath, content: '' });
      closeNewFile();
      _toast('Fichier créé.', 'success');
      loadDir(_currentPath);
    } catch (e) {
      _toast(`Erreur: ${e.message}`, 'error');
    }
  }

  // ── SELECTION ─────────────────────────────────────────────────────────────

  function toggleSelect(path, checked) {
    if (checked) _selected.add(path); else _selected.delete(path);
    _updateSelectionBar();
  }

  function _updateSelectionBar() {
    const bar = document.getElementById('fm-selection-bar');
    const count = document.getElementById('fm-selection-count');
    if (_selected.size > 0) {
      bar.style.display = 'flex';
      count.textContent = `${_selected.size} élément(s) sélectionné(s)`;
    } else {
      bar.style.display = 'none';
    }
  }

  // ── HELPERS ───────────────────────────────────────────────────────────────

  function _basename(path) {
    return path.split('/').pop() || path;
  }

  function _fmtSize(bytes) {
    if (bytes < 1024) return bytes + ' o';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' Ko';
    return (bytes / 1024 / 1024).toFixed(1) + ' Mo';
  }

  function _fileIcon(ext) {
    const icons = {
      py: 'mdi-language-python',
      js: 'mdi-language-javascript', ts: 'mdi-language-typescript',
      jsx: 'mdi-language-javascript', tsx: 'mdi-language-typescript',
      json: 'mdi-code-json',
      yaml: 'mdi-file-code-outline', yml: 'mdi-file-code-outline',
      md: 'mdi-language-markdown-outline', markdown: 'mdi-language-markdown-outline',
      txt: 'mdi-file-document-outline', log: 'mdi-file-document-outline',
      csv: 'mdi-file-delimited-outline',
      html: 'mdi-language-html5', htm: 'mdi-language-html5',
      css: 'mdi-language-css3',
      sh: 'mdi-console', bash: 'mdi-console',
      sql: 'mdi-database',
      xml: 'mdi-file-xml-box',
      pdf: 'mdi-file-pdf-box',
      png: 'mdi-file-image-outline', jpg: 'mdi-file-image-outline',
      jpeg: 'mdi-file-image-outline', gif: 'mdi-file-image-outline',
      svg: 'mdi-file-image-outline', webp: 'mdi-file-image-outline',
      mp4: 'mdi-file-video-outline', webm: 'mdi-file-video-outline',
      mov: 'mdi-file-video-outline', avi: 'mdi-file-video-outline',
      mkv: 'mdi-file-video-outline', m4v: 'mdi-file-video-outline',
      ogv: 'mdi-file-video-outline',
      mp3: 'mdi-file-music-outline', wav: 'mdi-file-music-outline',
      flac: 'mdi-file-music-outline', aac: 'mdi-file-music-outline',
      m4a: 'mdi-file-music-outline', opus: 'mdi-file-music-outline',
      ogg: 'mdi-file-music-outline',
      zip: 'mdi-zip-box-outline', tar: 'mdi-zip-box-outline', gz: 'mdi-zip-box-outline',
      go: 'mdi-language-go', rs: 'mdi-file-code-outline',
      java: 'mdi-language-java', cpp: 'mdi-language-cpp', c: 'mdi-language-c',
      rb: 'mdi-language-ruby', php: 'mdi-language-php',
      toml: 'mdi-file-cog-outline', ini: 'mdi-file-cog-outline',
      cfg: 'mdi-file-cog-outline', env: 'mdi-file-cog-outline',
      tf: 'mdi-file-code-outline', hcl: 'mdi-file-code-outline',
    };
    const icon = icons[ext] || 'mdi-file-outline';
    return `<i class="mdi ${icon}"></i>`;
  }

  function _isCode(ext) {
    return ['py','js','ts','jsx','tsx','json','yaml','yml','html','htm','css',
            'sh','bash','sql','xml','toml','ini','cfg','conf','env','rs','go',
            'java','c','cpp','h','hpp','rb','php','r','tf','hcl','proto',
            'graphql','dockerfile'].includes(ext);
  }

  function _esc(str) {
    return String(str)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  // Escape a path for use in a single-quoted JS string inside an HTML attribute.
  // Backslashes first, then apostrophes, so "L'Iliade" → "L\'Iliade".
  function _jsSafe(str) {
    return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  }

  // ── ACTIVITY SSE ──────────────────────────────────────────────────────────

  function _connectActivityStream() {
    if (_activitySource) _activitySource.close();
    const url = `/dashboard/w/${_wsUuid}/activity/stream`;
    _activitySource = new EventSource(url);
    _activitySource.onmessage = (e) => {
      try { _handleActivityEvent(JSON.parse(e.data)); } catch (_) {}
    };
    ['task_done', 'task_error', 'task_cancelled'].forEach(evtType => {
      _activitySource.addEventListener(evtType, (e) => {
        try { _handleActivityEvent(JSON.parse(e.data)); } catch (_) {}
      });
    });
    _activitySource.onerror = () => {
      // Reconnect silently
      setTimeout(_connectActivityStream, 5000);
    };
  }

  function _handleActivityEvent(evt) {
    const taskId = evt.task_id;
    if (!taskId || !_pendingTaskToasts[taskId]) return;
    const { name } = _pendingTaskToasts[taskId];
    const data = evt.data || {};

    if (evt.type === 'task_done') {
      const outputFile = data.output_file;
      if (outputFile) {
        _toastLink(`✓ ${name} — terminé`, outputFile, 'success');
      } else {
        _toast(`✓ ${name} — terminé`, 'success');
      }
      delete _pendingTaskToasts[taskId];
    } else if (evt.type === 'task_error') {
      _toast(`✗ ${name} — erreur: ${data.error || '?'}`, 'error');
      delete _pendingTaskToasts[taskId];
    } else if (evt.type === 'task_cancelled') {
      _toast(`${name} — annulé`, 'warning');
      delete _pendingTaskToasts[taskId];
    }
  }

  // ── TOOL MENU (D10-D12) ───────────────────────────────────────────────────

  async function openToolMenu(path, ext, anchorEl) {
    _closeToolMenu();
    closeToolPopover();
    let tools;
    try {
      tools = await _fetchTools(ext);
    } catch (_) { tools = []; }

    const menu = _buildToolMenu(tools, path, ext);
    document.body.appendChild(menu);
    _toolMenuEl = menu;
    _positionMenu(menu, anchorEl);

    // Close on next click outside
    setTimeout(() => {
      document.addEventListener('click', _closeToolMenu, { once: true });
    }, 0);
  }

  function openViewerToolMenu(anchorEl) {
    if (_viewerPath) openToolMenu(_viewerPath, _viewerExt, anchorEl);
  }

  function _closeToolMenu() {
    if (_toolMenuEl) {
      _toolMenuEl.remove();
      _toolMenuEl = null;
    }
  }

  async function _fetchTools(ext) {
    const key = (ext || '').toLowerCase();
    if (_toolsCache[key]) return _toolsCache[key];
    const data = await _api('GET', `/tools/available?file_ext=${encodeURIComponent(key)}`);
    _toolsCache[key] = Array.isArray(data) ? data : [];
    return _toolsCache[key];
  }

  // D11-D12 — construire le menu dropdown
  function _buildToolMenu(tools, path, ext) {
    const menu = document.createElement('div');
    menu.className = 'fm-tool-menu';
    menu.onclick = (e) => e.stopPropagation();

    // Groupe par group field
    const byGroup = {};
    const noGroup = [];
    tools.forEach(t => {
      if (t.group) {
        (byGroup[t.group] = byGroup[t.group] || []).push(t);
      } else {
        noGroup.push(t);
      }
    });

    // Section EDITION (actions fichier existantes)
    const hasEditSection = path && !path.endsWith('/');
    if (hasEditSection) {
      const sec = _menuSection('FICHIER');
      menu.appendChild(sec);
      const safePath = _jsSafe(path);
      const name = _jsSafe(_basename(path));
      if (_canWrite) {
        sec.appendChild(_menuItemAction('mdi-pencil', 'Renommer', () => {
          _closeToolMenu(); FM.openRename(path, _basename(path));
        }));
        sec.appendChild(_menuItemAction('mdi-folder-move-outline', 'Déplacer', () => {
          _closeToolMenu(); FM.openMove(path, _basename(path));
        }));
      }
      const dlUrl = `/api/w/${_slug}/files/download?path=${encodeURIComponent(path)}`;
      sec.appendChild(_menuItemLink('mdi-download', 'Télécharger', dlUrl));
      if (_canWrite) {
        sec.appendChild(_menuItemAction('mdi-delete-outline', 'Supprimer', () => {
          _closeToolMenu(); FM.deleteSingle(path);
        }, true));
      }
    }

    if (tools.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'fm-tool-menu-empty';
      empty.textContent = 'Aucun outil disponible pour ce type de fichier';
      menu.appendChild(empty);
      return menu;
    }

    // Section pour chaque groupe
    Object.entries(byGroup).forEach(([groupName, groupTools]) => {
      const sec = _menuSection(groupName.toUpperCase());
      menu.appendChild(sec);
      const pinned = groupTools.filter(t => t.pinned);
      const unpinned = groupTools.filter(t => !t.pinned);
      pinned.forEach(t => {
        sec.appendChild(_menuItemTool(t, path, ext));
      });
      if (unpinned.length > 0) {
        // Entrée générique "Fabric…" → ouvre le modal
        const modalTool = unpinned.find(t => t.ui === 'modal');
        if (modalTool) {
          sec.appendChild(_menuItemAction(
            modalTool.icon,
            modalTool.name,
            () => { _closeToolMenu(); openFabricModal(path, ext); }
          ));
        }
      }
    });

    // Tools sans groupe
    if (noGroup.length > 0) {
      if (Object.keys(byGroup).length > 0) {
        const div = document.createElement('div');
        div.className = 'fm-tool-menu-sep';
        menu.appendChild(div);
      }
      noGroup.forEach(t => menu.appendChild(_menuItemTool(t, path, ext)));
    }

    return menu;
  }

  function _menuSection(title) {
    const div = document.createElement('div');
    div.className = 'fm-tool-menu-section';
    div.textContent = title;
    return div;
  }

  function _menuItemTool(tool, path, ext) {
    const item = document.createElement('div');
    item.className = 'fm-tool-menu-item';
    item.innerHTML = `
      <i class="mdi ${tool.icon} fm-tool-menu-icon"></i>
      <div class="fm-tool-menu-info">
        <span class="fm-tool-menu-name">${_esc(tool.name)}</span>
        <span class="fm-tool-menu-desc">${_esc(tool.description)}</span>
      </div>
    `;
    item.onclick = () => {
      _closeToolMenu();
      if (tool.ui === 'modal') {
        openFabricModal(path, ext);
      } else {
        openToolPopover(tool, path, item);
      }
    };
    return item;
  }

  function _menuItemAction(icon, label, fn, danger) {
    const item = document.createElement('div');
    item.className = 'fm-tool-menu-item' + (danger ? ' danger' : '');
    item.innerHTML = `<i class="mdi ${icon} fm-tool-menu-icon"></i><span>${label}</span>`;
    item.onclick = fn;
    return item;
  }

  function _menuItemLink(icon, label, href) {
    const a = document.createElement('a');
    a.className = 'fm-tool-menu-item';
    a.href = href;
    a.download = '';
    a.innerHTML = `<i class="mdi ${icon} fm-tool-menu-icon"></i><span>${label}</span>`;
    a.onclick = () => _closeToolMenu();
    return a;
  }

  function _positionMenu(menu, anchorEl) {
    menu.style.visibility = 'hidden';
    menu.style.display = 'block';
    const rect = anchorEl.getBoundingClientRect();
    const mw = menu.offsetWidth || 240;
    const mh = menu.offsetHeight || 200;
    let left = rect.left;
    let top = rect.bottom + 4;
    if (left + mw > window.innerWidth - 8) left = window.innerWidth - mw - 8;
    if (top + mh > window.innerHeight - 8) top = rect.top - mh - 4;
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
    menu.style.visibility = '';
  }

  // ── TOOL INLINE POPOVER (D15) ─────────────────────────────────────────────

  function openToolPopover(tool, path, anchorEl) {
    _popoverTool = tool;
    _popoverPath = path;
    const pop = document.getElementById('fm-tool-popover');
    document.getElementById('fm-tool-popover-title').textContent = tool.name;
    document.getElementById('fm-tool-popover-desc').textContent = tool.description;
    const body = document.getElementById('fm-tool-popover-body');
    body.innerHTML = '';

    (tool.params || []).forEach(param => {
      const wrap = document.createElement('div');
      wrap.style.cssText = 'margin-bottom:10px;';
      const label = document.createElement('label');
      label.className = 'form-label';
      label.textContent = param.label;
      label.style.cssText = 'display:block;margin-bottom:4px;font-size:0.72rem;';
      wrap.appendChild(label);

      let input;
      if (param.type === 'select' && param.options) {
        input = document.createElement('select');
        input.className = 'form-input';
        input.name = param.key;
        input.style.width = '100%';
        param.options.forEach(opt => {
          const o = document.createElement('option');
          o.value = opt; o.textContent = opt;
          if (opt === param.default) o.selected = true;
          input.appendChild(o);
        });
      } else if (param.type === 'select' && param.options_endpoint) {
        // Dynamic select — populated async from options_endpoint
        input = document.createElement('select');
        input.className = 'form-input';
        input.name = param.key;
        input.style.width = '100%';
        const loadingOpt = document.createElement('option');
        loadingOpt.value = '';
        loadingOpt.textContent = 'Chargement…';
        input.appendChild(loadingOpt);
        _api('GET', param.options_endpoint).then(items => {
          input.innerHTML = '';
          items.forEach(item => {
            const o = document.createElement('option');
            o.value = item.id !== undefined ? item.id : item;
            o.textContent = item.name !== undefined ? item.name : item;
            input.appendChild(o);
          });
        }).catch(() => {
          input.innerHTML = '<option value="">Erreur chargement agents</option>';
        });
      } else {
        input = document.createElement('input');
        input.type = 'text';
        input.className = 'form-input';
        input.name = param.key;
        input.style.width = '100%';
        input.placeholder = param.default || '';
        input.value = param.default || '';
      }
      wrap.appendChild(input);
      body.appendChild(wrap);
    });

    pop.style.display = 'block';

    // Positionner le popover
    const rect = anchorEl.getBoundingClientRect();
    const pw = 280;
    let left = rect.left;
    let top = rect.bottom + 6;
    if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
    if (top + 200 > window.innerHeight - 8) top = rect.top - 220;
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    pop.style.width = pw + 'px';
  }

  function closeToolPopover() {
    const pop = document.getElementById('fm-tool-popover');
    if (pop) pop.style.display = 'none';
    _popoverTool = null;
    _popoverPath = '';
  }

  function launchFromPopover() {
    if (!_popoverTool || !_popoverPath) return;
    const toolKey = _popoverTool.key;
    const filePath = _popoverPath;
    const body = document.getElementById('fm-tool-popover-body');
    const params = {};
    body.querySelectorAll('[name]').forEach(el => {
      params[el.name] = el.value;
    });
    closeToolPopover();
    executeTool(toolKey, filePath, params);
  }

  // ── FABRIC MODAL (D16-D18) ────────────────────────────────────────────────

  async function openFabricModal(path, ext) {
    _fabricPath = path;
    _fabricExt = ext || '';
    _fabricSelected = null;
    _fabricAcIndex = -1;

    document.getElementById('fm-fabric-pattern-input').value = '';
    document.getElementById('fm-fabric-pattern-dropdown').style.display = 'none';
    document.getElementById('fm-fabric-output').value = '';
    document.getElementById('fm-fabric-preview-wrap').style.display = 'none';
    document.getElementById('fm-fabric-preview').textContent = '';
    document.getElementById('fm-fabric-selected-label').textContent = '';
    document.getElementById('fm-fabric-launch-btn').disabled = true;
    document.getElementById('fm-fabric-modal').style.display = 'flex';

    // Charger les agents dans le select
    const agentSel = document.getElementById('fm-fabric-agent');
    if (agentSel && agentSel.children.length <= 1) {
      _api('GET', '/tools/agents').then(agents => {
        agentSel.innerHTML = '';
        agents.forEach(a => {
          const o = document.createElement('option');
          o.value = a.id;
          o.textContent = a.name;
          agentSel.appendChild(o);
        });
        if (!agentSel.children.length) {
          agentSel.innerHTML = '<option value="">(aucun agent)</option>';
        }
      }).catch(() => {
        agentSel.innerHTML = '<option value="">Erreur chargement</option>';
      });
    }

    // Précharger les patterns
    if (!_fabricPatterns) {
      try {
        _fabricPatterns = await _api('GET', '/tools/fabric/patterns');
      } catch (_) {
        _fabricPatterns = [];
      }
    }

    // Afficher la liste complète dans le dropdown au focus
    document.getElementById('fm-fabric-pattern-input').focus();
    setTimeout(() => fabricPatternFilter(''), 50);
  }

  function closeFabricModal() {
    document.getElementById('fm-fabric-modal').style.display = 'none';
    document.getElementById('fm-fabric-pattern-dropdown').style.display = 'none';
    _fabricPath = '';
    _fabricSelected = null;
  }

  // D17 — Autocomplete pattern (D17)
  function fabricPatternFilter(query) {
    const dropdown = document.getElementById('fm-fabric-pattern-dropdown');
    const patterns = _fabricPatterns || [];
    const q = query.toLowerCase().trim();
    const filtered = q
      ? patterns.filter(p => p.key.toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q))
      : patterns;
    const display = filtered.slice(0, 30);

    if (display.length === 0) {
      dropdown.style.display = 'none';
      return;
    }

    dropdown.innerHTML = '';
    _fabricAcIndex = -1;
    display.forEach((p, i) => {
      const item = document.createElement('div');
      item.className = 'fm-autocomplete-item' + (p.pinned ? ' pinned' : '');
      item.dataset.key = p.key;
      item.innerHTML = `
        <span class="fm-ac-name">${_esc(p.key)}</span>
        ${p.pinned ? '<span class="fm-ac-badge">★ ÉPINGLÉ</span>' : ''}
      `;
      item.onmousedown = (e) => {
        e.preventDefault();
        selectFabricPattern(p.key, p.description);
      };
      dropdown.appendChild(item);
    });
    dropdown.style.display = 'block';
  }

  function fabricPatternKeydown(e) {
    const dropdown = document.getElementById('fm-fabric-pattern-dropdown');
    const items = dropdown.querySelectorAll('.fm-autocomplete-item');
    if (!items.length) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _fabricAcIndex = Math.min(_fabricAcIndex + 1, items.length - 1);
      _updateAcSelection(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _fabricAcIndex = Math.max(_fabricAcIndex - 1, 0);
      _updateAcSelection(items);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const sel = _fabricAcIndex >= 0 ? items[_fabricAcIndex] : items[0];
      if (sel) selectFabricPattern(sel.dataset.key, '');
    } else if (e.key === 'Escape') {
      dropdown.style.display = 'none';
    }
  }

  function _updateAcSelection(items) {
    items.forEach((item, i) => item.classList.toggle('active', i === _fabricAcIndex));
    if (_fabricAcIndex >= 0) items[_fabricAcIndex].scrollIntoView({ block: 'nearest' });
  }

  async function selectFabricPattern(key, description) {
    _fabricSelected = { key, description };
    document.getElementById('fm-fabric-pattern-input').value = key;
    document.getElementById('fm-fabric-pattern-dropdown').style.display = 'none';
    document.getElementById('fm-fabric-selected-label').textContent = `Pattern sélectionné : ${key}`;
    document.getElementById('fm-fabric-launch-btn').disabled = false;

    // Auto-génère le nom de fichier de sortie
    if (!document.getElementById('fm-fabric-output').value) {
      const stem = _basename(_fabricPath).replace(/\.[^.]+$/, '');
      document.getElementById('fm-fabric-output').value = `.hive/outputs/${stem}.${key}.md`;
    }

    // Aperçu du system prompt (D18)
    const previewWrap = document.getElementById('fm-fabric-preview-wrap');
    const preview = document.getElementById('fm-fabric-preview');
    preview.textContent = 'Chargement…';
    previewWrap.style.display = '';
    try {
      const data = await _api('GET', `/tools/fabric/pattern/${encodeURIComponent(key)}`);
      preview.textContent = data.system_prompt || '(aperçu non disponible)';
    } catch (_) {
      preview.textContent = '(aperçu non disponible)';
    }
  }

  function launchFabric() {
    if (!_fabricSelected || !_fabricPath) return;
    const filePath = _fabricPath;   // capture avant que closeFabricModal() remette _fabricPath = ''
    const agentSel = document.getElementById('fm-fabric-agent');
    const agent = agentSel ? agentSel.value : '';
    const outputFile = document.getElementById('fm-fabric-output').value.trim();
    const params = {
      pattern: _fabricSelected.key,
      agent,
      ...(outputFile ? { output_file: outputFile } : {}),
    };
    closeFabricModal();
    executeTool('fabric_run', filePath, params);
  }

  // ── EXECUTE TOOL (D19) ────────────────────────────────────────────────────

  async function executeTool(toolKey, filePath, params) {
    const fileName = _basename(filePath);
    _toast(`⏳ Lancement de ${toolKey} sur ${fileName}…`, 'info');
    try {
      const result = await _api('POST', '/tools/execute', {
        tool_key: toolKey,
        file_path: filePath,
        params: params || {},
      });
      const taskId = result.task_id;
      const taskName = result.name || `${toolKey} — ${fileName}`;
      // Enregistrer pour le suivi SSE
      _pendingTaskToasts[taskId] = { name: taskName };
      _toast(`⏳ ${taskName} — en cours…`, 'info');
    } catch (e) {
      _toast(`Erreur: ${e.message}`, 'error');
    }
  }

  // ── HELPERS ───────────────────────────────────────────────────────────────

  function _toast(msg, type) {
    const el = document.getElementById('fm-toast');
    el.textContent = msg;
    el.className = `fm-toast fm-toast-${type}`;
    el.style.display = 'block';
    clearTimeout(el._tid);
    el._tid = setTimeout(() => { el.style.display = 'none'; }, type === 'info' ? 8000 : 3500);
  }

  function _toastLink(msg, outputFile, type) {
    const el = document.getElementById('fm-toast');
    const enc = encodeURIComponent(outputFile);
    el.innerHTML = `${_esc(msg)} — <a href="#" onclick="FM.openViewer('${_jsSafe(outputFile)}');return false;" style="color:var(--cyan);">Ouvrir</a>`;
    el.className = `fm-toast fm-toast-${type}`;
    el.style.display = 'block';
    clearTimeout(el._tid);
    el._tid = setTimeout(() => { el.style.display = 'none'; }, 6000);
  }

  // ── PUBLIC API ────────────────────────────────────────────────────────────
  return {
    init, loadDir,
    openViewer, closeViewer,
    openEditor, closeEditor, confirmCloseEditor, markDirty, editorKeydown, saveFile,
    triggerUpload, uploadFromInput,
    deleteSingle, deleteSelected,
    openRename, closeRename, submitRename,
    openMove, closeMove, submitMove,
    mkdirPrompt, closeMkdir, submitMkdir,
    newFilePrompt, closeNewFile, submitNewFile,
    toggleSelect,
    // D10-D19 — Human Tools
    openToolMenu, openViewerToolMenu,
    openToolPopover, closeToolPopover, launchFromPopover,
    openFabricModal, closeFabricModal, launchFabric,
    fabricPatternFilter, fabricPatternKeydown, selectFabricPattern,
    executeTool,
  };
})();
