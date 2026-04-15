/* ═══════════════════════════════════════════════════════════
   MangaArr — app.js  (index.html)
═══════════════════════════════════════════════════════════ */

// ══════════════════════════════════════════════════════════
// NAVIGATION
// ══════════════════════════════════════════════════════════

document.querySelectorAll('.nav-item[data-page]').forEach(link => {
  link.addEventListener('click', e => { e.preventDefault(); navigate(link.dataset.page); });
});

function navigate(page) {
  document.querySelectorAll('.nav-item').forEach(l => l.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const link = document.querySelector(`[data-page="${page}"]`);
  const pageEl = document.getElementById(`page-${page}`);
  if (link) link.classList.add('active');
  if (pageEl) pageEl.classList.add('active');
  const loaders = {
    'collection-local':          loadCollection,
    'activity-logs':             loadLogs,
    'indexers':                  loadIndexerConfig,
    'settings-download-client':  loadDownloadClients,
    'settings-libraries':        loadLibraries,
    'settings-media':            loadMediaSettings,
    'settings-profiles':         loadProfiles,
    'settings-metadata':         loadMetadataSources,
    'settings-storage':          loadCacheStats,
    'settings-incoming':         loadIncomingSettings,
    'activity-queue':            loadQueue,
  };
  if (loaders[page]) loaders[page]();
}

// ══════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════

window.addEventListener('DOMContentLoaded', () => {
  loadCollection();
  pollTaskStatus();
  const hash = location.hash.replace('#','');
  if (hash) navigate(hash);
});

// ══════════════════════════════════════════════════════════
// API HELPER
// ══════════════════════════════════════════════════════════

async function api(path, method = 'GET', body = null, timeoutMs = 30000) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  try {
    const controller = new AbortController();
    const tid = setTimeout(() => controller.abort(), timeoutMs);
    opts.signal = controller.signal;
    const r = await fetch('/api' + path, opts);
    clearTimeout(tid);
    return r.json();
  } catch (e) {
    if (e.name === 'AbortError') return { ok: false, message: 'Timeout (serveur occupé)' };
    return { ok: false, message: String(e) };
  }
}

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ══════════════════════════════════════════════════════════
// KOMGA STATUS SIDEBAR
// ══════════════════════════════════════════════════════════

// Komga status supprimé

// ══════════════════════════════════════════════════════════
// PAGE: COLLECTION — LOCALE
// ══════════════════════════════════════════════════════════

let _allSeries   = [];
let _curPage     = 1;
let _perPage     = 25;
let _filteredSeries = [];

async function loadCollection() {
  const libSel = document.getElementById('library-select');
  const grid   = document.getElementById('series-grid');
  grid.innerHTML = '';
  if (libSel) libSel.innerHTML = '<option value="">Chargement…</option>';

  const d = await api('/libraries');
  const libs = d.libraries || [];

  if (!libs.length) {
    if (libSel) libSel.innerHTML = '<option value="">Aucune librairie</option>';
    grid.innerHTML = emptyState('Aucune librairie configurée — allez dans Settings > Librairies');
    return;
  }

  if (libSel) {
    libSel.innerHTML = libs.map(l =>
      `<option value="${esc(l.id)}">${esc(l.name)}</option>`
    ).join('');
    libSel.onchange = () => { _curPage = 1; loadSeries(libSel.value); };
  }
  loadSeries(libs[0].id);
}

async function loadSeries(libraryId) {
  const grid = document.getElementById('series-grid');
  // Skeleton immédiat pour éviter le freeze visuel
  grid.innerHTML = Array(6).fill('<div class="series-card" style="opacity:.3;pointer-events:none"><div style="height:200px;background:var(--bg-input);border-radius:8px"></div></div>').join('');
  stopEnrichPoll();

  const d = await api(`/collection/series?library_id=${libraryId}`);
  _allSeries = d.series || [];
  _curPage = 1;
  // Applique le tri A->Z par défaut via filterSeries
  filterSeries();

  // Lance le polling d'enrichissement si nécessaire
  if (d.enriching || _allSeries.some(s => !s.metaLoaded)) {
    startEnrichPoll(libraryId);
  }
}

function renderPage() {
  const start = (_curPage - 1) * _perPage;
  const slice = _filteredSeries.slice(start, start + _perPage);
  renderSeries(slice);
  renderPagination(_filteredSeries.length);
}

function renderSeries(series) {
  const grid = document.getElementById('series-grid');
  if (!series.length) { grid.innerHTML = emptyState('Aucune série'); return; }

  grid.innerHTML = series.map(s => {
    const hasProgress = s.totalVF && s.totalVF > 0;
    const pct = hasProgress ? Math.min(100, Math.round((s.booksCount / s.totalVF) * 100)) : 0;
    const initial = (s.name || '?')[0].toUpperCase();

    // Infos metadata pour le tooltip ℹ
    const metaLines = [
      s.statut_vf  ? `Statut : ${s.statut_vf}`                  : '',
      s.genres?.length ? `Genres : ${s.genres.slice(0,3).join(', ')}` : '',
      s.totalVF    ? `Tomes VF : ${s.booksCount}/${s.totalVF}`  : `Tomes : ${s.booksCount}`,
      s.diskPath   ? `Dossier : ${s.diskPath}`                   : '',
    ].filter(Boolean);

    return `<a class="series-card" href="/series/${esc(s.slug || s.id)}">
      <div class="series-cover-wrap">
        <img class="series-cover" src="${esc(s.thumbnail)}"
             alt="${esc(s.name)}"
             onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
        <div class="series-cover-placeholder" style="display:none">${esc(initial)}</div>
        <button class="series-info-btn" title="${esc(metaLines.join('\n'))}"
                onclick="event.preventDefault();event.stopPropagation();showSeriesInfo(${esc(JSON.stringify({
                  name: s.name, statut: s.statut_vf, genres: s.genres,
                  booksCount: s.booksCount, totalVF: s.totalVF, diskPath: s.diskPath,
                  slug: s.slug || s.id
                }))})">ℹ</button>
      </div>
      <div class="series-card-body">
        <div class="series-name">${esc(s.name)}</div>
        <div class="series-count">${s.booksCount} tome(s)${s.statut_vf ? ' · ' + esc(s.statut_vf) : ''}</div>
        ${hasProgress ? `
          <div class="series-progress-bar">
            <div class="series-progress-fill" style="width:${pct}%"></div>
          </div>
          <div class="series-progress-label">${s.booksCount}/${s.totalVF} VF</div>
        ` : ''}
      </div>
    </a>`;
  }).join('');
}

function renderPagination(total) {
  const totalPages = Math.ceil(total / _perPage);
  const pag = document.getElementById('pagination');
  if (totalPages <= 1) { pag.innerHTML = ''; return; }

  let html = `<button class="page-btn" onclick="goPage(${_curPage-1})" ${_curPage===1?'disabled':''}>‹</button>`;
  for (let i = 1; i <= totalPages; i++) {
    if (totalPages > 9 && i > 2 && i < totalPages - 1 && Math.abs(i - _curPage) > 2) {
      if (i === 3 || i === totalPages - 2) html += `<span class="page-btn" style="cursor:default">…</span>`;
      continue;
    }
    html += `<button class="page-btn ${i===_curPage?'active':''}" onclick="goPage(${i})">${i}</button>`;
  }
  html += `<button class="page-btn" onclick="goPage(${_curPage+1})" ${_curPage===totalPages?'disabled':''}>›</button>`;
  pag.innerHTML = html;
}

function goPage(p) {
  const totalPages = Math.ceil(_filteredSeries.length / _perPage);
  if (p < 1 || p > totalPages) return;
  _curPage = p;
  renderPage();
  window.scrollTo(0, 0);
}

function changePerPage(val) {
  _perPage = parseInt(val);
  _curPage = 1;
  renderPage();
}

// Normalise un texte pour la recherche :
// supprime apostrophes, accents, ponctuations, casse
// Ex: "L'Île errante" → "lile errante" | "Josée" → "josee"
function normalizeSearch(text) {
  if (!text) return '';
  // 1. Supprime apostrophes/guillemets avant tout (l'île → lile)
  text = text.replace(/[''`'"]/g, '');
  // 2. Décompose accents (NFD) puis supprime diacritiques
  text = text.normalize('NFD').replace(/[̀-ͯ]/g, '');
  // 3. Minuscules
  text = text.toLowerCase();
  // 4. Tout non-alnum → espace (tirets, virgules, #, +, /, (, ), etc.)
  text = text.replace(/[^a-z0-9 ]/g, ' ');
  // 5. Collapse espaces multiples
  text = text.replace(/ +/g, ' ').trim();
  return text;
}

function filterSeries() {
  const raw        = document.getElementById('series-search').value;
  const statusFilter = (document.getElementById('status-filter') || {}).value || '';
  const sortOrder  = (document.getElementById('sort-order') || {}).value || 'asc';

  let result = [..._allSeries];

  // 1. Filtre textuel
  if (raw.trim()) {
    const q      = normalizeSearch(raw);
    const tokens = q.split(' ').filter(t => t.length > 0);
    result = result.filter(s => {
      const norm    = normalizeSearch(s.name);
      const compact = norm.replace(/ /g, '');
      const qComp   = q.replace(/ /g, '');
      return tokens.every(tok => norm.includes(tok)) || compact.includes(qComp);
    });
  }

  // 2. Filtre statut
  if (statusFilter) {
    result = result.filter(s => {
      const statut  = (s.statut_vf || '').toLowerCase();
      const owned   = s.booksCount || 0;
      const totalVF = s.totalVF   || 0;
      const termine = statut.includes('termin');
      const enCours = statut.includes('cours');
      const complet = totalVF > 0 && owned >= totalVF;

      switch (statusFilter) {
        case 'termine':
          return termine;
        case 'en_cours':
          return enCours;
        case 'termine_incomplet':
          // Terminé mais ne possède pas tous les tomes
          return termine && totalVF > 0 && owned < totalVF;
        case 'a_jour':
          // En cours ET possède tous les tomes actuellement sortis
          return enCours && complet;
        default:
          return true;
      }
    });
  }

  // 3. Tri alphabétique
  result.sort((a, b) => {
    const na = normalizeSearch(a.name);
    const nb = normalizeSearch(b.name);
    return sortOrder === 'asc' ? na.localeCompare(nb) : nb.localeCompare(na);
  });

  _filteredSeries = result;
  _curPage = 1;
  renderPage();
}

async function scanLibrary() {
  const libId = document.getElementById('library-select').value;
  if (!libId) return;
  const d = await api(`/collection/scan/${libId}`, 'POST');
  showToast(d.ok ? 'Scan demandé à Komga ✓' : 'Erreur scan');
}

// ══════════════════════════════════════════════════════════
// PAGE: LOGS
// ══════════════════════════════════════════════════════════

async function loadLogs() {
  const container = document.getElementById('logs-container');
  const d = await api('/logs');
  const logs = d.logs || [];
  if (!logs.length) { container.innerHTML = emptyState('Aucun log'); return; }
  container.innerHTML = logs.map(log => `
    <div class="log-entry">
      <span class="log-time">${esc(log.time)}</span>
      <span class="log-level ${esc(log.level)}">${esc(log.level)}</span>
      <span class="log-message">${esc(log.message)}</span>
    </div>`).join('');
}

async function clearLogs() {
  await api('/logs/clear', 'POST');
  loadLogs();
}

// ══════════════════════════════════════════════════════════
// PAGE: INDEXERS
// ══════════════════════════════════════════════════════════

// loadIndexerConfig → voir section EBDZ SCRAPE

async function testIndexer() {
  const mybbuser = document.getElementById('mybbuser-input').value.trim();
  const status   = document.getElementById('indexer-status');
  status.textContent = 'Test…'; status.className = 'status-msg';
  const d = await api('/indexers/test', 'POST', { mybbuser });
  status.textContent = d.message;
  status.className = 'status-msg ' + (d.ok ? 'ok' : 'error');
}

async function launchProcess() {
  const path   = document.getElementById('rename-path').value.trim();
  const status = document.getElementById('process-status');
  if (!path) { status.textContent = 'Chemin requis'; status.className = 'status-msg error'; return; }
  const d = await api('/media/process', 'POST', { path });
  if (d.ok) {
    status.textContent = 'Lancé…'; status.className = 'status-msg ok';
    showTaskToast('Conversion + renommage en cours…');
  } else {
    status.textContent = d.message; status.className = 'status-msg error';
  }
}

// ══════════════════════════════════════════════════════════
// PAGE: SETTINGS MEDIA
// ══════════════════════════════════════════════════════════

let _forceOrganizeEnabled = false;

async function saveMediaSettings() {
  const forceOrg = document.getElementById('toggle-force-organize').checked;
  const fmtEl    = document.querySelector('input[name="rename-format"]:checked');
  const mm = {
    auto_rename:            document.getElementById('toggle-rename').checked,
    auto_convert_cbr:       document.getElementById('toggle-cbr').checked,
    auto_convert_pdf:       document.getElementById('toggle-pdf').checked,
    auto_replace:           document.getElementById('toggle-replace').checked,
    force_organize_enabled: forceOrg,
    rename_format:          fmtEl ? parseInt(fmtEl.value) : 1,
  };
  _forceOrganizeEnabled = forceOrg;
  await api('/config', 'POST', { media_management: mm });
  showToast('Paramètres sauvegardés ✓');
}

async function saveDownloadDir() {
  const dir = document.getElementById('download-dir').value.trim();
  await api('/config', 'POST', { download_dir: dir });
  showToast('Chemin sauvegardé ✓');
}

// ══════════════════════════════════════════════════════════
// RENOMMAGE BIBLIOTHÈQUE
// ══════════════════════════════════════════════════════════

async function loadMediaSettings() {
  const d  = await api('/config');
  const mm = d.media_management || {};
  document.getElementById('toggle-rename').checked          = mm.auto_rename           ?? true;
  document.getElementById('toggle-cbr').checked            = mm.auto_convert_cbr       ?? true;
  document.getElementById('toggle-pdf').checked            = mm.auto_convert_pdf       ?? true;
  document.getElementById('toggle-replace').checked        = mm.auto_replace           ?? true;
  document.getElementById('toggle-force-organize').checked = mm.force_organize_enabled ?? false;
  _forceOrganizeEnabled = mm.force_organize_enabled ?? false;
  const fmt   = String(mm.rename_format ?? 1);
  const fmtEl = document.querySelector(`input[name="rename-format"][value="${fmt}"]`);
  if (fmtEl) fmtEl.checked = true;
  else { const f1 = document.getElementById('fmt-1'); if (f1) f1.checked = true; }
  if (d.download_dir) document.getElementById('download-dir').value = d.download_dir;
  loadWatcherInterval();
  loadRenameHistory();
}

async function loadRenameHistory() {
  const container = document.getElementById('rename-history-list');
  if (!container) return;
  const d = await api('/library/rename/history');
  if (!d.ok || !d.history?.length) {
    container.innerHTML = '<p style="font-size:12px;color:var(--text-dim);margin:0">Aucun historique disponible.</p>';
    return;
  }
  const fmtNames = { 1: 'Format 1', 2: 'Format 2', 3: 'Format 3' };
  container.innerHTML = d.history.map(h => {
    const date    = h.timestamp ? h.timestamp.replace(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/, '$3/$2/$1 $4:$5') : '?';
    const isDry   = h.dry_run;
    const isRB    = h.rolled_back;
    const canRB   = !isDry && !isRB && h.renamed > 0;
    const badge   = isDry  ? '<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:rgba(99,102,241,.2);color:var(--accent)">dry-run</span>'
                  : isRB   ? '<span style="font-size:10px;padding:1px 6px;border-radius:8px;background:rgba(107,114,128,.2);color:var(--text-dim)">annulé</span>'
                  : '';
    return `<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border);font-size:12px">
      <div style="flex:1;min-width:0">
        <span style="color:var(--text);font-weight:500">${date}</span>
        <span style="color:var(--text-dim);margin-left:8px">${fmtNames[h.format_id]||'?'}</span>
        ${badge}
        <span style="color:var(--text-dim);margin-left:8px">${h.renamed} fichier(s)</span>
      </div>
      ${canRB
        ? `<button onclick="renameLibraryRollback('${esc(h.file)}')"
                   style="padding:4px 12px;border-radius:6px;border:1px solid var(--border);
                          background:none;color:var(--text);font-size:11px;cursor:pointer;white-space:nowrap">
             ↩ Rollback
           </button>`
        : ''}
    </div>`;
  }).join('');
}

function _renameSetBusy(busy) {
  const p = document.getElementById('btn-rename-preview');
  const a = document.getElementById('btn-rename-apply');
  if (p) p.disabled = busy;
  if (a) a.disabled = busy;
}

function _renameShowResult(res, isDry) {
  const area    = document.getElementById('rename-result');
  const summary = document.getElementById('rename-result-summary');
  const details = document.getElementById('rename-result-details');
  if (!area) return;

  const mode = isDry ? ' (prévisualisation — aucun fichier modifié)' : '';
  const col  = res.errors > 0 ? 'var(--danger)' : res.renamed > 0 ? 'var(--success)' : 'var(--text-dim)';
  summary.style.color = col;
  summary.textContent = `${res.renamed} renommé(s) · ${res.unchanged} inchangé(s) · ${res.skipped} ignoré(s)${res.errors ? ` · ${res.errors} erreur(s)` : ''}${mode}`;

  details.innerHTML = (res.details || []).map(d => {
    if (d.status === 'renamed') {
      return `<div style="padding:4px 16px;display:flex;gap:6px;align-items:baseline">
        <span style="color:var(--success);flex-shrink:0">${isDry ? '→' : '✓'}</span>
        <div>
          <span style="color:var(--text-dim)">${esc(d.series)}/</span><span>${esc(d.old)}</span>
          <span style="color:var(--text-dim);margin:0 4px">→</span>
          <span style="color:var(--accent)">${esc(d.new)}</span>
        </div>
      </div>`;
    }
    if (d.status === 'conflict') {
      return `<div style="padding:4px 16px;color:var(--warning);font-size:11px">
        ⊘ CONFLIT : ${esc(d.series)}/${esc(d.old)} (bloqué par ${esc(d.blocker||'?')})
      </div>`;
    }
    if (d.status === 'error') {
      return `<div style="padding:4px 16px;color:var(--danger);font-size:11px">
        ✗ ${esc(d.series||'')}/${esc(d.old||'')} : ${esc(d.msg||'')}
      </div>`;
    }
    return '';
  }).join('');

  area.style.display = '';
}

async function renameLibraryPreview() {
  const fmtEl = document.querySelector('input[name="rename-format"]:checked');
  const fmt   = fmtEl ? parseInt(fmtEl.value) : 1;
  _renameSetBusy(true);
  const res = await api('/library/rename', 'POST', { dry_run: true, format_id: fmt }, 120000);
  _renameSetBusy(false);
  if (!res.ok) { alert(`Erreur : ${res.message || 'Inconnue'}`); return; }
  _renameShowResult(res, true);
  loadRenameHistory();
}

async function renameLibraryConfirm() {
  const fmtEl = document.querySelector('input[name="rename-format"]:checked');
  const fmt   = fmtEl ? parseInt(fmtEl.value) : 1;
  const fmtNames = { 1: 'Format 1 (Titre Tome XX)', 2: 'Format 2 (Tome XX)', 3: 'Format 3 (héritage)' };
  if (!confirm(`Renommer tous les fichiers de la bibliothèque en ${fmtNames[fmt] || 'Format ?'} ?\n\nCette action peut être annulée via le rollback.`)) return;
  _renameSetBusy(true);
  const res = await api('/library/rename', 'POST', { dry_run: false, format_id: fmt }, 300000);
  _renameSetBusy(false);
  if (!res.ok) { alert(`Erreur : ${res.message || 'Inconnue'}`); return; }
  _renameShowResult(res, false);
  loadRenameHistory();
  if (res.renamed > 0) showToast(`✓ ${res.renamed} fichier(s) renommé(s)`);
}

async function renameLibraryRollback(histFile) {
  if (!confirm(`Annuler le renommage "${histFile}" ?\n\nLes fichiers seront restaurés à leurs noms d'origine.`)) return;
  _renameSetBusy(true);
  const res = await api('/library/rename/rollback', 'POST', { file: histFile }, 120000);
  _renameSetBusy(false);
  if (!res.ok) { alert(`Erreur : ${res.message || 'Inconnue'}`); return; }
  showToast(`↩ ${res.restored} fichier(s) restauré(s)`);
  loadRenameHistory();
  // Affiche le résultat du rollback
  const area    = document.getElementById('rename-result');
  const summary = document.getElementById('rename-result-summary');
  const details = document.getElementById('rename-result-details');
  if (area) {
    summary.style.color = res.errors > 0 ? 'var(--danger)' : 'var(--success)';
    summary.textContent = `Rollback : ${res.restored} restauré(s) · ${res.skipped} ignoré(s)${res.errors ? ` · ${res.errors} erreur(s)` : ''}`;
    details.innerHTML = (res.details || []).map(d => {
      if (d.status === 'restored')
        return `<div style="padding:4px 16px"><span style="color:var(--success)">↩</span> ${esc(d.old)} → <span style="color:var(--accent)">${esc(d.new)}</span></div>`;
      if (d.status === 'absent')
        return `<div style="padding:4px 16px;color:var(--text-dim);font-size:11px">⊘ Absent : ${esc(d.file)}</div>`;
      if (d.status === 'error')
        return `<div style="padding:4px 16px;color:var(--danger);font-size:11px">✗ ${esc(d.file)} : ${esc(d.msg||'')}</div>`;
      return '';
    }).join('');
    area.style.display = '';
  }
}

// ══════════════════════════════════════════════════════════
// PAGE: PROFILES
// ══════════════════════════════════════════════════════════

let _profileData = { tags: [], must_contain: [], must_not_contain: [] };

async function loadProfiles() {
  const d = await api('/profiles');
  _profileData = d;
  renderTagsList(d.tags || []);
  renderKeywords('must_contain',     d.must_contain || []);
  renderKeywords('must_not_contain', d.must_not_contain || []);
}

function renderTagsList(tags) {
  const sorted = [...tags].sort((a, b) => b.score - a.score);
  document.getElementById('tags-list').innerHTML = sorted.map(t => {
    const barW = Math.min(100, t.score);
    return `<div class="tag-score-item" data-name="${esc(t.name)}">
      <span class="tag-score-name">${t.name === 'no_tag' ? 'Sans tag (meilleur)' : esc(t.name)}</span>
      <div class="tag-score-bar"><div class="tag-score-fill" style="width:${barW}%"></div></div>
      <input type="number" class="tag-score-input" value="${t.score}" min="0" max="999"
             onchange="updateLocalScore('${esc(t.name)}', this.value)"
             oninput="this.previousElementSibling.firstElementChild.style.width=Math.min(100,this.value)+'%'">
      <button class="tag-score-del" onclick="deleteTag('${esc(t.name)}')" title="Supprimer">✕</button>
    </div>`;
  }).join('');
}

function updateLocalScore(name, val) {
  const tag = _profileData.tags.find(t => t.name === name);
  if (tag) tag.score = parseInt(val) || 0;
}

async function saveProfiles() {
  // Lit les valeurs actuelles des inputs
  document.querySelectorAll('.tag-score-item').forEach(el => {
    const name  = el.dataset.name;
    const score = parseInt(el.querySelector('.tag-score-input').value) || 0;
    const tag   = _profileData.tags.find(t => t.name === name);
    if (tag) tag.score = score;
  });
  await api('/profiles', 'POST', { tags: _profileData.tags });
  showToast('Profils sauvegardés ✓');
  loadProfiles();
}

async function addTag() {
  const name  = document.getElementById('new-tag-name').value.trim();
  const score = parseInt(document.getElementById('new-tag-score').value) || 50;
  const status = document.getElementById('tag-add-status');
  if (!name) { status.textContent = 'Nom requis'; status.className = 'status-msg error'; return; }
  const d = await api('/profiles/tags', 'POST', { name, score });
  if (d.ok) {
    status.textContent = `Mot clé "${name}" ajouté ✓`; status.className = 'status-msg ok';
    document.getElementById('new-tag-name').value = '';
    document.getElementById('new-tag-score').value = '';
    loadProfiles();
  } else {
    status.textContent = d.message; status.className = 'status-msg error';
  }
}

async function deleteTag(name) {
  if (!confirm(`Supprimer le mot clé "${name}" ?`)) return;
  await api(`/profiles/tags/${encodeURIComponent(name)}`, 'DELETE');
  loadProfiles();
}

// Keywords must_contain / must_not_contain
function renderKeywords(type, keywords) {
  const elId  = type === 'must_contain' ? 'must-contain-tags' : 'must-not-contain-tags';
  const cls   = type === 'must_contain' ? 'must' : 'must-not';
  document.getElementById(elId).innerHTML = keywords.map(kw =>
    `<span class="kw-tag ${cls}">${esc(kw)}<button onclick="removeKeyword('${esc(type)}','${esc(kw)}')">✕</button></span>`
  ).join('');
}

async function addKeyword(type) {
  const inputId = type === 'must_contain' ? 'must-contain-input' : 'must-not-contain-input';
  const val = document.getElementById(inputId).value.trim().toLowerCase();
  if (!val) return;
  const current = type === 'must_contain' ? _profileData.must_contain : _profileData.must_not_contain;
  if (!current.includes(val)) {
    current.push(val);
    await api('/profiles', 'POST', { [type]: current });
    _profileData[type] = current;
  }
  document.getElementById(inputId).value = '';
  renderKeywords(type, current);
}

async function removeKeyword(type, kw) {
  const current = (type === 'must_contain' ? _profileData.must_contain : _profileData.must_not_contain)
    .filter(k => k !== kw);
  await api('/profiles', 'POST', { [type]: current });
  _profileData[type] = current;
  renderKeywords(type, current);
}

// ══════════════════════════════════════════════════════════
// PAGE: METADATA SOURCES
// ══════════════════════════════════════════════════════════

let _komgaConns   = [];
let _komgaLibraries = {};  // { komga_idx: [{id, name}] }

async function loadMetadataSources() {
  // Charge TOUJOURS les connexions Komga en premier (nécessaire pour le modal)
  const connData = await api('/connect/komga');
  _komgaConns = connData.connections || [];

  const [d] = await Promise.all([api('/metadata/sources')]);
  const sources  = d.sources || [];
  const container = document.getElementById('metadata-sources-list');
  if (!container) return;

  if (!sources.length) {
    container.innerHTML = `<div class="settings-card">${emptyState('Aucune source configurée. Cliquez sur + Ajouter.')}</div>`;
  } else {
    // Charge les noms de librairies pour affichage
  const libsData  = await api('/libraries');
  const libsMap   = {};
  (libsData.libraries || []).forEach(l => { libsMap[l.id] = l.name; });

  container.innerHTML = sources.map(s => {
      const libTags = (s.library_ids || []).map(lid =>
        `<span class="source-lib-tag">${esc(libsMap[lid] || lid)}</span>`).join('');
      return `<div class="source-card">
        <div class="source-card-header">
          <div class="source-icon">DB</div>
          <div class="source-info">
            <div class="source-name">${esc(s.name)}</div>
            <div class="source-url">${esc(s.url)}</div>
          </div>
          <button class="btn btn-sm btn-danger" onclick="deleteSource('${esc(s.id)}')">Supprimer</button>
        </div>
        <div class="source-meta">
          ${libTags ? `<span>Librairies : ${libTags}</span>` : '<span style="color:var(--text-dim)">Aucune librairie liée</span>'}
        </div>
      </div>`;
    }).join('');
  }

  // Charge aussi l'intervalle de sync auto
  await loadMetaSyncInterval();
}

async function showAddSourceModal() {
  // Recharge les connexions Komga au moment de l'ouverture
  // (au cas où une instance a été ajoutée depuis la page Connect)
  // Charge les librairies locales pour lier la source
  const libsData = await api('/libraries');
  const libs     = libsData.libraries || [];
  const libsList = document.getElementById('source-libraries-list');
  libsList.innerHTML = libs.length
    ? libs.map(l => `
        <label class="checkbox-item">
          <input type="checkbox" value="${esc(l.id)}" data-lib-name="${esc(l.name)}">
          ${esc(l.name)} <small style="color:var(--text-dim)">(${esc(l.path)})</small>
        </label>`).join('')
    : '<span style="color:var(--text-dim);font-size:12px">Aucune librairie configurée (Settings > Librairies)</span>';
  document.getElementById('source-test-result').textContent = '';
  document.getElementById('source-name').value = 'MangaDB';
  document.getElementById('source-url').value  = 'https://mangadb.uncloudy-nextcloudy.ovh';
  document.getElementById('modal-add-source').classList.remove('hidden');
}


async function addSource() {
  const url    = document.getElementById('source-url').value.trim();
  const name   = document.getElementById('source-name').value.trim();
  const libIds = [...document.querySelectorAll('#source-libraries-list input:checked')].map(el => el.value);
  const result = document.getElementById('source-test-result');

  result.textContent = 'Test de connexion…'; result.className = 'status-msg';

  const d = await api('/metadata/sources', 'POST', {
    url, name,
    library_ids: libIds,
  });

  result.textContent = d.message || (d.ok ? 'Source ajoutée ✓' : 'Erreur');
  result.className = 'status-msg ' + (d.ok ? 'ok' : 'error');

  if (d.ok) {
    setTimeout(() => {
      closeModal('modal-add-source');
      loadMetadataSources();
    }, 1000);
  }
}

async function deleteSource(id) {
  if (!confirm('Supprimer cette source ?')) return;
  await api(`/metadata/sources/${id}`, 'DELETE');
  loadMetadataSources();
}


// ══════════════════════════════════════════════════════════
// TASK POLLING
// ══════════════════════════════════════════════════════════

function pollTaskStatus() {
  setInterval(async () => {
    let d;
    try { d = await api('/media/status'); } catch { return; }
    const toast = document.getElementById('task-toast');
    if (d.running) {
      toast.classList.remove('hidden');
      document.getElementById('task-toast-label').textContent = d.label || 'En cours…';
      document.querySelector('.toast-spinner').style.display = '';
    } else {
      if (!toast.classList.contains('hidden') && d.results?.length) {
        renderProcessResults(d.results);
      }
      // Ne cache pas si c'est un toast manuel
      if (document.querySelector('.toast-spinner').style.display !== 'none') {
        toast.classList.add('hidden');
      }
    }
  }, 2500);
}

function renderProcessResults(results) {
  const container = document.getElementById('process-results');
  if (!container) return;
  container.innerHTML = results.map(r => `
    <div class="result-item">
      <span class="result-badge ${esc(r.status)}">${esc(r.status)}</span>
      <span class="result-name">${esc(r.new || r.file || '')}${r.reason ? ` (${esc(r.reason)})` : ''}</span>
    </div>`).join('');
}

// ══════════════════════════════════════════════════════════
// MODALS
// ══════════════════════════════════════════════════════════

function closeModal(id) {
  document.getElementById(id).classList.add('hidden');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(m => m.classList.add('hidden'));
  }
});

document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.add('hidden');
  });
});

// ══════════════════════════════════════════════════════════
// HELPERS
// ══════════════════════════════════════════════════════════

function emptyState(msg) {
  return `<div class="empty-state">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
    </svg>
    <p>${esc(msg)}</p>
  </div>`;
}

let _toastTimer = null;
function showToast(msg) {
  const toast = document.getElementById('task-toast');
  const label = document.getElementById('task-toast-label');
  const spinner = document.querySelector('.toast-spinner');
  spinner.style.display = 'none';
  label.textContent = msg;
  toast.classList.remove('hidden');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    toast.classList.add('hidden');
    spinner.style.display = '';
  }, 2500);
}

function showTaskToast(msg) {
  const toast = document.getElementById('task-toast');
  document.querySelector('.toast-spinner').style.display = '';
  document.getElementById('task-toast-label').textContent = msg;
  toast.classList.remove('hidden');
}

// ══════════════════════════════════════════════════════════
// ENRICHISSEMENT ARRIÈRE-PLAN (polling)
// ══════════════════════════════════════════════════════════

let _enrichPollInterval = null;
let _currentLibraryId   = null;

function startEnrichPoll(libraryId) {
  _currentLibraryId = libraryId;
  if (_enrichPollInterval) clearInterval(_enrichPollInterval);
  _enrichPollInterval = setInterval(() => pollEnrichEvents(libraryId), 1500);
}

function stopEnrichPoll() {
  if (_enrichPollInterval) { clearInterval(_enrichPollInterval); _enrichPollInterval = null; }
}

async function pollEnrichEvents(libraryId) {
  try {
    const d = await api(`/collection/series/enrich-events/${libraryId}`);
    const events = d.events || [];

    for (const ev of events) {
      applyEnrichEvent(ev.series_id, ev.meta);
    }

    if (!d.enriching) {
      stopEnrichPoll();
    }
  } catch { stopEnrichPoll(); }
}

function applyEnrichEvent(seriesId, meta) {
  if (!meta || !seriesId) return;

  // Met à jour la série dans _allSeries
  const s = _allSeries.find(x => x.id === seriesId);
  if (!s) return;

  s.metaLoaded = true;
  if (meta.tomes_vf)  s.totalVF   = meta.tomes_vf;
  if (meta.statut_vf) s.statut_vf = meta.statut_vf;
  if (meta.genres)    s.genres    = meta.genres;

  // Met à jour la card dans le DOM si visible
  const card = document.querySelector(`.series-card[href*="${seriesId.substring(0,8)}"]`);
  if (!card) return;

  // Mise à jour jauge
  const hasProgress = s.totalVF && s.totalVF > 0;
  const pct = hasProgress ? Math.min(100, Math.round((s.booksCount / s.totalVF) * 100)) : 0;

  const body = card.querySelector('.series-card-body');
  if (!body) return;

  // Met à jour ou ajoute la jauge
  let progressEl = body.querySelector('.series-progress-bar');
  if (hasProgress) {
    if (!progressEl) {
      body.insertAdjacentHTML('beforeend', `
        <div class="series-progress-bar"><div class="series-progress-fill" style="width:0%"></div></div>
        <div class="series-progress-label"></div>`);
      progressEl = body.querySelector('.series-progress-bar');
    }
    const fill  = progressEl.querySelector('.series-progress-fill');
    const label = body.querySelector('.series-progress-label');
    if (fill)  fill.style.width = pct + '%';
    if (label) label.textContent = `${s.booksCount}/${s.totalVF} VF`;
  }
}

// ══════════════════════════════════════════════════════════
// PAGE: STOCKAGE / CACHE
// ══════════════════════════════════════════════════════════

async function loadCacheStats() {
  const container = document.getElementById('cache-stats');
  if (!container) return;
  const d = await api('/cache/stats');
  const libs = d.libraries || {};
  const total = d.total_entries || 0;
  const diskKb = d.disk_size_kb || 0;
  const nbLibs = Object.keys(libs).length;

  container.innerHTML = `
    <div class="cache-stat-card">
      <div class="cache-stat-value">${total}</div>
      <div class="cache-stat-label">Séries en cache</div>
    </div>
    <div class="cache-stat-card">
      <div class="cache-stat-value">${nbLibs}</div>
      <div class="cache-stat-label">Bibliothèque(s) indexée(s)</div>
    </div>
    <div class="cache-stat-card">
      <div class="cache-stat-value">${diskKb} Ko</div>
      <div class="cache-stat-label">Taille sur disque</div>
    </div>`;
  loadCoverCacheStats();
}

async function clearAllCache() {
  const status = document.getElementById('cache-clear-status');
  if (!confirm('Vider tout le cache des métadonnées ? La prochaine ouverture relancera l\'enrichissement.')) return;
  status.textContent = 'Effacement…'; status.className = 'status-msg';
  await api('/cache/clear', 'POST', {});
  status.textContent = 'Cache effacé ✓'; status.className = 'status-msg ok';
  loadCacheStats();
  setTimeout(() => { status.textContent = ''; }, 3000);
}

// ══════════════════════════════════════════════════════════
// PAGE: ACTIVITY — QUEUE
// ══════════════════════════════════════════════════════════

let _queuePage = 1;
const QUEUE_PAGE_SIZE = 20;

async function loadQueue() {
  // Assure que le bon onglet est visible
  switchQueueTab(_queueActiveTab || 'emule');
  await _loadQueueLibrarySelect();
  await refreshQueue();
  await loadCollections();
  updateQueueBadge();
  startIncomingPoll();
  scanIncoming();
}

async function applyQueueFilters() {
  const d = await api('/queue/apply-filters', 'POST', {});
  if (d.ok && d.removed > 0) {
    showToast(`${d.removed} item(s) retiré(s) (filtres profiles)`);
    refreshQueue();
    updateQueueBadge();
  }
}

async function _loadQueueLibrarySelect() {
  const sel = document.getElementById('queue-series-select');
  const lbl = document.getElementById('queue-dest-label');

  const d = await api('/queue/series-on-disk');

  if (!d.ok || !d.libraries?.length) {
    if (lbl) {
      lbl.textContent = '⚠ Aucune librairie configurée (Settings > Librairies)';
      lbl.style.color = 'var(--danger)';
    }
    if (sel) sel.innerHTML = '<option value="">Aucune librairie</option>';
    return;
  }

  if (lbl) { lbl.textContent = ''; }

  if (!sel) return;

  // Groupe par librairie : "Toutes" + optgroup par librairie
  let html = '<option value="|">Toutes les librairies</option>';
  for (const lib of d.libraries) {
    // Option pour toute la librairie
    html += `<optgroup label="📚 ${esc(lib.name)}">`;
    html += `<option value="${esc(lib.id)}|">— Toute la librairie —</option>`;
    for (const serie of lib.series) {
      html += `<option value="${esc(lib.id)}|${esc(serie)}">${esc(serie)}</option>`;
    }
    html += '</optgroup>';
  }
  sel.innerHTML = html;
}

let _allQueueItems = [];     // Cache complet pour filtre/tri/pagination locale
let _queueSortDir   = 'asc'; // 'asc' | 'desc'

async function refreshQueue(page = 1) {
  _queuePage = page;
  // Charge TOUS les items d'un coup (filtre + tri + pagination se font en local)
  const d = await api('/queue?page=1&size=9999');
  _allQueueItems = d.items || [];
  renderQueueStats(d.stats || {});
  filterQueueTable();
}

function filterQueueTable() {
  const q  = ((document.getElementById('queue-search') || {}).value || '').toLowerCase().trim();
  let items = [..._allQueueItems];

  // Filtre textuel sur la série ou le fichier
  if (q) {
    items = items.filter(i =>
      (i.series_name || '').toLowerCase().includes(q) ||
      (i.filename    || '').toLowerCase().includes(q)
    );
  }

  // Tri alphabétique par série
  items.sort((a, b) => {
    const na = (a.series_name || '').toLowerCase();
    const nb = (b.series_name || '').toLowerCase();
    return _queueSortDir === 'asc' ? na.localeCompare(nb) : nb.localeCompare(na);
  });

  // Pagination locale
  const total = items.length;
  const start = (_queuePage - 1) * QUEUE_PAGE_SIZE;
  renderQueueTable(items.slice(start, start + QUEUE_PAGE_SIZE));
  renderQueuePagination(total);
}

function sortQueueBy(col) {
  if (col !== 'series') return;
  _queueSortDir = _queueSortDir === 'asc' ? 'desc' : 'asc';
  const icon = document.getElementById('queue-sort-icon');
  if (icon) icon.textContent = _queueSortDir === 'asc' ? '↑' : '↓';
  _queuePage = 1;
  filterQueueTable();
}

function renderQueueStats(stats) {
  const bar = document.getElementById('queue-stats-bar');
  if (!bar) return;
  bar.innerHTML = `
    <div class="queue-stat"><div class="queue-stat-val">${stats.total||0}</div><div class="queue-stat-lbl">Total</div></div>
    <div class="queue-stat"><div class="queue-stat-val" style="color:var(--warning)">${stats.pending||0}</div><div class="queue-stat-lbl">En attente</div></div>
    <div class="queue-stat"><div class="queue-stat-val" style="color:var(--accent)">${stats.downloading||0}</div><div class="queue-stat-lbl">En cours</div></div>
    <div class="queue-stat"><div class="queue-stat-val" style="color:var(--success)">${stats.done||0}</div><div class="queue-stat-lbl">Terminé</div></div>`;
}

function renderQueueTable(items) {
  const tbody = document.getElementById('queue-tbody');
  const empty = document.getElementById('queue-empty');
  if (!items.length) {
    tbody.innerHTML = '';
    if (empty) empty.style.display = '';
    return;
  }
  if (empty) empty.style.display = 'none';

  _emulePendingList = items;  // référence pour openPendingActionModal

  tbody.innerHTML = items.map((item, _itemIdx) => {
    const isAP      = item.status === 'action_pending';
    const statusCls = item.status === 'done'        ? 'done'
                    : item.status === 'downloading' ? 'downloading'
                    : isAP                          ? 'action-pending'
                    : 'pending';
    const statusLbl = item.status === 'done'        ? 'Terminé'
                    : item.status === 'downloading' ? 'En téléchargement'
                    : isAP                          ? 'ACTION EN ATTENTE'
                    : 'En attente';
    const seriesLink = item.series_slug
      ? `<a class="queue-series-link" href="/series/${esc(item.series_slug)}">${esc(item.series_name || '—')}</a>`
      : `<span>${esc(item.series_name || '—')}</span>`;
    const tomeNum = item.tome_number ? String(item.tome_number).replace('T','') : '?';

    // Bouton ✎ édition tome (eMule — filehash requis)
    const editTomeBtn = item.filehash
      ? `<button class="btn-info-hist" title="Modifier le tome" style="margin-left:4px"
           onclick="editEmuleQueueTome(${esc(JSON.stringify(item))})">✎</button>`
      : '';

    // Bouton info historique (si l'item a été traité)
    const hist = item.history;
    let infoBtn = '';
    if (hist) {
      const tooltip = [
        hist.source_file  ? `Source : ${hist.source_file}`    : '',
        hist.dest_filename ? `Copié  : ${hist.dest_filename}`  : '',
        hist.processed_at  ? `Date   : ${hist.processed_at}`   : '',
        hist.owned_replaced ? `Remplacé : ${hist.owned_replaced}` : '',
        hist.action === 'upgrade' ? '↑ Meilleure qualité' : '',
      ].filter(Boolean).join('\n');
      infoBtn = `<button class="btn-info-hist" title="${esc(tooltip)}" onclick="showItemHistory(${esc(JSON.stringify(hist))})">ℹ</button>`;
    }

    const chk = item.filehash
      ? `<input type="checkbox" class="emule-item-chk" value="${esc(item.filehash)}" onchange="_onEmuleCheckChange()">`
      : '';

    const emulePendingBtn = isAP
      ? `<button class="btn-info-hist" title="Cliquez pour gérer le conflit"
               style="margin-left:4px;background:var(--warning,#f59e0b);color:#000;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700;cursor:pointer"
               onclick="_openEmulePending(${_itemIdx})">▶ Gérer</button>`
      : '';

    return `<tr data-series="${esc((item.series_name||'').toLowerCase())}">
      <td style="text-align:center">${chk}</td>
      <td>${seriesLink}</td>
      <td class="queue-filename" title="${esc(item.filename)}">${esc((item.filename||'').substring(0,60))}${(item.filename||'').length>60?'…':''}</td>
      <td class="queue-tome">${tomeNum}${editTomeBtn}</td>
      <td><span style="font-size:11px;color:var(--text-dim)">${esc(item.tag||'')}</span></td>
      <td style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span class="status-pill ${statusCls}">${statusLbl}</span>${emulePendingBtn}${infoBtn}</td>
    </tr>`;
  }).join('');
}

function _onEmuleCheckChange() {
  const any = document.querySelectorAll('.emule-item-chk:checked').length > 0;
  const btn = document.getElementById('btn-delete-emule');
  if (btn) btn.style.display = any ? '' : 'none';
}

function toggleSelectAllEmule(cb) {
  document.querySelectorAll('.emule-item-chk').forEach(c => c.checked = cb.checked);
  _onEmuleCheckChange();
}

async function deleteSelectedEmule() {
  const checked = [...document.querySelectorAll('.emule-item-chk:checked')];
  if (!checked.length) return;
  const filehashes = checked.map(c => c.value);
  if (!confirm(`Supprimer ${filehashes.length} item(s) de la queue ?`)) return;
  await api('/queue/items', 'DELETE', { filehashes });
  document.getElementById('emule-select-all').checked = false;
  const btn = document.getElementById('btn-delete-emule');
  if (btn) btn.style.display = 'none';
  await refreshQueue();
  await loadCollections();  // Met à jour le nombre de liens dans les .emulecollection
  showToast(`${filehashes.length} item(s) supprimé(s) ✓`);
}

function renderQueuePagination(total) {
  const el = document.getElementById('queue-pagination');
  if (!el) return;
  const totalPages = Math.ceil(total / QUEUE_PAGE_SIZE);
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  // Pagination locale — goQueuePage() ne recharge pas depuis le serveur
  let html = `<button class="page-btn" onclick="goQueuePage(${_queuePage-1})" ${_queuePage===1?'disabled':''}>‹</button>`;
  for (let i = 1; i <= totalPages; i++) {
    html += `<button class="page-btn ${i===_queuePage?'active':''}" onclick="goQueuePage(${i})">${i}</button>`;
  }
  html += `<button class="page-btn" onclick="goQueuePage(${_queuePage+1})" ${_queuePage===totalPages?'disabled':''}>›</button>`;
  el.innerHTML = html;
}

function goQueuePage(page) {
  _queuePage = page;
  filterQueueTable();
}

async function updateQueueBadge() {
  const d = await api('/queue?page=1&size=1');
  const stats = d.stats || {};
  const pending = (stats.pending || 0) + (stats.downloading || 0);
  const badge = document.getElementById('queue-badge');
  if (badge) {
    badge.textContent = pending;
    badge.style.display = pending > 0 ? '' : 'none';
  }
}


async function detectMissing() {
  const sel = document.getElementById('queue-series-select');
  const val = sel ? sel.value : '|';  // format: "lib_id|serie_name" ou "lib_id|" ou "|"

  const [lib_id, serie_name] = (val || '|').split('|');

  const d = await api('/queue/detect-missing', 'POST', {
    lib_id:     lib_id     || null,
    serie_name: serie_name || null,
  });

  if (!d.ok) {
    showToast(d.message || 'Erreur');
    return;
  }

  const lbl = document.getElementById('queue-dest-label');
  const label0 = serie_name ? `Analyse de "${serie_name}"…`
               : lib_id ? `Analyse de la librairie…`
               : 'Analyse de toutes les librairies…';
  if (lbl) { lbl.textContent = label0; lbl.style.color = 'var(--accent)'; }

  // Poll progression
  const poll = setInterval(async () => {
    const s = await api('/media/status');
    if (lbl) lbl.textContent = s.label || '…';
    if (!s.running) {
      clearInterval(poll);
      showToast(s.label || 'Détection terminée ✓');
      if (lbl) {
        lbl.style.color = 'var(--text-dim)';
        const cfg = await api('/config');
        lbl.textContent = cfg.download_dir ? `📁 ${cfg.download_dir}` : '';
      }
      refreshQueue();
      loadCollections();
      updateQueueBadge();
    }
  }, 1500);
}

async function generateCollection() {
  const d = await api('/queue/generate-collection', 'POST', {});
  if (d.ok) {
    showToast(`Collection générée : ${d.filename || ''} (${d.links || 0} lien(s))`);
    loadCollections();
  } else {
    showToast(d.message || 'Erreur génération', 'error');
  }
}

async function loadCollections() {
  const d = await api('/queue/collections');
  const files = d.files || [];
  const el = document.getElementById('collections-list');
  if (!el) return;
  if (!files.length) {
    el.innerHTML = '<p style="color:var(--text-dim);font-size:12px">Aucun fichier généré.</p>';
    return;
  }

  // Groupe par type (ADD / UPGRADE / autre)
  const addFiles     = files.filter(f => f.filename.includes('_ADD'));
  const upgradeFiles = files.filter(f => f.filename.includes('_UPGRADE'));
  const otherFiles   = files.filter(f => !f.filename.includes('_ADD') && !f.filename.includes('_UPGRADE'));

  const renderGroup = (label, groupFiles, type) => {
    if (!groupFiles.length) return '';
    const total = groupFiles.reduce((s, f) => s + (f.links || 0), 0);
    return `<div style="margin-bottom:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <strong style="font-size:13px">${label} <span style="color:var(--text-dim);font-weight:400">(${total} liens)</span></strong>
        ${groupFiles.length > 1
          ? `<a href="/api/queue/collections/download/${type}" class="btn btn-sm btn-secondary">↓ ZIP tout</a>`
          : `<a href="/api/queue/collections/${esc(groupFiles[0].filename)}" class="btn btn-sm btn-secondary" download>↓ Télécharger</a>`
        }
      </div>
      ${groupFiles.map(f => `
        <div class="collection-item" style="padding:6px 0">
          <span class="collection-name" style="font-size:12px">${esc(f.filename)}</span>
          <span class="collection-meta">${f.links} liens · ${f.size_kb} Ko · ${esc(f.created)}</span>
          <a href="/api/queue/collections/${esc(f.filename)}" class="btn btn-sm btn-secondary" download style="padding:3px 8px;font-size:11px">↓</a>
        </div>`).join('')}
    </div>`;
  };

  el.innerHTML =
    renderGroup('📥 Téléchargements (ADD)',     addFiles,     'ADD')     +
    renderGroup('⬆ Upgrades (UPGRADE)',         upgradeFiles, 'UPGRADE') +
    renderGroup('📋 Autres',                    otherFiles,   'all');
}

async function clearQueue(mode) {
  if (!confirm(`Purger les items ${mode === 'all' ? 'de toute la queue' : 'terminés'} ?`)) return;
  await api('/queue/clear', 'POST', { mode });
  refreshQueue();
  updateQueueBadge();
}


// ══════════════════════════════════════════════════════════
// PAGE: SETTINGS — INCOMING
// ══════════════════════════════════════════════════════════

async function loadIncomingSettings() {
  const d   = await api('/settings/incoming');
  const cfg = await api('/config');

  // Chemins : lecture seule, affiche ce que l'env var a configuré
  const dirInput  = document.getElementById('incoming-dir');
  const destInput = document.getElementById('incoming-download-dir');
  if (dirInput)  dirInput.value  = d.emule_incoming_dir || '(non configuré)';
  if (destInput) destInput.value = cfg.download_dir      || '(non configuré)';

  const toggleOrg  = document.getElementById('toggle-auto-organize');
  const toggleConv = document.getElementById('toggle-auto-convert');
  const toggleRen  = document.getElementById('toggle-rename');
  if (toggleOrg)  toggleOrg.checked  = d.auto_organize        ?? true;
  if (toggleConv) toggleConv.checked = d.auto_convert          ?? true;
  if (toggleRen)  toggleRen.checked  = d.auto_rename_incoming  ?? true;
}

async function saveIncoming() {
  const dir    = document.getElementById('incoming-dir').value.trim();
  const status = document.getElementById('incoming-status');
  const d = await api('/settings/incoming', 'POST', { emule_incoming_dir: dir });
  if (status) {
    status.textContent = d.ok ? 'Sauvegardé ✓' : (d.message || 'Erreur');
    status.className   = 'status-msg ' + (d.ok ? 'ok' : 'error');
    setTimeout(() => { status.textContent = ''; }, 2000);
  }
}

async function saveDownloadDirFromIncoming() {
  const dir    = (document.getElementById('incoming-download-dir') || {}).value?.trim() || '';
  const status = document.getElementById('incoming-dest-status');
  if (!dir) {
    if (status) { status.textContent = 'Chemin requis'; status.className = 'status-msg error'; }
    return;
  }
  // Sauvegarde dans la même clé que Media Management
  const d = await api('/config', 'POST', { download_dir: dir });
  if (status) {
    status.textContent = 'Sauvegardé ✓';
    status.className   = 'status-msg ok';
    setTimeout(() => { status.textContent = ''; }, 2000);
  }
  // Sync aussi le champ dans Media Management
  const mediaInput = document.getElementById('download-dir');
  if (mediaInput) mediaInput.value = dir;
}

async function saveIncomingSettings() {
  const autoOrg  = (document.getElementById('toggle-auto-organize') || {}).checked ?? false;
  const autoConv = (document.getElementById('toggle-auto-convert')  || {}).checked ?? true;
  const autoRen  = (document.getElementById('toggle-rename')        || {}).checked ?? true;
  const d = await api('/settings/incoming', 'POST', {
    auto_organize:        autoOrg,
    auto_convert:         autoConv,
    auto_rename_incoming: autoRen,
  });
  const status = document.getElementById('incoming-settings-status');
  if (status) {
    status.textContent = 'Sauvegardé ✓';
    status.className   = 'status-msg ok';
    setTimeout(() => { status.textContent = ''; }, 2000);
  }
}

// ══════════════════════════════════════════════════════════
// EBDZ SCRAPE — STATUT ET CONTRÔLE
// ══════════════════════════════════════════════════════════

let _ebdzPollInterval = null;

async function loadIndexerConfig() {
  const d = await api('/config');
  if (d.mybbuser) document.getElementById('mybbuser-input').value = d.mybbuser;
  if (d.download_dir) document.getElementById('rename-path').value = d.download_dir;
  // Assure que le bon onglet est affiché
  switchIndexerTab(_indexerTab || 'ebdz');
  await refreshEbdzState();
  await loadScrapeInterval();
}

async function refreshEbdzState() {
  const d = await api('/ebdz/state');
  renderEbdzState(d);
  if (d.running && !_ebdzPollInterval) {
    _ebdzPollInterval = setInterval(async () => {
      const s = await api('/ebdz/state');
      renderEbdzState(s);
      if (!s.running) {
        clearInterval(_ebdzPollInterval);
        _ebdzPollInterval = null;
        document.getElementById('ebdz-scrape-status').textContent = 'Terminé ✓';
        document.getElementById('ebdz-scrape-status').className = 'status-msg ok';
      }
    }, 1500);
  }
}

function renderEbdzState(d) {
  const el = document.getElementById('ebdz-state-card');
  if (!el) return;

  const running     = d.running;
  const page        = d.page  || 0;
  const total       = d.total || 0;
  const threads     = d.threads || 0;
  const mode        = d.mode  || '';
  const lastFull    = d.last_full    ? d.last_full.replace('T',' ') : 'Jamais';
  const lastPartial = d.last_partial ? d.last_partial.replace('T',' ') : 'Jamais';
  const pct         = total > 0 ? Math.round((page / total) * 100) : 0;

  el.innerHTML = `
    <div class="ebdz-state-item">
      <div class="ebdz-state-val">${threads}</div>
      <div class="ebdz-state-lbl">Forums indexés</div>
    </div>
    <div class="ebdz-state-item">
      <div class="ebdz-state-val" style="font-size:12px;color:var(--text-dim)">${lastPartial}</div>
      <div class="ebdz-state-lbl">Dernier scrape partiel</div>
    </div>
    <div class="ebdz-state-item">
      <div class="ebdz-state-val" style="font-size:12px;color:var(--text-dim)">${lastFull}</div>
      <div class="ebdz-state-lbl">Dernier scrape complet</div>
    </div>
    ${running ? `
    <div style="flex:1;min-width:200px">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <span class="ebdz-running">● Scrape ${mode} en cours — page ${page}/${total}</span>
        <span style="font-size:11px;color:var(--accent)">${pct}%</span>
      </div>
      <div class="ebdz-progress">
        <div class="ebdz-progress-fill" style="width:${pct}%"></div>
      </div>
    </div>` : ''}`;
}

async function startEbdzScrape(mode) {
  const status = document.getElementById('ebdz-scrape-status');
  status.textContent = mode === 'full'
    ? 'Lancement du scrape complet (peut prendre plusieurs minutes)…'
    : 'Lancement du scrape partiel…';
  status.className = 'status-msg';

  const d = await api('/ebdz/scrape', 'POST', {
    mode,
    max_pages: mode === 'partial' ? 3 : 9999,
  });

  if (d.ok) {
    status.textContent = d.message;
    status.className = 'status-msg ok';
    // Démarre le polling de l'état
    if (_ebdzPollInterval) clearInterval(_ebdzPollInterval);
    _ebdzPollInterval = setInterval(async () => {
      const s = await api('/ebdz/state');
      renderEbdzState(s);
      if (!s.running) {
        clearInterval(_ebdzPollInterval);
        _ebdzPollInterval = null;
        status.textContent = `Terminé ✓ — ${s.threads} forums indexés`;
        status.className = 'status-msg ok';
      }
    }, 1500);
  } else {
    status.textContent = d.message;
    status.className = 'status-msg error';
  }
}

// ── Fréquence scrape ebdz ──────────────────────────────────────────────────

async function loadScrapeInterval() {
  const d = await api('/settings/scrape-interval');
  const sel = document.getElementById('scrape-interval-select');
  if (!sel) return;
  const hNum = parseInt(d.interval_hours || 12);
  let bestOpt = null, bestDiff = Infinity;
  for (const opt of sel.options) {
    const diff = Math.abs(parseInt(opt.value) - hNum);
    if (diff < bestDiff) { bestDiff = diff; bestOpt = opt; }
  }
  if (bestOpt) bestOpt.selected = true;
}

async function saveScrapeInterval(val) {
  const hours = parseInt(val);
  const status = document.getElementById('interval-save-status');
  const d = await api('/settings/scrape-interval', 'POST', { interval_hours: hours });
  if (d.ok) {
    status.textContent = `Sauvegardé (${hours}h) ✓`;
    status.className = 'status-msg ok';
    setTimeout(() => { status.textContent = ''; }, 2000);
  }
}


// ══════════════════════════════════════════════════════════
// QUEUE — POLL INCOMING (statut Terminé)
// ══════════════════════════════════════════════════════════

let _incomingPollInterval = null;

function startIncomingPoll() {
  if (_incomingPollInterval) return;
  // Poll toutes les 30s quand la page Queue est active
  _incomingPollInterval = setInterval(scanIncoming, 60000); // 60s — le watcher serveur fait le vrai travail
}

function stopIncomingPoll() {
  if (_incomingPollInterval) {
    clearInterval(_incomingPollInterval);
    _incomingPollInterval = null;
  }
}

async function scanIncoming() {
  const d = await api('/queue/scan-incoming');
  if (d.ok && d.updated > 0) {
    // Des statuts ont changé → rafraîchit la liste
    refreshQueue(_queuePage);
    updateQueueBadge();
    showToast(`${d.updated} fichier(s) terminé(s) détecté(s) ✓`);
  }
}



// ══════════════════════════════════════════════════════════
// COLLECTION — SYNCHRONISATION METADATA
// ══════════════════════════════════════════════════════════

async function syncMetadata() {
  const btn    = document.getElementById('btn-sync-meta');
  const status = document.getElementById('sync-meta-status');
  const libId  = document.getElementById('library-select').value;

  if (btn) btn.disabled = true;
  if (status) { status.textContent = 'Lancement…'; status.className = 'status-msg'; }

  const d = await api('/metadata/sync', 'POST', {
    force: false,       // Skip les séries déjà en cache
    library_id: libId || null,
  });

  if (!d.ok) {
    if (status) { status.textContent = d.message || 'Erreur'; status.className = 'status-msg error'; }
    if (btn) btn.disabled = false;
    return;
  }

  // Poll toutes les 1.5s — timeout sécurité à 10 min
  const startTime = Date.now();
  const MAX_MS    = 10 * 60 * 1000;
  const poll = setInterval(async () => {
    // Timeout de sécurité
    if (Date.now() - startTime > MAX_MS) {
      clearInterval(poll);
      if (status) { status.textContent = 'Délai dépassé — vérifiez les logs'; status.className = 'status-msg error'; }
      if (btn) btn.disabled = false;
      return;
    }

    const s = await api('/media/status');
    if (!s.running) {
      clearInterval(poll);
      const lbl = s.label || 'Synchronisé ✓';
      if (status) { status.textContent = lbl; status.className = 'status-msg ok'; }
      if (btn) btn.disabled = false;
      // Recharge la collection pour afficher les nouvelles infos
      const currentLib = document.getElementById('library-select').value;
      if (currentLib) loadSeries(currentLib);
      setTimeout(() => { if (status) status.textContent = ''; }, 5000);
    } else {
      // Affiche la progression en direct
      if (status) { status.textContent = s.label || 'En cours…'; status.className = 'status-msg'; }
    }
  }, 1500);
}


// ══════════════════════════════════════════════════════════
// SETTINGS METADATA — FRÉQUENCE SYNC AUTO
// ══════════════════════════════════════════════════════════

async function loadMetaSyncInterval() {
  const d   = await api('/metadata/sync-interval');
  const sel = document.getElementById('meta-sync-interval');
  if (!sel) return;
  const hNum = parseInt(d.interval_hours ?? 24);
  // Sélectionne l'option dont la valeur est la plus proche
  let bestOpt = null, bestDiff = Infinity;
  for (const opt of sel.options) {
    const diff = Math.abs(parseInt(opt.value) - hNum);
    if (diff < bestDiff) { bestDiff = diff; bestOpt = opt; }
  }
  if (bestOpt) bestOpt.selected = true;
}

async function saveMetaSyncInterval(val) {
  const hours  = parseInt(val);
  const status = document.getElementById('meta-sync-status');
  const d      = await api('/metadata/sync-interval', 'POST', { interval_hours: hours });
  if (d.ok && status) {
    const label = hours === 0 ? 'Désactivée ✓' : `Toutes les ${hours}h ✓`;
    status.textContent = label;
    status.className   = 'status-msg ok';
    setTimeout(() => { status.textContent = ''; }, 2000);
  }
}

// ══════════════════════════════════════════════════════════

// ══════════════════════════════════════════════════════════
// PAGE: SETTINGS — LIBRAIRIES
// ══════════════════════════════════════════════════════════

async function loadLibraries() {
  const container = document.getElementById('libraries-list');
  if (!container) return;
  const d    = await api('/libraries');
  const libs  = d.libraries || [];
  if (!libs.length) {
    container.innerHTML = '<div class="settings-card">' + emptyState('Aucune librairie. Cliquez sur + Ajouter.') + '</div>';
    return;
  }
  container.innerHTML = libs.map(l => `
    <div class="source-card">
      <div class="source-card-header">
        <div class="source-icon" style="background:var(--accent);color:#000">📚</div>
        <div class="source-info">
          <div class="source-name">${esc(l.name)}</div>
          <div class="source-url" style="font-family:monospace">${esc(l.path)}</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn btn-sm btn-secondary" onclick="testLibrary('${esc(l.id)}')">Tester</button>
          <button class="btn btn-sm btn-danger" onclick="deleteLibrary('${esc(l.id)}','${esc(l.name)}')">Supprimer</button>
        </div>
      </div>
      <div id="lib-test-${esc(l.id)}" style="font-size:12px;margin-top:6px;color:var(--text-dim)"></div>
    </div>`).join('');
}

async function testLibrary(id) {
  const el = document.getElementById(`lib-test-${id}`);
  if (el) el.textContent = 'Test…';
  const d = await api(`/libraries/${id}/test`);
  if (el) { el.textContent = d.message || (d.ok ? 'OK' : 'Erreur'); el.style.color = d.ok ? 'var(--success)' : 'var(--danger)'; }
}

async function deleteLibrary(id, name) {
  if (!confirm(`Supprimer la librairie "${name}" ? (les fichiers ne sont pas supprimés)`)) return;
  await api(`/libraries/${id}`, 'DELETE');
  loadLibraries();
  loadCollection();
}

let _browserCurrentPath = '/media';
let _browserSelectedPath = '';

async function showAddLibraryModal() {
  // Démarre à /media (dossier destination monté)
  const dest = await api('/settings/media');
  _browserCurrentPath  = dest.download_dir || '/media';
  _browserSelectedPath = '';
  document.getElementById('modal-file-browser').style.display = 'flex';
  document.getElementById('lib-name-input').value = '';
  document.getElementById('lib-browser-selected').textContent = '';
  document.getElementById('lib-browser-status').textContent   = '';
  await _browserLoad(_browserCurrentPath);
}

function closeFileBrowser() {
  document.getElementById('modal-file-browser').style.display = 'none';
}

async function _browserLoad(path) {
  const list = document.getElementById('lib-browser-list');
  const cur  = document.getElementById('lib-browser-current');
  list.innerHTML = '<div style="color:var(--text-dim);font-size:12px;padding:8px">Chargement…</div>';

  const d = await api(`/browse?path=${encodeURIComponent(path)}`);
  if (cur) cur.textContent = d.current || path;
  _browserCurrentPath = d.current || path;

  if (!d.ok) {
    list.innerHTML = `<div style="color:var(--danger);font-size:12px;padding:8px">${esc(d.message)}</div>`;
    return;
  }

  let html = '';
  // Bouton parent
  if (d.parent) {
    html += `<div class="browser-item browser-parent" onclick="_browserLoad('${esc(d.parent)}')">
      <span class="browser-icon">↑</span>
      <span class="browser-name">.. (dossier parent)</span>
    </div>`;
  }
  if (!d.entries.length) {
    html += '<div style="color:var(--text-dim);font-size:12px;padding:8px">Dossier vide</div>';
  }
  for (const e of d.entries) {
    const sub = e.sub_count > 0 ? `<span class="browser-sub">${e.sub_count} dossier(s)</span>` : '';
    html += `<div class="browser-item" onclick="_browserSelect('${esc(e.path)}', '${esc(e.name)}')"
              ondblclick="_browserLoad('${esc(e.path)}')">
      <span class="browser-icon">📁</span>
      <span class="browser-name">${esc(e.name)}</span>
      ${sub}
      <button class="btn btn-sm btn-secondary" style="margin-left:auto;padding:2px 8px;font-size:11px"
              onclick="event.stopPropagation();_browserLoad('${esc(e.path)}')">Ouvrir →</button>
    </div>`;
  }
  list.innerHTML = html;

  // Si un dossier était sélectionné, le remettre en surbrillance
  if (_browserSelectedPath) {
    _refreshBrowserSelection();
  }
}

function _browserSelect(path, name) {
  _browserSelectedPath = path;
  // Pré-remplit le nom si vide
  const nameInput = document.getElementById('lib-name-input');
  if (nameInput && !nameInput.value) nameInput.value = name;
  // Affiche le chemin sélectionné
  const sel = document.getElementById('lib-browser-selected');
  if (sel) sel.textContent = path;
  _refreshBrowserSelection();
}

function _refreshBrowserSelection() {
  document.querySelectorAll('.browser-item').forEach(el => {
    el.classList.toggle('browser-item-selected',
      el.querySelector('.browser-name')?.textContent === _browserSelectedPath.split('/').pop()
      && _browserCurrentPath === _browserSelectedPath.substring(0, _browserSelectedPath.lastIndexOf('/'))
    );
  });
}

async function confirmAddLibrary() {
  const name   = document.getElementById('lib-name-input').value.trim();
  const path   = _browserSelectedPath || _browserCurrentPath;
  const status = document.getElementById('lib-browser-status');

  if (!name) { status.textContent = 'Donnez un nom à la librairie'; status.style.color = 'var(--danger)'; return; }
  if (!path) { status.textContent = 'Sélectionnez un dossier';     status.style.color = 'var(--danger)'; return; }

  const d = await api('/libraries', 'POST', { name, path });
  if (d.ok) {
    closeFileBrowser();
    showToast(d.message || 'Librairie ajoutée ✓');
    loadLibraries();
    loadCollection();
  } else {
    status.textContent = d.message || 'Erreur';
    status.style.color = 'var(--danger)';
  }
}

// ── Watcher interval ──────────────────────────────────────

async function loadWatcherInterval() {
  const d   = await api('/settings/media');
  const sel = document.getElementById('watcher-interval-select');
  if (!sel) return;
  const v = String(d.watcher_interval || 0);
  [...sel.options].forEach(o => { o.selected = (o.value === v); });
}

async function saveWatcherInterval() {
  const sel      = document.getElementById('watcher-interval-select');
  const status   = document.getElementById('watcher-status');
  const interval = parseInt(sel?.value || '0');
  const d = await api('/settings/media', 'POST', { watcher_interval: interval });
  if (status) {
    status.textContent = interval === 0 ? 'Désactivé ✓' : `Toutes les ${interval/3600}h ✓`;
    status.className   = 'status-msg ok';
    setTimeout(() => { status.textContent = ''; }, 2000);
  }
}

// ── Cache covers ──────────────────────────────────────────

async function loadCoverCacheStats() {
  const d  = await api('/cache/covers/stats');
  const el = document.getElementById('covers-cache-stats');
  if (el) el.textContent = `${d.count || 0} image(s) · ${d.size_kb || 0} Ko · ${d.path || ''}`;
}

async function clearCoversCache() {
  if (!confirm('Vider le cache des couvertures ? Elles seront recréées au prochain chargement.')) return;
  const d      = await api('/cache/covers/clear', 'POST');
  const status = document.getElementById('covers-cache-status');
  if (status) {
    status.textContent = `${d.removed || 0} image(s) supprimée(s) ✓`;
    status.className   = 'status-msg ok';
    setTimeout(() => { status.textContent = ''; }, 3000);
  }
  loadCoverCacheStats();
}

function showItemHistory(hist) {
  const lines = [
    ['Fichier source',    hist.source_file],
    ['Chemin source',     hist.source_path],
    ['Fichier final',     hist.dest_filename],
    ['Chemin final',      hist.dest_path],
    ['Traité le',         hist.processed_at],
    ['Action',            hist.action === 'upgrade' ? '↑ Remplacement (meilleure qualité)' : 'Ajout manquant'],
    ['Fichier remplacé',  hist.owned_replaced || null],
  ].filter(([, v]) => v);

  const html = lines.map(([k, v]) =>
    `<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
      <span style="color:var(--text-dim);min-width:130px;font-size:12px">${esc(k)}</span>
      <span style="font-size:12px;font-family:monospace;word-break:break-all">${esc(v)}</span>
    </div>`
  ).join('');

  // Affiche dans une alert stylisée (ou modal simple)
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:2000;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg-card);border-radius:12px;padding:24px;width:560px;max-width:95vw;border:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3 style="margin:0;font-size:15px">ℹ Historique du tome</h3>
        <button onclick="this.closest('div[style*=fixed]').remove()"
                style="background:none;border:none;color:var(--text-dim);font-size:20px;cursor:pointer">✕</button>
      </div>
      ${html}
    </div>`;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

function editEmuleQueueTome(item) {
  const current = (item.tome_number || '').replace(/T/gi,'');
  const input = prompt(
    `Série : ${item.series_name}\nFichier : ${item.filename}\n\nTome(s) (ex: 5 ou plage 1-30) :`,
    current
  );
  if (input === null) return;
  const tomes = _parseTomeInput(input);
  if (!tomes.length) return;

  const patchData = { key_field: 'filehash', key_value: item.filehash, tomes };
  api('/queue/item', 'PATCH', patchData).then(() => {
    // Recharge la page de queue pour refléter le changement
    if (typeof loadQueue === 'function') loadQueue();
  });
}

async function pollTaskStatus() {
  let _polling = false;
  setInterval(async () => {
    if (_polling) return;
    _polling = true;
    try {
      const s = await api('/media/status');
      const dot = document.getElementById('task-status-dot');
      const lbl = document.getElementById('task-status-label');
      if (dot) dot.classList.toggle('running', s.running);
      if (lbl) lbl.textContent = s.running ? (s.label || 'En cours…') : '';
    } catch(e) {}
    finally { _polling = false; }
  }, 2500);
}

function showSeriesInfo(info) {
  const lines = [
    ['Série',      info.name],
    ['Statut',     info.statut || '—'],
    ['Genres',     (info.genres||[]).join(', ') || '—'],
    ['Tomes',      info.totalVF ? `${info.booksCount} / ${info.totalVF} VF` : String(info.booksCount)],
    ['Dossier',    info.diskPath || '—'],
  ];
  const html = lines.map(([k,v]) =>
    `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--border)">
      <span style="color:var(--text-dim);min-width:80px;font-size:12px">${esc(k)}</span>
      <span style="font-size:12px;word-break:break-all">${esc(v)}</span>
    </div>`).join('');

  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:2000;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg-card);border-radius:12px;padding:24px;width:440px;max-width:95vw;border:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
        <h3 style="margin:0;font-size:14px">ℹ ${esc(info.name)}</h3>
        <button onclick="this.closest('[style*=fixed]').remove()"
                style="background:none;border:none;color:var(--text-dim);font-size:18px;cursor:pointer">✕</button>
      </div>
      ${html}
      <div style="margin-top:14px;text-align:right">
        <a href="/series/${esc(info.slug)}" class="btn btn-sm btn-secondary">Voir la série →</a>
      </div>
    </div>`;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

// ══════════════════════════════════════════════════════════
// COLLECTION — ONGLET AJOUTER SÉRIE
// ══════════════════════════════════════════════════════════

let _collectionTab = 'browse';

function switchCollectionTab(tab) {
  _collectionTab = tab;
  const grid      = document.getElementById('series-grid');
  const search    = document.querySelector('.search-bar-wrap');
  const pag       = document.getElementById('pagination');
  const addPanel  = document.getElementById('panel-add-series');
  const tabBrowse = document.getElementById('tab-collection');
  const tabAdd    = document.getElementById('tab-add');

  if (tab === 'browse') {
    if (grid)    grid.style.display    = '';
    if (search)  search.style.display  = '';
    if (pag)     pag.style.display     = '';
    if (addPanel) addPanel.style.display = 'none';
    if (tabBrowse) tabBrowse.classList.add('active');
    if (tabAdd)    tabAdd.classList.remove('active');
  } else {
    if (grid)    grid.style.display    = 'none';
    if (search)  search.style.display  = 'none';
    if (pag)     pag.style.display     = 'none';
    if (addPanel) addPanel.style.display = '';
    if (tabBrowse) tabBrowse.classList.remove('active');
    if (tabAdd)    tabAdd.classList.add('active');
  }
}

async function searchSeriesToAdd() {
  const q       = (document.getElementById('add-series-search') || {}).value?.trim();
  const status  = document.getElementById('add-series-status');
  const results = document.getElementById('add-series-results');
  if (!q) return;

  results.innerHTML = '<span style="color:var(--text-dim);font-size:12px">Recherche…</span>';
  if (status) status.textContent = '';

  // Cherche dans MangaDB
  const d = await api(`/collection/series/any--any/mangadb-search?q=${encodeURIComponent(q)}`);

  if (!d.ok || !d.results?.length) {
    results.innerHTML = '<span style="color:var(--text-dim);font-size:12px">Aucun résultat dans MangaDB.</span>';
    return;
  }

  const libId = (document.getElementById('library-select') || {}).value || '';

  results.innerHTML = d.results.map(r => `
    <div style="display:flex;align-items:center;justify-content:space-between;
                padding:10px 12px;background:var(--bg-input);border-radius:8px;border:1px solid var(--border)">
      <div>
        <div style="font-size:13px;font-weight:500">${esc(r.titre)}</div>
        <div style="font-size:11px;color:var(--text-dim)">${esc(r.auteur||'')} · ${esc(r.statut||'')}</div>
      </div>
      <button class="btn btn-sm btn-primary"
              onclick="addSeriesToLibrary('${esc(r.titre)}', '${esc(libId)}')">
        + Ajouter
      </button>
    </div>`).join('');
}

async function addSeriesToLibrary(mangadbTitre, libId) {
  const status = document.getElementById('add-series-status');
  if (!libId) {
    if (status) { status.textContent = 'Sélectionnez une librairie'; status.className = 'status-msg error'; }
    return;
  }
  if (status) { status.textContent = 'Création…'; status.className = 'status-msg'; }

  const d = await api(`/libraries/${libId}/create-series`, 'POST', {
    name: mangadbTitre, mangadb_titre: mangadbTitre
  });

  if (status) {
    status.textContent = d.message || (d.ok ? 'Créée ✓' : 'Erreur');
    status.className   = 'status-msg ' + (d.ok ? 'ok' : 'error');
  }
  if (d.ok) {
    setTimeout(() => { switchCollectionTab('browse'); loadSeries(libId); }, 1000);
  }
}

// CSS dynamique pour les onglets
const _tabStyle = document.createElement('style');
_tabStyle.textContent = `
  .tab-pill { padding:5px 14px;border-radius:20px;border:1px solid var(--border);
              background:var(--bg-input);color:var(--text-dim);font-size:12px;cursor:pointer; }
  .tab-pill.active { background:var(--accent);color:#000;border-color:var(--accent);font-weight:600; }
  .tab-pill:hover:not(.active) { border-color:var(--accent);color:var(--accent); }
  .series-info-btn { position:absolute;top:6px;right:6px;background:rgba(0,0,0,.6);
                     border:none;border-radius:50%;width:22px;height:22px;color:#fff;
                     font-size:11px;cursor:pointer;display:flex;align-items:center;justify-content:center; }
  .series-info-btn:hover { background:var(--accent);color:#000; }
  .series-cover-wrap { position:relative; }
  /* Torznab / DC cards */
  .indexer-card, .dc-card {
    display:flex;align-items:center;justify-content:space-between;
    padding:12px 16px;background:var(--bg-input);border-radius:8px;
    border:1px solid var(--border);margin-bottom:8px;gap:10px;flex-wrap:wrap;
  }
  .indexer-card-info, .dc-card-info { flex:1;min-width:0; }
  .indexer-card-name, .dc-card-name { font-weight:600;font-size:13px; }
  .indexer-card-url, .dc-card-url { font-size:11px;color:var(--text-dim);word-break:break-all;margin-top:2px; }
  .indexer-card-actions, .dc-card-actions { display:flex;gap:6px;align-items:center;flex-shrink:0; }
  .release-card {
    padding:10px 14px;background:var(--bg-input);border-radius:8px;
    border:1px solid var(--border);margin-bottom:6px;
  }
  .release-card.integrale { border-color:var(--warning); }
  .release-card.pack      { border-color:var(--accent); }
  .release-title { font-size:12px;font-weight:500;word-break:break-all;margin-bottom:4px; }
  .release-meta  { font-size:11px;color:var(--text-dim);display:flex;gap:10px;flex-wrap:wrap; }
  .release-badge {
    display:inline-block;padding:1px 6px;border-radius:10px;font-size:10px;font-weight:600;
    text-transform:uppercase;
  }
  .release-badge.integrale { background:rgba(251,191,36,.2);color:var(--warning); }
  .release-badge.pack      { background:rgba(0,181,204,.2);color:var(--accent); }
  .release-badge.single    { background:rgba(34,197,94,.2);color:var(--success); }
  .release-badge.unknown   { background:rgba(156,163,175,.2);color:var(--text-dim); }
  .tome-badge-row { display:flex;gap:3px;flex-wrap:wrap;margin-top:4px; }
  .tome-badge {
    padding:2px 5px;border-radius:4px;font-size:10px;cursor:pointer;
    border:1px solid var(--border);background:var(--bg-input);
    transition:background .15s;
  }
  .tome-badge.owned    { background:rgba(34,197,94,.15);border-color:var(--success);color:var(--success); }
  .tome-badge.missing  { background:rgba(251,191,36,.15);border-color:var(--warning);color:var(--warning); }
  .tome-badge.selected { background:var(--accent);border-color:var(--accent);color:#000; }
  .monitor-series-card {
    padding:12px 16px;background:var(--bg-input);border-radius:8px;
    border:1px solid var(--border);margin-bottom:8px;cursor:pointer;transition:border-color .15s;
  }
  .monitor-series-card:hover { border-color:var(--accent); }
  .monitor-series-card .ms-name { font-weight:600;font-size:13px; }
  .monitor-series-card .ms-meta { font-size:11px;color:var(--text-dim);margin-top:3px; }
  .monitor-series-card .ms-badges { display:flex;gap:6px;margin-top:6px;flex-wrap:wrap; }
`;

// ══════════════════════════════════════════════════════════
// PAGE: INDEXERS — TABS
// ══════════════════════════════════════════════════════════

let _indexerTab = 'ebdz';

function switchIndexerTab(tab) {
  _indexerTab = tab;
  document.getElementById('indexer-panel-ebdz').style.display    = tab === 'ebdz'    ? '' : 'none';
  document.getElementById('indexer-panel-torznab').style.display = tab === 'torznab' ? '' : 'none';
  document.getElementById('tab-idx-ebdz').classList.toggle('active',    tab === 'ebdz');
  document.getElementById('tab-idx-torznab').classList.toggle('active', tab === 'torznab');
  if (tab === 'torznab') loadTorznabIndexers();
}

// ══════════════════════════════════════════════════════════
// PAGE: INDEXERS — TORZNAB
// ══════════════════════════════════════════════════════════

async function loadTorznabIndexers() {
  const list = document.getElementById('torznab-indexers-list');
  if (!list) return;
  const d = await api('/indexers/torznab');
  const indexers = d.indexers || [];
  if (!indexers.length) {
    list.innerHTML = '<p style="color:var(--text-dim);font-size:12px">Aucun indexer configuré.</p>';
    return;
  }
  list.innerHTML = indexers.map(idx => `
    <div class="indexer-card">
      <div class="indexer-card-info">
        <div class="indexer-card-name">${esc(idx.name)}</div>
        <div class="indexer-card-url">${esc(idx.url)}</div>
      </div>
      <div class="indexer-card-actions">
        <label class="toggle" title="${idx.enabled ? 'Actif' : 'Désactivé'}">
          <input type="checkbox" ${idx.enabled ? 'checked' : ''}
                 onchange="toggleTorznabIndexer('${esc(idx.id)}', this.checked)">
          <span class="toggle-slider"></span>
        </label>
        <button class="btn btn-sm btn-secondary" onclick="testTorznabIndexer('${esc(idx.id)}', this)">Tester</button>
        <button class="btn btn-sm btn-danger"    onclick="deleteTorznabIndexer('${esc(idx.id)}')">✕</button>
      </div>
    </div>`).join('');
}

async function addTorznabIndexer() {
  const name   = (document.getElementById('torznab-name')   || {}).value?.trim();
  const url    = (document.getElementById('torznab-url')    || {}).value?.trim();
  const apikey = (document.getElementById('torznab-apikey') || {}).value?.trim();
  const status = document.getElementById('torznab-add-status');
  if (!name || !url) {
    if (status) { status.textContent = 'Nom et URL requis'; status.className = 'status-msg error'; }
    return;
  }
  if (status) { status.textContent = 'Ajout…'; status.className = 'status-msg'; }
  const d = await api('/indexers/torznab', 'POST', { name, url, apikey });
  if (status) {
    status.textContent = d.ok ? 'Ajouté ✓' : (d.message || 'Erreur');
    status.className   = 'status-msg ' + (d.ok ? 'ok' : 'error');
  }
  if (d.ok) {
    document.getElementById('torznab-name').value   = '';
    document.getElementById('torznab-url').value    = '';
    document.getElementById('torznab-apikey').value = '';
    loadTorznabIndexers();
    setTimeout(() => { if (status) status.textContent = ''; }, 2500);
  }
}

async function deleteTorznabIndexer(id) {
  if (!confirm('Supprimer cet indexer ?')) return;
  await api(`/indexers/torznab/${id}`, 'DELETE');
  loadTorznabIndexers();
}

async function toggleTorznabIndexer(id, enabled) {
  await api(`/indexers/torznab/${id}`, 'PATCH', { enabled });
}

async function testTorznabIndexer(id, btn) {
  if (btn) { btn.textContent = '…'; btn.disabled = true; }
  const d = await api(`/indexers/torznab/${id}/test`, 'POST', {});
  if (btn) {
    btn.textContent = d.ok ? '✓' : '✗';
    btn.title = d.message || '';
    btn.disabled = false;
    setTimeout(() => { btn.textContent = 'Tester'; }, 2500);
  }
  showToast(d.message || (d.ok ? 'OK' : 'Erreur'), d.ok ? 'ok' : 'error');
}

// ══════════════════════════════════════════════════════════
// PAGE: SETTINGS — DOWNLOAD CLIENT
// ══════════════════════════════════════════════════════════

async function loadDownloadClients() {
  // Affiche le chemin du dossier de surveillance qBittorrent (depuis env var container)
  const info = await api('/settings/download-clients/info');
  const dirInput = document.getElementById('qbt-watch-dir');
  if (dirInput) dirInput.value = info.qbt_watch_dir || '(non configuré — définir MANGAARR_QBT_WATCH dans docker-compose.yml)';

  const list = document.getElementById('download-clients-list');
  if (!list) return;
  const d = await api('/settings/download-clients');
  const clients = d.clients || [];
  if (!clients.length) {
    list.innerHTML = '<p style="color:var(--text-dim);font-size:12px">Aucun client configuré.</p>';
    return;
  }
  list.innerHTML = clients.map(c => `
    <div class="dc-card">
      <div class="dc-card-info">
        <div class="dc-card-name">${esc(c.name)}</div>
        <div class="dc-card-url">${esc(c.host)}:${c.port} · catégorie : <em>${esc(c.category||'(aucune)')}</em></div>
      </div>
      <div class="dc-card-actions">
        <label class="toggle" title="${c.enabled ? 'Actif' : 'Désactivé'}">
          <input type="checkbox" ${c.enabled ? 'checked' : ''}
                 onchange="toggleDownloadClient('${esc(c.id)}', this.checked)">
          <span class="toggle-slider"></span>
        </label>
        <button class="btn btn-sm btn-secondary" onclick="testDownloadClient('${esc(c.id)}', this)">Tester</button>
        <button class="btn btn-sm btn-danger"    onclick="deleteDownloadClient('${esc(c.id)}')">✕</button>
      </div>
    </div>`).join('');
}

async function addDownloadClient() {
  const name      = (document.getElementById('dc-name')      || {}).value?.trim();
  const host      = (document.getElementById('dc-host')      || {}).value?.trim();
  const port      = parseInt((document.getElementById('dc-port') || {}).value || '8080');
  const username  = (document.getElementById('dc-username')  || {}).value?.trim();
  const password  = (document.getElementById('dc-password')  || {}).value?.trim();
  const category  = (document.getElementById('dc-category')  || {}).value?.trim();
  const status = document.getElementById('dc-add-status');

  if (!name || !host) {
    if (status) { status.textContent = 'Nom et host requis'; status.className = 'status-msg error'; }
    return;
  }
  if (status) { status.textContent = 'Ajout…'; status.className = 'status-msg'; }
  const d = await api('/settings/download-clients', 'POST', { name, host, port, username, password, category });
  if (status) {
    status.textContent = d.ok ? 'Ajouté ✓' : (d.message || 'Erreur');
    status.className   = 'status-msg ' + (d.ok ? 'ok' : 'error');
  }
  if (d.ok) {
    ['dc-name','dc-host','dc-username','dc-password','dc-category'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    document.getElementById('dc-port').value = '8080';
    loadDownloadClients();
    setTimeout(() => { if (status) status.textContent = ''; }, 2500);
  }
}

async function deleteDownloadClient(id) {
  if (!confirm('Supprimer ce client ?')) return;
  await api(`/settings/download-clients/${id}`, 'DELETE');
  loadDownloadClients();
}

async function toggleDownloadClient(id, enabled) {
  await api(`/settings/download-clients/${id}`, 'PATCH', { enabled });
}

async function testDownloadClient(id, btn) {
  if (btn) { btn.textContent = '…'; btn.disabled = true; }
  const d = await api(`/settings/download-clients/${id}/test`, 'POST', {});
  if (btn) {
    btn.textContent = d.ok ? '✓' : '✗';
    btn.title = d.message || '';
    btn.disabled = false;
    setTimeout(() => { btn.textContent = 'Tester'; }, 2500);
  }
  showToast(d.message || (d.ok ? 'OK' : 'Erreur'), d.ok ? 'ok' : 'error');
}

// ══════════════════════════════════════════════════════════
// PAGE: QUEUE — ONGLETS eMule / Torrent / Surveillance
// ══════════════════════════════════════════════════════════

let _queueActiveTab = 'emule';

function switchQueueTab(tab) {
  _queueActiveTab = tab;
  ['emule', 'torrent', 'monitor'].forEach(t => {
    const panel = document.getElementById(`queue-panel-${t}`);
    const btn   = document.getElementById(`tab-queue-${t}`);
    if (panel) panel.style.display = t === tab ? '' : 'none';
    if (btn)   btn.classList.toggle('active', t === tab);
  });
  if (tab === 'torrent') loadTorrentQueue();
  if (tab === 'monitor') { /* user clicks "Analyser" manually */ }
}

// ── Queue Torrent ──
let _torrentQueueItems = [];
let _torrentQueuePage  = 1;
let _torrentSortDir    = 'asc';
const TORRENT_PAGE_SIZE = 20;

// Registres pour l'ouverture du modal "Action en attente"
// (évite la sérialisation JSON dans les attributs onclick)
let _pendingActionItem   = null;
let _emulePendingList    = [];
let _torrentPendingList  = [];

async function loadTorrentQueue() {
  const d = await api('/torrent/queue');
  _torrentQueueItems = d.items || [];
  renderTorrentQueueStats();
  filterTorrentQueueTable();
  updateTorrentQueueBadge();
}

function renderTorrentQueueStats() {
  const bar = document.getElementById('torrent-queue-stats');
  if (!bar) return;
  const items = _torrentQueueItems;
  const pending = items.filter(i => i.status === 'pending').length;
  const done    = items.filter(i => i.status === 'done').length;
  bar.innerHTML = `
    <div class="queue-stat"><div class="queue-stat-val">${items.length}</div><div class="queue-stat-lbl">Total</div></div>
    <div class="queue-stat"><div class="queue-stat-val" style="color:var(--warning)">${pending}</div><div class="queue-stat-lbl">En attente</div></div>
    <div class="queue-stat"><div class="queue-stat-val" style="color:var(--success)">${done}</div><div class="queue-stat-lbl">Terminé</div></div>`;
}

function sortTorrentQueueBy(col) {
  if (col !== 'series') return;
  _torrentSortDir = _torrentSortDir === 'asc' ? 'desc' : 'asc';
  const icon = document.getElementById('torrent-sort-icon');
  if (icon) icon.textContent = _torrentSortDir === 'asc' ? '↑' : '↓';
  _torrentQueuePage = 1;
  filterTorrentQueueTable();
}

function filterTorrentQueueTable() {
  const q = ((document.getElementById('torrent-queue-search') || {}).value || '').toLowerCase();
  let items = [..._torrentQueueItems];
  if (q) items = items.filter(i =>
    (i.series_name || '').toLowerCase().includes(q) ||
    (i.filename    || '').toLowerCase().includes(q));
  items.sort((a, b) => {
    const na = (a.series_name || '').toLowerCase();
    const nb = (b.series_name || '').toLowerCase();
    return _torrentSortDir === 'asc' ? na.localeCompare(nb) : nb.localeCompare(na);
  });
  const total = items.length;
  const start = (_torrentQueuePage - 1) * TORRENT_PAGE_SIZE;
  renderTorrentQueueTable(items.slice(start, start + TORRENT_PAGE_SIZE));
  // Pagination simple
  const pag = document.getElementById('torrent-queue-pagination');
  if (pag) {
    const pages = Math.ceil(total / TORRENT_PAGE_SIZE);
    pag.innerHTML = pages <= 1 ? '' : Array.from({length:pages},(_,i)=>
      `<button class="page-btn ${i+1===_torrentQueuePage?'active':''}" onclick="_torrentQueuePage=${i+1};filterTorrentQueueTable()">${i+1}</button>`
    ).join('');
  }
}

function renderTorrentQueueTable(items) {
  const tbody = document.getElementById('torrent-queue-tbody');
  const empty = document.getElementById('torrent-queue-empty');
  if (!items.length) {
    if (tbody) tbody.innerHTML = '';
    if (empty) empty.style.display = '';
    return;
  }
  if (empty) empty.style.display = 'none';

  const typeLabel = { single:'Tome', pack:'Pack', integrale:'Intégrale', unknown:'?' };
  _torrentPendingList = items;  // référence pour openPendingActionModal
  if (tbody) tbody.innerHTML = items.map((item, i) => {
    // Statut + couleur
    const isPending = item.status === 'action_pending';
    const sCls = item.status === 'done'           ? 'done'
               : item.status === 'downloading'    ? 'downloading'
               : isPending                        ? 'action-pending'
               : 'pending';
    const sLbl = item.status === 'done'           ? 'Terminé'
               : item.status === 'downloading'    ? 'En cours'
               : isPending                        ? 'ACTION EN ATTENTE'
               : 'En attente';

    const seriesLink = item.series_slug
      ? `<a class="queue-series-link" href="/series/${esc(item.series_slug)}">${esc(item.series_name||'—')}</a>`
      : `<span>${esc(item.series_name||'—')}</span>`;

    const tomes = item.tomes?.length
      ? item.tomes.map(n => `T${String(n).padStart(2,'0')}`).join(', ')
      : esc(item.tome_number || '?');
    const vtype = item.vol_type || 'unknown';

    // Nom de release — cliquable si release_url disponible
    const rawFilename = item.filename || '';
    const releaseCell = item.release_url
      ? `<a href="${esc(item.release_url)}" target="_blank" rel="noopener"
            style="color:var(--accent);font-weight:600;text-decoration:none"
            title="Voir la release sur l'indexer">${esc(rawFilename.substring(0,50))}${rawFilename.length>50?'…':''}</a>`
      : `<span title="${esc(rawFilename)}">${esc(rawFilename.substring(0,50))}${rawFilename.length>50?'…':''}</span>`;

    // Bouton ✎ édition tome
    const editBtn = `<button class="btn-info-hist" title="Modifier les tomes" style="margin-left:4px"
      onclick="editTorrentQueueTomes(${i})">✎</button>`;

    // Bouton ℹ historique
    const hist = item.history;
    let infoBtn = '';
    if (hist) {
      const lines = [
        hist.source_file   ? `Source    : ${hist.source_file}`   : '',
        hist.dest_filename ? `Final     : ${hist.dest_filename}`  : '',
        hist.processed_at  ? `Traité le : ${hist.processed_at}`  : '',
      ].filter(Boolean).join('\n');
      infoBtn = `<button class="btn-info-hist" title="${esc(lines)}"
        onclick="showTorrentHistory(${esc(JSON.stringify({...hist, indexer: item.indexer||''}))})">ℹ</button>`;
    }

    // Bouton ACTION EN ATTENTE
    const pendingBtn = isPending
      ? `<button class="btn-info-hist" title="Cliquez pour gérer les conflits"
               style="margin-left:4px;background:var(--warning,#f59e0b);color:#000;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700;cursor:pointer"
               onclick="_openTorrentPending(${i})">▶ Gérer</button>`
      : '';

    // Checkbox de sélection
    const itemKey = esc(item.torrent_link || item.filename || '');
    const chk = `<input type="checkbox" class="torrent-item-chk" value="${itemKey}" onchange="_onTorrentCheckChange()">`;

    // Bouton forcer organisation (si activé dans Media Management)
    const forceBtn = (_forceOrganizeEnabled && item.status !== 'done')
      ? `<button class="btn-info-hist" title="Forcer organisation" style="margin-left:4px"
           onclick="forceOrganizeTorrent(${esc(JSON.stringify(item))})">⚙</button>`
      : '';

    return `<tr data-series="${esc((item.series_name||'').toLowerCase())}">
      <td style="text-align:center">${chk}</td>
      <td>${seriesLink}</td>
      <td class="queue-filename">${releaseCell}</td>
      <td class="queue-tome">${tomes}${editBtn}</td>
      <td><span class="release-badge ${vtype}">${typeLabel[vtype]||vtype}</span></td>
      <td style="display:flex;align-items:center;gap:6px;flex-wrap:wrap"><span class="status-pill ${sCls}">${sLbl}</span>${pendingBtn}${infoBtn}${forceBtn}</td>
    </tr>`;
  }).join('');
}

function _onTorrentCheckChange() {
  const any = document.querySelectorAll('.torrent-item-chk:checked').length > 0;
  const btn = document.getElementById('btn-delete-torrent');
  if (btn) btn.style.display = any ? '' : 'none';
}

function toggleSelectAllTorrent(cb) {
  document.querySelectorAll('.torrent-item-chk').forEach(c => c.checked = cb.checked);
  _onTorrentCheckChange();
}

async function deleteSelectedTorrent() {
  const checked = [...document.querySelectorAll('.torrent-item-chk:checked')];
  if (!checked.length) return;
  const keys = checked.map(c => c.value);
  if (!confirm(`Supprimer ${keys.length} item(s) torrent de la queue ?`)) return;
  await api('/torrent/queue/items', 'DELETE', { keys });
  document.getElementById('torrent-select-all').checked = false;
  const btn = document.getElementById('btn-delete-torrent');
  if (btn) btn.style.display = 'none';
  await loadTorrentQueue();
  showToast(`${keys.length} item(s) supprimé(s) ✓`);
}

// ═══════════════════════════════════════════════════
// MODAL ACTION EN ATTENTE (conflits upgrade)
// ═══════════════════════════════════════════════════

function _openEmulePending(idx)   { openPendingActionModal(_emulePendingList[idx]);   }
function _openTorrentPending(idx) { openPendingActionModal(_torrentPendingList[idx]); }

function openPendingActionModal(item) {
  _pendingActionItem = item;
  const old = document.getElementById('pending-action-modal');
  if (old) old.remove();

  const pending   = item.pending_action || {};
  const conflicts = pending.conflicts   || [];
  const isEmule   = item.source !== 'torrent';

  const rowsHtml = conflicts.map(c => `
    <tr>
      <td style="text-align:center;padding:8px">
        <input type="checkbox" class="conflict-chk" value="${c.tome}" checked
               style="accent-color:var(--accent);width:16px;height:16px">
      </td>
      <td style="padding:8px;font-weight:600;white-space:nowrap">T${String(c.tome).padStart(2,'0')}</td>
      <td style="padding:8px;font-size:12px;font-family:monospace;color:var(--text-dim);word-break:break-all">${esc(c.current_file)}</td>
      <td style="padding:8px;font-size:12px;font-family:monospace;word-break:break-all">${esc(c.new_file)}</td>
    </tr>`).join('');

  const modal = document.createElement('div');
  modal.id = 'pending-action-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:3000;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg-card);border-radius:12px;border:1px solid var(--border);
                width:820px;max-width:96vw;max-height:90vh;display:flex;flex-direction:column;overflow:hidden">

      <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
        <div>
          <h3 style="margin:0 0 4px;font-size:15px">⚠ Action en attente — Conflits détectés</h3>
          <div style="font-size:12px;color:var(--text-dim)">
            La collection possède déjà le(s) Tome(s) suivant(s) pour
            <strong>${esc(item.series_name || '?')}</strong>
          </div>
        </div>
        <button onclick="document.getElementById('pending-action-modal').remove()"
                style="background:none;border:none;color:var(--text-dim);font-size:20px;cursor:pointer">✕</button>
      </div>

      <div style="padding:12px 16px;border-bottom:1px solid var(--border);font-size:13px;color:var(--text-dim)">
        Sélectionnez le(s) Tome(s) que vous souhaitez remplacer :
      </div>

      <div style="overflow-y:auto;flex:1">
        <table style="width:100%;border-collapse:collapse">
          <thead>
            <tr style="background:var(--bg-sidebar);font-size:12px;color:var(--text-dim)">
              <th style="padding:8px;width:40px">
                <input type="checkbox" id="conflict-select-all" checked onchange="
                  document.querySelectorAll('.conflict-chk').forEach(c=>c.checked=this.checked)"
                style="accent-color:var(--accent)">
              </th>
              <th style="padding:8px;text-align:left">Tome</th>
              <th style="padding:8px;text-align:left">Fichier actuel</th>
              <th style="padding:8px;text-align:left">Nouveau fichier</th>
            </tr>
          </thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>

      <div id="pending-action-error" style="display:none;padding:8px 20px;color:var(--danger);font-size:13px;font-weight:600"></div>

      <div style="padding:14px 20px;border-top:1px solid var(--border);display:flex;gap:10px;justify-content:flex-end">
        <button onclick="_resolvePending(true)"
                style="padding:9px 20px;border-radius:8px;border:1px solid var(--border);
                       background:none;color:var(--text);cursor:pointer;font-size:13px">
          Ne pas remplacer
        </button>
        <button onclick="_resolvePending(false)"
                style="padding:9px 20px;border-radius:8px;border:none;background:var(--accent);
                       color:#fff;cursor:pointer;font-size:13px;font-weight:600">
          ✓ Valider
        </button>
      </div>
    </div>`;

  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

async function _resolvePending(skipAll) {
  const item  = _pendingActionItem;
  if (!item) { alert('Erreur interne : item non trouvé.'); return; }

  const errEl = document.getElementById('pending-action-error');
  if (errEl) errEl.style.display = 'none';

  let replace_tomes = [];
  if (!skipAll) {
    replace_tomes = [...document.querySelectorAll('.conflict-chk:checked')].map(c => parseInt(c.value));
    if (replace_tomes.length === 0) {
      if (errEl) {
        errEl.textContent = 'Sélectionnez le(s) Tome(s) à remplacer pour valider.';
        errEl.style.display = '';
      }
      return;
    }
  }

  const body = {
    filehash:      item.filehash     || '',
    torrent_link:  item.torrent_link || '',
    series_name:   item.series_name  || '',
    filename:      item.filename     || '',
    replace_tomes,
    skip_all:      skipAll,
  };

  const res = await api('/queue/resolve-pending', 'POST', body);
  document.getElementById('pending-action-modal')?.remove();

  if (res.ok) {
    showToast(skipAll ? 'Aucun remplacement effectué ✓' : `✓ ${res.message}`);
    refreshQueue();
    loadTorrentQueue();
  } else {
    alert(`Erreur : ${res.message || 'Inconnue'}`);
  }
}


// ═══════════════════════════════════════════════════
// NAVIGATEUR DE FICHIERS — force organize
// ═══════════════════════════════════════════════════

let _fileBrowserItem    = null;   // Item torrent en cours
let _fileBrowserSelPath = null;   // Chemin sélectionné
let _fbCurrentPath      = '';     // Répertoire courant affiché

const _FB_MANGA_EXTS = new Set(['.cbz', '.cbr', '.pdf', '.zip']);

function forceOrganizeTorrent(item) {
  _fileBrowserItem    = item;
  _fileBrowserSelPath = null;
  _openFileBrowserModal(item);
}

function _openFileBrowserModal(item) {
  const old = document.getElementById('file-browser-modal');
  if (old) old.remove();

  const typeHint = (item.vol_type === 'pack' || item.vol_type === 'integrale')
    ? 'Sélectionnez le <strong>dossier</strong> contenant les fichiers du pack'
    : 'Sélectionnez le <strong>fichier</strong> manga (.cbz / .cbr / .pdf)';

  const tomes = item.tomes?.length
    ? item.tomes.map(n => `T${String(n).padStart(2,'0')}`).join(', ')
    : (item.tome_number || '');

  const modal = document.createElement('div');
  modal.id = 'file-browser-modal';
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:3000;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `
    <div style="background:var(--bg-card);border-radius:12px;border:1px solid var(--border);
                width:700px;max-width:95vw;max-height:90vh;display:flex;flex-direction:column;overflow:hidden">

      <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <h3 style="margin:0 0 4px;font-size:15px">⚙ Forcer l'organisation</h3>
          <div style="font-size:12px;color:var(--text-dim)">
            <strong>${esc(item.series_name)}</strong>
            ${tomes ? ` &mdash; ${esc(tomes)}` : ''}
            &nbsp;&middot;&nbsp; ${typeHint}
          </div>
        </div>
        <button onclick="document.getElementById('file-browser-modal').remove()"
                style="background:none;border:none;color:var(--text-dim);font-size:20px;cursor:pointer;padding:0;line-height:1">✕</button>
      </div>

      <div style="padding:8px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
        <button id="fb-btn-up" onclick="_fbUp()" disabled
                style="background:none;border:1px solid var(--border);border-radius:6px;color:var(--text);
                       padding:4px 10px;cursor:pointer;font-size:13px">↑</button>
        <div id="fb-breadcrumb" style="font-size:12px;font-family:monospace;color:var(--text-dim);
                                       flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></div>
      </div>

      <div id="fb-list" style="flex:1;overflow-y:auto;min-height:200px;max-height:400px">
        <div style="padding:20px;text-align:center;color:var(--text-dim)">Chargement…</div>
      </div>

      <div style="padding:10px 16px;border-top:1px solid var(--border);background:var(--bg-sidebar)">
        <div style="font-size:12px;color:var(--text-dim);margin-bottom:4px">Sélectionné :</div>
        <div id="fb-selected" style="font-size:12px;font-family:monospace;color:var(--accent);
                                     word-break:break-all;min-height:18px"></div>
      </div>

      <div style="padding:12px 16px;border-top:1px solid var(--border);display:flex;gap:10px;justify-content:flex-end">
        <button onclick="document.getElementById('file-browser-modal').remove()"
                style="padding:8px 18px;border-radius:8px;border:1px solid var(--border);
                       background:none;color:var(--text);cursor:pointer;font-size:13px">Annuler</button>
        <button id="fb-btn-validate" onclick="_fbValidate()" disabled
                style="padding:8px 18px;border-radius:8px;border:none;background:var(--accent);
                       color:#fff;cursor:pointer;font-size:13px;opacity:.4">✓ Valider</button>
      </div>
    </div>`;

  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
  _fbNavigate('');
}

async function _fbNavigate(path) {
  const listEl  = document.getElementById('fb-list');
  const crumbEl = document.getElementById('fb-breadcrumb');
  if (listEl) listEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim)">Chargement…</div>';

  const url = path ? `/files/browse?path=${encodeURIComponent(path)}` : '/files/browse';
  const d   = await api(url);

  if (!d.ok) {
    if (listEl) listEl.innerHTML = `<div style="padding:20px;color:var(--danger)">${esc(d.message || 'Erreur')}</div>`;
    return;
  }
  _fbCurrentPath = d.path;

  // Bouton Up
  const upBtn = document.getElementById('fb-btn-up');
  if (upBtn) upBtn.disabled = !d.parent;

  // Breadcrumb
  if (crumbEl) {
    const parts = d.path.split('/').filter(Boolean);
    let html = `<span style="cursor:pointer;color:var(--accent)" onclick="_fbNavigate('')">/ </span>`;
    let cumul = '';
    parts.forEach((p, i) => {
      cumul += '/' + p;
      const target = JSON.stringify(cumul);
      html += i < parts.length - 1
        ? `<span style="cursor:pointer;color:var(--accent)" onclick="_fbNavigate(${target})">${esc(p)}</span><span style="color:var(--text-dim)">/</span>`
        : `<span style="color:var(--text)">${esc(p)}</span>`;
    });
    crumbEl.innerHTML = html;
  }

  if (!listEl) return;
  if (!d.items.length) {
    listEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim)">Dossier vide</div>';
    return;
  }

  const volType = _fileBrowserItem?.vol_type || 'single';
  let html = '';

  // Option "sélectionner ce dossier" pour les packs/intégrales
  if (volType === 'pack' || volType === 'integrale') {
    const p = JSON.stringify(d.path);
    const dirName = d.path.split('/').filter(Boolean).pop() || '/';
    html += `<div onclick="_fbSelect(${p}, 'dir')"
               style="padding:8px 16px;cursor:pointer;display:flex;align-items:center;gap:10px;
                      border-bottom:1px solid var(--border);color:var(--accent);font-size:13px">
      <span style="font-size:16px">📂</span>
      <span>↳ Sélectionner ce dossier : <strong>${esc(dirName)}</strong></span>
    </div>`;
  }

  html += d.items.map(it => {
    const isDir   = it.type === 'dir';
    const isManga = !isDir && _FB_MANGA_EXTS.has(it.ext);
    const full    = (d.path.endsWith('/') ? d.path : d.path + '/') + it.name;
    const icon    = isDir ? '📁' : (isManga ? '📄' : '📎');
    const sz      = !isDir && it.size > 0
      ? `<span style="font-size:11px;color:var(--text-dim);margin-left:auto">${(it.size/1024/1024).toFixed(1)} Mo</span>`
      : '';
    const p = JSON.stringify(full);

    if (isDir) {
      return `<div style="padding:8px 16px;display:flex;align-items:center;gap:10px;cursor:pointer"
                   onclick="_fbNavigate(${p})">
        <span style="font-size:16px">${icon}</span>
        <span style="font-size:13px">${esc(it.name)}/</span>
        ${sz}
      </div>`;
    } else if (isManga) {
      return `<div class="fb-file-item" data-path="${esc(full)}"
                   style="padding:8px 16px;display:flex;align-items:center;gap:10px;cursor:pointer"
                   onclick="_fbSelect(${p}, 'file')">
        <span style="font-size:16px">${icon}</span>
        <span style="font-size:13px">${esc(it.name)}</span>
        ${sz}
      </div>`;
    } else {
      return `<div style="padding:8px 16px;display:flex;align-items:center;gap:10px;opacity:.4">
        <span style="font-size:16px">${icon}</span>
        <span style="font-size:13px;color:var(--text-dim)">${esc(it.name)}</span>
        ${sz}
      </div>`;
    }
  }).join('');

  listEl.innerHTML = html;
}

function _fbUp() {
  if (!_fbCurrentPath || _fbCurrentPath === '/') return;
  const parent = _fbCurrentPath.replace(/\/[^/]+\/?$/, '') || '/';
  _fbNavigate(parent);
}

function _fbSelect(path, type) {
  _fileBrowserSelPath = path;
  document.querySelectorAll('.fb-file-item').forEach(el => {
    el.style.background = el.dataset.path === path ? 'rgba(99,102,241,.15)' : '';
  });
  const selEl = document.getElementById('fb-selected');
  if (selEl) selEl.textContent = (type === 'dir' ? '📂  ' : '📄  ') + path;
  const btn = document.getElementById('fb-btn-validate');
  if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
}

async function _fbValidate() {
  if (!_fileBrowserSelPath || !_fileBrowserItem) return;
  const btn = document.getElementById('fb-btn-validate');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ En cours…'; }

  const item = _fileBrowserItem;
  const res  = await api('/queue/force-organize', 'POST', {
    local_path:    _fileBrowserSelPath,
    series_name:   item.series_name   || '',
    tome_number:   item.tome_number   || '',
    tomes:         item.tomes         || [],
    vol_type:      item.vol_type      || 'single',
    torrent_link:  item.torrent_link  || '',
    filename:      item.filename      || '',
    replace_tomes: item.replace_tomes || [],
  });

  const modal   = document.getElementById('file-browser-modal');
  if (!modal) return;
  const content = modal.querySelector('div');

  if (res.ok) {
    content.innerHTML = `
      <div style="padding:40px;text-align:center">
        <div style="font-size:52px;margin-bottom:16px">✅</div>
        <h3 style="margin:0 0 8px;font-size:16px;color:var(--success)">${esc(res.message || 'Organisé avec succès')}</h3>
        <p style="font-size:13px;color:var(--text-dim);margin:0 0 24px">
          ${esc(item.series_name)} &mdash; ${esc(_fileBrowserSelPath.split('/').pop())}
        </p>
        <button onclick="document.getElementById('file-browser-modal').remove();loadTorrentQueue()"
                style="padding:10px 24px;border-radius:8px;border:none;background:var(--accent);
                       color:#fff;cursor:pointer;font-size:14px">OK</button>
      </div>`;
    loadTorrentQueue();
  } else {
    content.innerHTML = `
      <div style="padding:40px;text-align:center">
        <div style="font-size:52px;margin-bottom:16px">❌</div>
        <h3 style="margin:0 0 8px;font-size:16px;color:var(--danger)">Erreur</h3>
        <p style="font-size:13px;color:var(--text-dim);margin:0 0 24px">${esc(res.message || 'Inconnu')}</p>
        <div style="display:flex;gap:10px;justify-content:center">
          <button onclick="_openFileBrowserModal(_fileBrowserItem)"
                  style="padding:10px 20px;border-radius:8px;border:1px solid var(--border);
                         background:none;color:var(--text);cursor:pointer;font-size:13px">Réessayer</button>
          <button onclick="document.getElementById('file-browser-modal').remove()"
                  style="padding:10px 20px;border-radius:8px;border:none;background:var(--danger);
                         color:#fff;cursor:pointer;font-size:13px">Fermer</button>
        </div>
      </div>`;
  }
}

function showTorrentHistory(hist) {
  const lines = [
    ['Fichier source',    hist.source_file],
    ['Chemin source',     hist.source_path],
    ['Fichier final',     hist.dest_filename],
    ['Chemin final',      hist.dest_path],
    ['Traité le',         hist.processed_at],
    ['Action',            hist.action === 'upgrade' ? '↑ Remplacement' : 'Ajout manquant'],
    ['Fichier remplacé',  hist.owned_replaced || null],
    ['Indexer',           hist.indexer || null],
  ].filter(([, v]) => v);
  const html = lines.map(([k, v]) =>
    `<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
      <span style="color:var(--text-dim);min-width:130px;font-size:12px">${esc(k)}</span>
      <span style="font-size:12px;font-family:monospace;word-break:break-all">${esc(v)}</span>
    </div>`).join('');
  const modal = document.createElement('div');
  modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:2000;display:flex;align-items:center;justify-content:center';
  modal.innerHTML = `<div style="background:var(--bg-card);border-radius:12px;padding:24px;width:560px;max-width:95vw;border:1px solid var(--border)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h3 style="margin:0;font-size:15px">ℹ Historique torrent</h3>
      <button onclick="this.closest('div[style*=fixed]').remove()"
              style="background:none;border:none;color:var(--text-dim);font-size:20px;cursor:pointer">✕</button>
    </div>${html}</div>`;
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  document.body.appendChild(modal);
}

function editTorrentQueueTomes(itemIdx) {
  const item = _torrentQueueItems[itemIdx];
  if (!item) return;
  const current = (item.tomes?.length ? item.tomes : []).join(', ');
  const input = prompt(
    `Release : ${item.filename}\n\nTomes (virgules ou plage ex: 1-30) :`,
    current || (item.tome_number || '').replace(/T/gi,'')
  );
  if (input === null) return;
  const tomes = _parseTomeInput(input);
  if (!tomes.length) return;

  api('/queue/item', 'PATCH', {
    key_field: 'torrent_link',
    key_value: item.torrent_link || item.filename,
    tomes,
  }).then(() => loadTorrentQueue());
}

function _parseTomeInput(input) {
  const tomes = [];
  for (const part of input.split(',')) {
    const t = part.trim();
    const range = t.match(/^(\d+)\s*[-àa]\s*(\d+)$/i);
    if (range) {
      const s = parseInt(range[1]), e = parseInt(range[2]);
      if (e >= s && (e - s) <= 500) for (let i = s; i <= e; i++) tomes.push(i);
    } else {
      const n = parseInt(t);
      if (!isNaN(n) && n > 0) tomes.push(n);
    }
  }
  return [...new Set(tomes)].sort((a,b) => a-b);
}

function updateTorrentQueueBadge() {
  const badge   = document.getElementById('torrent-queue-badge');
  if (!badge) return;
  const pending = _torrentQueueItems.filter(i => i.status !== 'done').length;
  badge.textContent   = pending;
  badge.style.display = pending > 0 ? '' : 'none';
}

async function clearTorrentQueue() {
  if (!confirm('Purger les torrents terminés ?')) return;
  await api('/queue/clear', 'POST', { mode: 'done', source: 'torrent' });
  loadTorrentQueue();
}

async function scanTorrentIncoming(btn) {
  const status = document.getElementById('torrent-scan-status');
  if (btn) btn.disabled = true;
  if (status) { status.textContent = 'Scan en cours…'; status.style.color = 'var(--text-dim)'; }
  const d = await api('/torrent/scan-incoming');
  if (btn) btn.disabled = false;
  if (status) {
    status.textContent = d.message || (d.ok ? 'Scan terminé' : 'Erreur');
    status.style.color = d.ok ? 'var(--success)' : 'var(--error)';
    setTimeout(() => { if (status) status.textContent = ''; }, 4000);
  }
  loadTorrentQueue();
}

// ── Surveillance ──
let _monitorData = [];

async function refreshMonitoring() {
  const status = document.getElementById('monitor-status');
  const list   = document.getElementById('monitor-list');
  if (status) { status.textContent = 'Analyse en cours…'; status.className = 'status-msg'; }
  if (list)   list.innerHTML = '<p style="color:var(--text-dim);font-size:12px">Recherche sur les indexers…</p>';

  const d = await api('/torrent/monitoring', 'GET', null, 120000);

  if (status) { status.textContent = d.ok ? '' : (d.message || 'Erreur'); status.className = 'status-msg' + (d.ok ? '' : ' error'); }

  _monitorData = d.series || [];
  renderMonitorList();
}

function filterMonitorList() {
  renderMonitorList();
}

function renderMonitorList() {
  const list = document.getElementById('monitor-list');
  if (!list) return;
  const q = ((document.getElementById('monitor-search') || {}).value || '').toLowerCase();
  let items = _monitorData.filter(s =>
    !q || (s.series_name || '').toLowerCase().includes(q));

  if (!items.length) {
    list.innerHTML = '<p style="color:var(--text-dim);font-size:12px">Aucune série avec des releases disponibles.</p>';
    return;
  }

  list.innerHTML = items.map(s => {
    const missing  = s.missing_tomes?.length ? s.missing_tomes.map(n=>`T${String(n).padStart(2,'0')}`).join(' ') : '—';
    const progress = s.total_vf > 0
      ? `${s.owned_count}/${s.total_vf} VF`
      : `${s.owned_count} possédé(s)`;
    return `<div class="monitor-series-card" onclick="openMonitorSeriesDetail(${JSON.stringify(s).replace(/"/g,'&quot;')})">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px">
        <div class="ms-name">${esc(s.series_name)}</div>
        <span style="font-size:11px;background:rgba(0,181,204,.2);color:var(--accent);padding:2px 8px;border-radius:10px;font-weight:600">
          ${s.releases_count} release(s)
        </span>
      </div>
      <div class="ms-meta">${esc(s.lib_name)} · ${progress}</div>
      <div class="ms-badges">
        ${s.missing_count > 0 ? `<span style="font-size:10px;color:var(--warning)">⚠ ${s.missing_count} manquant(s) : ${esc(missing)}</span>` : ''}
        ${s.has_integrale ? `<span style="font-size:10px;color:var(--warning);font-weight:600">★ Intégrale disponible</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

function openMonitorSeriesDetail(series) {
  // Redirige vers la page série avec le modal torrent ouvert
  window.location.href = `/series/${encodeURIComponent(series.series_slug)}#torrent`;
}
document.head.appendChild(_tabStyle);
