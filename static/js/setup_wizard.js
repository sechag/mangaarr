/* ═══════════════════════════════════════════════════════════
   MangaArr — Assistant de premier lancement (Setup Wizard)
   S'affiche au 1er démarrage Docker tant que la config n'est
   pas faite. S'auto-injecte dans le DOM ; réutilise les API
   existantes. Aucune dépendance sur app.js (helper local).
═══════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ── Helper API local (indépendant de app.js) ─────────────
  async function sapi(path, method = 'GET', body = null, timeoutMs = 30000) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), timeoutMs);
    opts.signal = ctrl.signal;
    try {
      const r = await fetch('/api' + path, opts);
      return await r.json();
    } catch (e) {
      return { ok: false, message: 'Erreur réseau ou délai dépassé' };
    } finally { clearTimeout(tid); }
  }

  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const LOGO = `<svg viewBox="0 0 24 24" fill="none" stroke="#00b5cc" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6c0-1.1.9-2 2-2h5a3 3 0 0 1 3 3 3 3 0 0 1 3-3h5a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6a2 2 0 0 0-2 2 2 2 0 0 0-2-2H4a2 2 0 0 1-2-2V6Z"/><path d="M12 7v13"/></svg>`;

  // ── État global du wizard ────────────────────────────────
  const W = {
    pane: 0,                       // index dans PANES
    state: {
      libraries: [],
      clients:   [],
      cookieOk:  false,
      ebdzLinked: false,
      metadata:  [],
      torznab:   [],
      telegram:  null,             // {id, authed}
      automationTouched: false,
    },
  };

  // PANES = welcome + étapes config + done
  // 'key' sert au rail (les étapes config uniquement y figurent)
  const CONFIG_STEPS = [
    { key: 'libraries',  num: 1, label: 'Librairies',      sub: 'Requis',    required: true },
    { key: 'clients',    num: 2, label: 'Téléchargement',  sub: 'qBittorrent · aMule', optional: true },
    { key: 'ebdz',       num: 3, label: 'ebdz.net',        sub: 'Source de scraping', optional: true },
    { key: 'automation', num: 4, label: 'Automatisation',  sub: 'Renommage · conversion' },
    { key: 'metadata',   num: 5, label: 'Métadonnées',     sub: 'Optionnel', optional: true },
    { key: 'torznab',    num: 6, label: 'Torznab',         sub: 'Optionnel', optional: true },
    { key: 'telegram',   num: 7, label: 'Telegram',        sub: 'Optionnel', optional: true },
  ];
  const PANES = ['welcome', ...CONFIG_STEPS.map(s => s.key), 'done'];

  // ══════════════════════════════════════════════════════
  // BOOT : vérifie si l'assistant doit s'afficher
  // ══════════════════════════════════════════════════════
  async function boot() {
    let needed = false;
    try {
      const st = await sapi('/setup/status');
      needed = !!st.needed;
    } catch (_) { needed = false; }
    if (needed) {
      buildShell();
      open();
    }
    // Expose un déclencheur manuel (reconfiguration depuis Settings)
    window.openSetupWizard = async () => {
      if (!document.getElementById('setup-overlay')) buildShell();
      await sapi('/setup/reset', 'POST');
      W.pane = 0; open();
    };
  }

  // ══════════════════════════════════════════════════════
  // SHELL (structure fixe : rail + corps + footer)
  // ══════════════════════════════════════════════════════
  function buildShell() {
    if (document.getElementById('setup-overlay')) return;
    const ov = document.createElement('div');
    ov.id = 'setup-overlay';
    ov.innerHTML = `
      <div class="setup-card" role="dialog" aria-modal="true" aria-label="Configuration de MangaArr">
        <aside class="setup-rail">
          <div class="setup-brand">
            ${LOGO}
            <div><div class="t">MangaArr</div><div class="s">Configuration initiale</div></div>
          </div>
          <div class="setup-steps" id="setup-rail-steps"></div>
          <div class="setup-rail-foot">
            <div class="bar"><i id="setup-progress" style="width:0%"></i></div>
            <span class="lbl-txt" id="setup-progress-lbl">Étape 0 / ${CONFIG_STEPS.length}</span>
          </div>
        </aside>
        <div class="setup-main">
          <div class="setup-body" id="setup-body"></div>
          <div class="setup-foot" id="setup-foot"></div>
        </div>
      </div>`;
    document.body.appendChild(ov);
    renderRail();
  }

  function open()  { const o = $('#setup-overlay'); if (o) { o.classList.add('show'); render(); } }
  function close() { const o = $('#setup-overlay'); if (o) o.classList.remove('show'); }

  // ── Rail latéral ──
  function renderRail() {
    const cur = currentConfigKey();
    const reached = configStepsReached();
    $('#setup-rail-steps').innerHTML = CONFIG_STEPS.map(s => {
      const isActive = s.key === cur;
      const isDone   = reached.indexOf(s.key) !== -1 && !isActive && isStepSatisfied(s.key);
      const cls = ['setup-step', isActive ? 'active' : '', isDone ? 'done' : '', 'clickable'].join(' ');
      const dot = isDone ? '✓' : s.num;
      return `<div class="${cls}" data-jump="${s.key}">
        <div class="dot">${dot}</div>
        <div><div class="lbl">${s.label}</div>${s.sub ? `<div class="opt">${s.sub}</div>` : ''}</div>
      </div>`;
    }).join('');
    $$('#setup-rail-steps .setup-step').forEach(el => {
      el.addEventListener('click', () => {
        const idx = PANES.indexOf(el.dataset.jump);
        if (idx >= 0) { W.pane = idx; render(); }
      });
    });
    // Progression
    const done = configStepsDoneCount();
    const pct = Math.round((done / CONFIG_STEPS.length) * 100);
    const pb = $('#setup-progress'); if (pb) pb.style.width = pct + '%';
    const lbl = $('#setup-progress-lbl'); if (lbl) lbl.textContent = `Étape ${Math.min(done + (currentConfigKey() ? 1 : 0), CONFIG_STEPS.length)} / ${CONFIG_STEPS.length}`;
  }

  function currentConfigKey() {
    const k = PANES[W.pane];
    return CONFIG_STEPS.some(s => s.key === k) ? k : null;
  }
  function configStepsReached() {
    // toutes les étapes config dont l'index <= pane courant
    return CONFIG_STEPS.filter(s => PANES.indexOf(s.key) <= W.pane).map(s => s.key);
  }
  function configStepsDoneCount() {
    return CONFIG_STEPS.filter(s => PANES.indexOf(s.key) < W.pane && isStepSatisfied(s.key)).length;
  }
  function isStepSatisfied(key) {
    switch (key) {
      case 'libraries':  return W.state.libraries.length > 0;
      case 'clients':    return W.state.clients.length > 0;
      case 'ebdz':       return W.state.cookieOk;
      case 'automation': return true;
      case 'metadata':   return W.state.metadata.length > 0;
      case 'torznab':    return W.state.torznab.length > 0;
      case 'telegram':   return !!(W.state.telegram && W.state.telegram.authed);
      default: return false;
    }
  }

  // ══════════════════════════════════════════════════════
  // RENDER : route vers la bonne vue
  // ══════════════════════════════════════════════════════
  async function render() {
    renderRail();
    const key = PANES[W.pane];
    const body = $('#setup-body');
    const foot = $('#setup-foot');
    body.scrollTop = 0;
    const r = RENDERERS[key];
    body.innerHTML = `<div class="setup-pane">${r.html()}</div>`;
    foot.innerHTML = footHtml(key);
    bindFoot(key);
    if (r.bind) await r.bind(body);
  }

  function footHtml(key) {
    if (key === 'welcome') {
      return `<button class="skip" data-act="quit">Configurer plus tard</button>
              <div class="spacer"></div>
              <button class="btn btn-primary" data-act="next">Commencer la configuration →</button>`;
    }
    if (key === 'done') {
      return `<button class="setup-ghost" data-act="prev">← Retour</button>
              <div class="spacer"></div>
              <button class="btn btn-primary" data-act="finish">Terminer et lancer MangaArr ✓</button>`;
    }
    const step = CONFIG_STEPS.find(s => s.key === key);
    const isOpt = step && (step.optional || !step.required);
    const skipBtn = (step && step.optional)
      ? `<button class="skip" data-act="next">Passer cette étape →</button>` : '';
    const nextLbl = 'Continuer →';
    return `<button class="setup-ghost" data-act="prev">← Précédent</button>
            <div class="spacer"></div>
            ${skipBtn}
            <button class="btn btn-primary" data-act="next" id="setup-next">${nextLbl}</button>`;
  }

  function bindFoot(key) {
    $$('#setup-foot [data-act]').forEach(b => {
      b.addEventListener('click', async () => {
        const act = b.dataset.act;
        if (act === 'quit')   return quitConfirm();
        if (act === 'prev')   return prev();
        if (act === 'finish') return finish(b);
        if (act === 'next')   return next(b);
      });
    });
  }

  async function next(btn) {
    const key = PANES[W.pane];
    // Validation bloquante : librairies obligatoires
    if (key === 'libraries' && W.state.libraries.length === 0) {
      flashStatus('lib-status', 'error', 'Ajoutez au moins une librairie pour continuer.');
      return;
    }
    // Sauvegarde automatique de l'automatisation en quittant l'étape
    if (key === 'automation') {
      if (btn) setLoading(btn, true);
      await saveAutomation();
      if (btn) setLoading(btn, false);
    }
    if (W.pane < PANES.length - 1) { W.pane++; render(); }
  }
  function prev() { if (W.pane > 0) { W.pane--; render(); } }

  function quitConfirm() {
    if (confirm("Quitter l'assistant ? Vous pourrez le relancer depuis Settings → Stockage. La configuration déjà saisie est conservée.")) {
      close();
    }
  }

  async function finish(btn) {
    if (btn) setLoading(btn, true);
    if (W.state.automationTouched) await saveAutomation();
    await sapi('/setup/complete', 'POST');
    close();
    // Recharge l'app pour prendre en compte toute la nouvelle config
    setTimeout(() => location.reload(), 250);
  }

  // ── Utilitaires UI ──
  function setLoading(btn, on) {
    if (!btn) return;
    if (on) { btn.dataset._t = btn.innerHTML; btn.disabled = true; btn.innerHTML = '…'; }
    else { btn.disabled = false; if (btn.dataset._t) btn.innerHTML = btn.dataset._t; }
  }
  function flashStatus(id, cls, msg, spin) {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'setup-status ' + cls;
    el.innerHTML = (spin ? '<span class="spin"></span>' : '') + esc(msg);
  }
  function libCheckHtml(selectedIds) {
    selectedIds = selectedIds || [];
    if (!W.state.libraries.length)
      return `<p class="setup-mini">Aucune librairie créée à l'étape 1.</p>`;
    return `<div class="setup-checks">` + W.state.libraries.map(l => `
      <label class="setup-check ${selectedIds.indexOf(l.id) !== -1 ? 'sel' : ''}" data-libcheck>
        <input type="checkbox" value="${esc(l.id)}" ${selectedIds.indexOf(l.id) !== -1 ? 'checked' : ''}>
        <span class="nm">${esc(l.name)}</span><span class="pth">${esc(l.path)}</span>
      </label>`).join('') + `</div>`;
  }
  function bindLibChecks(root) {
    $$('[data-libcheck]', root).forEach(lbl => {
      const cb = $('input', lbl);
      cb.addEventListener('change', () => lbl.classList.toggle('sel', cb.checked));
    });
  }
  function selectedLibIds(root) {
    return $$('[data-libcheck] input:checked', root).map(i => i.value);
  }

  // ══════════════════════════════════════════════════════
  // RENDERERS — un par vue
  // ══════════════════════════════════════════════════════
  const RENDERERS = {

    // ── BIENVENUE ──
    welcome: {
      html: () => `
        <div class="setup-hero">
          <div class="logo">${LOGO}</div>
          <h1>Bienvenue sur MangaArr 👋</h1>
          <p>Avant de commencer, configurons ensemble l'essentiel. Cet assistant vous guide en quelques étapes&nbsp;: librairies, téléchargement, sources et automatisation. Tout reste modifiable plus tard dans les Réglages.</p>
          <div class="feats">
            <div class="setup-feat"><span class="fi">📚</span><div><b>Vos librairies</b><span>Les dossiers où sont rangés vos mangas</span></div></div>
            <div class="setup-feat"><span class="fi">⬇️</span><div><b>Téléchargement</b><span>qBittorrent &amp; aMule</span></div></div>
            <div class="setup-feat"><span class="fi">🔎</span><div><b>Sources</b><span>ebdz.net, Torznab, Telegram</span></div></div>
            <div class="setup-feat"><span class="fi">⚙️</span><div><b>Automatisation</b><span>Renommage &amp; conversion auto</span></div></div>
          </div>
        </div>`,
    },

    // ── ÉTAPE 1 : LIBRAIRIES ──
    libraries: {
      html: () => `
        <div class="setup-eyebrow">Étape 1 · Requis</div>
        <h2 class="setup-h">Vos librairies</h2>
        <p class="setup-p">Une librairie est un <strong>dossier racine</strong> contenant un sous-dossier par série. MangaArr y range, renomme et organise vos fichiers. Ajoutez-en au moins une.</p>
        <div class="setup-note">
          <span class="ni">💡</span>
          <div class="nc"><b>Chemins Docker&nbsp;:</b> utilisez le chemin <i>interne au conteneur</i> tel que monté dans votre <code>docker-compose.yml</code> (ex. <span class="setup-kbd">/media/Mangas</span>), pas le chemin de votre machine hôte.</div>
        </div>
        <div class="setup-added" id="lib-list"></div>
        <div class="setup-block">
          <h4>Ajouter une librairie</h4>
          <div class="setup-row2">
            <div class="setup-field"><label>Nom</label><input type="text" class="text-input" id="lib-name" placeholder="ex : Mangas"></div>
            <div class="setup-field"><label>Chemin du dossier</label><input type="text" class="text-input" id="lib-path" placeholder="/media/Mangas"></div>
          </div>
          <button class="btn btn-primary" id="lib-add">+ Ajouter la librairie</button>
          <div class="setup-status" id="lib-status"></div>
        </div>`,
      bind: async (root) => {
        await reloadLibraries();
        renderLibList();
        const addBtn = $('#lib-add', root);
        const doAdd = async () => {
          const name = $('#lib-name', root).value.trim();
          const path = $('#lib-path', root).value.trim();
          if (!name || !path) { flashStatus('lib-status', 'error', 'Nom et chemin requis.'); return; }
          setLoading(addBtn, true);
          flashStatus('lib-status', 'load', 'Vérification du dossier…', true);
          const r = await sapi('/libraries', 'POST', { name, path });
          setLoading(addBtn, false);
          if (r.ok) {
            $('#lib-name', root).value = ''; $('#lib-path', root).value = '';
            flashStatus('lib-status', 'ok', 'Librairie ajoutée ✓');
            await reloadLibraries(); renderLibList(); renderRail();
          } else {
            flashStatus('lib-status', 'error', r.message || "Échec — vérifiez le chemin.");
          }
        };
        addBtn.addEventListener('click', doAdd);
        $('#lib-path', root).addEventListener('keydown', e => { if (e.key === 'Enter') doAdd(); });
      },
    },

    // ── ÉTAPE 2 : CLIENTS DE TÉLÉCHARGEMENT ──
    clients: {
      html: () => `
        <div class="setup-eyebrow muted">Étape 2 · Optionnel</div>
        <h2 class="setup-h">Clients de téléchargement</h2>
        <p class="setup-p">Connectez <strong>qBittorrent</strong> (torrents) et/ou <strong>aMule</strong> (ed2k). MangaArr y enverra les téléchargements et surveillera les fichiers terminés.</p>
        <div class="setup-added" id="dc-list"></div>
        <div class="setup-block">
          <h4>Ajouter un client</h4>
          <div class="setup-field"><label>Type</label>
            <select class="text-input" id="dc-type">
              <option value="qbittorrent">qBittorrent</option>
              <option value="amule">aMule</option>
            </select>
          </div>
          <div class="setup-row2">
            <div class="setup-field"><label>Nom</label><input type="text" class="text-input" id="dc-name" placeholder="ex : qBittorrent"></div>
            <div class="setup-field"><label>Host / IP</label><input type="text" class="text-input" id="dc-host" placeholder="localhost"></div>
          </div>
          <div id="dc-qbt">
            <div class="setup-row2">
              <div class="setup-field"><label>Port</label><input type="number" class="text-input" id="dc-port" value="8080"></div>
              <div class="setup-field"><label>Catégorie</label><input type="text" class="text-input" id="dc-category" placeholder="Mangaarr"></div>
            </div>
            <div class="setup-row2">
              <div class="setup-field"><label>Utilisateur</label><input type="text" class="text-input" id="dc-user" placeholder="admin"></div>
              <div class="setup-field"><label>Mot de passe</label><input type="password" class="text-input" id="dc-pass"></div>
            </div>
          </div>
          <div id="dc-amule" style="display:none">
            <div class="setup-row2">
              <div class="setup-field"><label>EC Port</label><input type="number" class="text-input" id="dc-ecport" value="4712"></div>
              <div class="setup-field"><label>Mot de passe EC</label><input type="password" class="text-input" id="dc-ecpass"></div>
            </div>
            <p class="setup-mini">Le port EC (External Connections) et son mot de passe se définissent dans les préférences d'aMule.</p>
          </div>
          <button class="btn btn-primary" id="dc-add">Tester &amp; ajouter</button>
          <div class="setup-status" id="dc-status"></div>
        </div>`,
      bind: async (root) => {
        await reloadClients();
        renderDcList();
        const typeSel = $('#dc-type', root);
        const sync = () => {
          const amule = typeSel.value === 'amule';
          $('#dc-qbt', root).style.display   = amule ? 'none' : '';
          $('#dc-amule', root).style.display = amule ? '' : 'none';
        };
        typeSel.addEventListener('change', sync); sync();

        $('#dc-add', root).addEventListener('click', async (e) => {
          const type = typeSel.value;
          const name = $('#dc-name', root).value.trim();
          const host = $('#dc-host', root).value.trim();
          if (!name || !host) { flashStatus('dc-status', 'error', 'Nom et host requis.'); return; }
          const payload = (type === 'amule')
            ? { type, name, host, ec_port: +$('#dc-ecport', root).value || 4712, password: $('#dc-ecpass', root).value }
            : { type, name, host, port: +$('#dc-port', root).value || 8080,
                username: $('#dc-user', root).value.trim(), password: $('#dc-pass', root).value,
                category: $('#dc-category', root).value.trim() };
          setLoading(e.target, true);
          flashStatus('dc-status', 'load', 'Ajout…', true);
          const r = await sapi('/settings/download-clients', 'POST', payload);
          if (!r.ok) { setLoading(e.target, false); flashStatus('dc-status', 'error', r.message || 'Échec.'); return; }
          // Test de connexion
          flashStatus('dc-status', 'load', 'Test de connexion…', true);
          const t = await sapi(`/settings/download-clients/${r.client.id}/test`, 'POST');
          setLoading(e.target, false);
          await reloadClients(); renderDcList(); renderRail();
          if (t.ok) flashStatus('dc-status', 'ok', 'Connecté ✓ ' + (t.message || ''));
          else flashStatus('dc-status', 'error', 'Ajouté mais connexion KO : ' + (t.message || ''));
          ['dc-name','dc-host','dc-user','dc-pass','dc-ecpass'].forEach(id => { const el = $('#' + id, root); if (el) el.value = ''; });
        });
      },
    },

    // ── ÉTAPE 3 : EBDZ.NET ──
    ebdz: {
      html: () => `
        <div class="setup-eyebrow muted">Étape 3 · Optionnel</div>
        <h2 class="setup-h">Source ebdz.net</h2>
        <p class="setup-p">ebdz.net nécessite votre <strong>cookie de session</strong> pour accéder aux liens. Il reste stocké uniquement en local.</p>
        <div class="setup-note warn">
          <span class="ni">🍪</span>
          <div class="nc">
            <b>Comment récupérer votre cookie <span class="setup-kbd">mybbuser</span> :</b>
            <ol class="setup-howto">
              <li>Connectez-vous à <code>ebdz.net</code> dans votre navigateur (Chrome/Firefox).</li>
              <li>Ouvrez les outils développeur avec <span class="setup-kbd">F12</span>.</li>
              <li>Onglet <b>Application</b> (Chrome) ou <b>Stockage</b> (Firefox) → <b>Cookies</b> → <code>https://ebdz.net</code>.</li>
              <li>Trouvez la ligne nommée <span class="setup-kbd">mybbuser</span> et copiez sa <b>valeur</b> (longue chaîne du type <code>123-ab12cd…</code>).</li>
              <li>Collez-la ci-dessous puis cliquez sur <b>Vérifier</b>.</li>
            </ol>
          </div>
        </div>
        <div class="setup-block">
          <h4>Cookie de session</h4>
          <div class="setup-inline">
            <div class="setup-field"><label>Valeur du cookie mybbuser</label><input type="text" class="text-input" id="ebdz-cookie" placeholder="Collez la valeur ici"></div>
            <button class="btn btn-primary" id="ebdz-check">Vérifier</button>
          </div>
          <div class="setup-status" id="ebdz-status"></div>
        </div>
        <div class="setup-block" id="ebdz-source-block" style="display:none">
          <h4>Source de scraping &amp; librairies liées</h4>
          <div class="desc">Choisissez la catégorie ebdz à surveiller et reliez-la à la/les librairie(s) où ranger les téléchargements.</div>
          <div class="setup-row2">
            <div class="setup-field"><label>Nom de la source</label><input type="text" class="text-input" id="ebdz-src-name" value="Mangas"></div>
            <div class="setup-field"><label>Fréquence d'actualisation</label>
              <select class="text-input" id="ebdz-interval">
                <option value="6">Toutes les 6 h</option>
                <option value="12">Toutes les 12 h</option>
                <option value="24" selected>Une fois par jour</option>
                <option value="48">Tous les 2 jours</option>
                <option value="168">Une fois par semaine</option>
              </select>
            </div>
          </div>
          <div class="setup-field"><label>URL de la catégorie ebdz</label><input type="text" class="text-input" id="ebdz-src-url" value="https://ebdz.net/forum/forumdisplay.php?fid=29"></div>
          <div class="setup-field"><label>Librairies à lier</label><div id="ebdz-libs"></div></div>
          <button class="btn btn-primary" id="ebdz-link">Enregistrer la source</button>
          <div class="setup-status" id="ebdz-link-status"></div>
        </div>`,
      bind: async (root) => {
        const srcBlock = $('#ebdz-source-block', root);
        if (W.state.cookieOk) {
          srcBlock.style.display = '';
          flashStatus('ebdz-status', 'ok', 'Cookie validé ✓');
        }
        const renderLibs = () => { $('#ebdz-libs', root).innerHTML = libCheckHtml(W.state.ebdzLibIds || []); bindLibChecks($('#ebdz-libs', root)); };
        renderLibs();

        $('#ebdz-check', root).addEventListener('click', async (e) => {
          const cookie = $('#ebdz-cookie', root).value.trim();
          if (!cookie) { flashStatus('ebdz-status', 'error', 'Collez la valeur du cookie.'); return; }
          setLoading(e.target, true);
          flashStatus('ebdz-status', 'load', 'Connexion à ebdz.net…', true);
          const r = await sapi('/indexers/test', 'POST', { mybbuser: cookie });
          setLoading(e.target, false);
          if (r.ok) {
            W.state.cookieOk = true;
            flashStatus('ebdz-status', 'ok', r.message || 'Connecté ✓');
            srcBlock.style.display = '';
            renderLibs(); renderRail();
          } else {
            W.state.cookieOk = false;
            flashStatus('ebdz-status', 'error', r.message || 'Cookie invalide.');
          }
        });

        $('#ebdz-link', root).addEventListener('click', async (e) => {
          const name = $('#ebdz-src-name', root).value.trim() || 'Mangas';
          const url  = $('#ebdz-src-url', root).value.trim();
          const libIds = selectedLibIds($('#ebdz-libs', root));
          W.state.ebdzLibIds = libIds;
          if (!url) { flashStatus('ebdz-link-status', 'error', 'URL requise.'); return; }
          setLoading(e.target, true);
          flashStatus('ebdz-link-status', 'load', 'Enregistrement…', true);
          // La source par défaut "manga" existe toujours (DEFAULTS) → on la met à jour
          const patch = await sapi('/ebdz/sources/manga', 'PATCH', { name, url, library_ids: libIds });
          // Fréquence de scrape
          await sapi('/settings/scrape-interval', 'POST', { interval_hours: +$('#ebdz-interval', root).value || 24 });
          setLoading(e.target, false);
          if (patch.ok) { W.state.ebdzLinked = true; flashStatus('ebdz-link-status', 'ok', 'Source enregistrée ✓'); }
          else {
            // Source absente → on la crée
            const add = await sapi('/ebdz/sources', 'POST', { name, url, library_ids: libIds });
            if (add.ok) { W.state.ebdzLinked = true; flashStatus('ebdz-link-status', 'ok', 'Source créée ✓'); }
            else flashStatus('ebdz-link-status', 'error', (patch.message || add.message) || 'Échec.');
          }
        });
      },
    },

    // ── ÉTAPE 4 : AUTOMATISATION ──
    automation: {
      html: () => `
        <div class="setup-eyebrow">Étape 4</div>
        <h2 class="setup-h">Automatisation &amp; renommage</h2>
        <p class="setup-p">Définissez comment MangaArr traite automatiquement les fichiers téléchargés.</p>
        <div class="setup-block" style="padding:6px 18px">
          <div class="setup-toggle"><div><strong>Renommage automatique</strong><p>Renomme les fichiers selon le format choisi ci-dessous.</p></div>
            <label class="toggle"><input type="checkbox" id="au-rename"><span class="toggle-slider"></span></label></div>
          <div class="setup-toggle"><div><strong>Conversion CBR → CBZ</strong><p>Convertit les archives RAR en ZIP.</p></div>
            <label class="toggle"><input type="checkbox" id="au-cbr"><span class="toggle-slider"></span></label></div>
          <div class="setup-toggle"><div><strong>Conversion PDF → CBZ</strong><p>Convertit les PDF en archives d'images.</p></div>
            <label class="toggle"><input type="checkbox" id="au-pdf"><span class="toggle-slider"></span></label></div>
          <div class="setup-toggle"><div><strong>Remplacement automatique</strong><p>Remplace un tome si un meilleur tag (qualité) est disponible.</p></div>
            <label class="toggle"><input type="checkbox" id="au-replace"><span class="toggle-slider"></span></label></div>
          <div class="setup-toggle"><div><strong>Forcer l'organisation manuelle (Torrent)</strong><p>Ajoute un bouton ▶ dans la queue Torrent pour forcer la copie/renommage en sélectionnant le fichier. Désactivé par défaut.</p></div>
            <label class="toggle"><input type="checkbox" id="au-force"><span class="toggle-slider"></span></label></div>
        </div>
        <div class="setup-block">
          <h4>Format de renommage</h4>
          <div class="desc">Modèle de nom appliqué lors de la copie dans la bibliothèque.</div>
          <label class="setup-radio" data-fmt="1"><input type="radio" name="au-fmt" value="1">
            <div class="info"><b>Format 1 — défaut</b><span class="ex">One Piece Tome 02 (Paprika+).cbz</span></div></label>
          <label class="setup-radio" data-fmt="2"><input type="radio" name="au-fmt" value="2">
            <div class="info"><b>Format 2 — compact</b><span class="ex">Tome 02 (Paprika+).cbz</span></div></label>
          <label class="setup-radio" data-fmt="3"><input type="radio" name="au-fmt" value="3">
            <div class="info"><b>Format 3 — style scene</b><span class="ex">One.Piece.T02.FRENCH.CBZ.eBook-Paprika+.cbz</span></div></label>
        </div>`,
      bind: async (root) => {
        const cfg = await sapi('/config');
        const mm = (cfg && cfg.media_management) || {};
        $('#au-rename', root).checked  = mm.auto_rename           ?? true;
        $('#au-cbr', root).checked     = mm.auto_convert_cbr       ?? true;
        $('#au-pdf', root).checked     = mm.auto_convert_pdf       ?? true;
        $('#au-replace', root).checked = mm.auto_replace           ?? true;
        $('#au-force', root).checked   = mm.force_organize_enabled ?? false;
        const fmt = String(mm.rename_format ?? 1);
        $$('input[name="au-fmt"]', root).forEach(i => { i.checked = (i.value === fmt); });
        const syncRadios = () => $$('.setup-radio', root).forEach(r => r.classList.toggle('sel', $('input', r).checked));
        syncRadios();
        $$('.setup-radio', root).forEach(r => r.addEventListener('change', () => { syncRadios(); W.state.automationTouched = true; }));
        $$('#au-rename,#au-cbr,#au-pdf,#au-replace,#au-force', root).forEach(t => t.addEventListener('change', () => { W.state.automationTouched = true; }));
      },
    },

    // ── ÉTAPE 5 : MÉTADONNÉES ──
    metadata: {
      html: () => `
        <div class="setup-eyebrow muted">Étape 5 · Optionnel</div>
        <h2 class="setup-h">Métadonnées</h2>
        <p class="setup-p">Reliez une source <strong>MangaDB</strong> pour enrichir vos séries (auteurs, genres, statut, nombre de tomes VF) et définissez la fréquence de synchronisation.</p>
        <div class="setup-added" id="meta-list"></div>
        <div class="setup-block">
          <h4>Ajouter une source MangaDB</h4>
          <div class="setup-row2">
            <div class="setup-field"><label>Nom</label><input type="text" class="text-input" id="meta-name" value="MangaDB"></div>
            <div class="setup-field"><label>Fréquence d'actualisation</label>
              <select class="text-input" id="meta-freq">
                <option value="0">Manuel uniquement</option>
                <option value="12">Toutes les 12 h</option>
                <option value="24" selected>Une fois par jour</option>
                <option value="72">Tous les 3 jours</option>
                <option value="168">Une fois par semaine</option>
              </select>
            </div>
          </div>
          <div class="setup-field"><label>URL</label><input type="text" class="text-input" id="meta-url" placeholder="https://mangadb.exemple.ovh"></div>
          <div class="setup-field"><label>Librairies associées</label><div id="meta-libs"></div></div>
          <button class="btn btn-primary" id="meta-add">Tester &amp; ajouter</button>
          <div class="setup-status" id="meta-status"></div>
        </div>`,
      bind: async (root) => {
        await reloadMetadata(); renderMetaList();
        $('#meta-libs', root).innerHTML = libCheckHtml(); bindLibChecks($('#meta-libs', root));
        $('#meta-add', root).addEventListener('click', async (e) => {
          const name = $('#meta-name', root).value.trim() || 'MangaDB';
          const url  = $('#meta-url', root).value.trim();
          if (!url) { flashStatus('meta-status', 'error', 'URL requise.'); return; }
          setLoading(e.target, true);
          flashStatus('meta-status', 'load', 'Test de connexion…', true);
          const r = await sapi('/metadata/sources', 'POST', { name, url, library_ids: selectedLibIds($('#meta-libs', root)) });
          await sapi('/metadata/sync-interval', 'POST', { interval_hours: +$('#meta-freq', root).value || 0 });
          setLoading(e.target, false);
          if (r.ok) {
            flashStatus('meta-status', 'ok', r.message || 'Source ajoutée ✓');
            $('#meta-url', root).value = '';
            await reloadMetadata(); renderMetaList(); renderRail();
          } else flashStatus('meta-status', 'error', r.message || 'Échec.');
        });
      },
    },

    // ── ÉTAPE 6 : TORZNAB ──
    torznab: {
      html: () => `
        <div class="setup-eyebrow muted">Étape 6 · Optionnel</div>
        <h2 class="setup-h">Indexers Torznab</h2>
        <p class="setup-p">Ajoutez vos indexers compatibles <strong>Torznab</strong> (Jackett, Prowlarr…) pour rechercher des torrents.</p>
        <div class="setup-added" id="tz-list"></div>
        <div class="setup-block">
          <h4>Ajouter un indexer</h4>
          <div class="setup-field"><label>Nom</label><input type="text" class="text-input" id="tz-name" placeholder="ex : Mon Jackett"></div>
          <div class="setup-field"><label>URL Torznab</label><input type="text" class="text-input" id="tz-url" placeholder="http://jackett:9117/api/v2.0/indexers/.../results/torznab"></div>
          <div class="setup-field"><label>Clé API</label><input type="text" class="text-input" id="tz-key" placeholder="apikey"></div>
          <button class="btn btn-primary" id="tz-add">Ajouter &amp; tester</button>
          <div class="setup-status" id="tz-status"></div>
        </div>`,
      bind: async (root) => {
        await reloadTorznab(); renderTzList();
        $('#tz-add', root).addEventListener('click', async (e) => {
          const name = $('#tz-name', root).value.trim();
          const url  = $('#tz-url', root).value.trim();
          const apikey = $('#tz-key', root).value.trim();
          if (!name || !url) { flashStatus('tz-status', 'error', 'Nom et URL requis.'); return; }
          setLoading(e.target, true);
          flashStatus('tz-status', 'load', 'Ajout…', true);
          const r = await sapi('/indexers/torznab', 'POST', { name, url, apikey });
          if (!r.ok) { setLoading(e.target, false); flashStatus('tz-status', 'error', r.message || 'Échec.'); return; }
          flashStatus('tz-status', 'load', 'Test…', true);
          const t = await sapi(`/indexers/torznab/${r.indexer.id}/test`, 'POST');
          setLoading(e.target, false);
          await reloadTorznab(); renderTzList(); renderRail();
          flashStatus('tz-status', t.ok ? 'ok' : 'error', (t.ok ? 'Indexer OK ✓ ' : 'Ajouté mais test KO : ') + (t.message || ''));
          ['tz-name','tz-url','tz-key'].forEach(id => $('#' + id, root).value = '');
        });
      },
    },

    // ── ÉTAPE 7 : TELEGRAM ──
    telegram: {
      html: () => `
        <div class="setup-eyebrow muted">Étape 7 · Optionnel</div>
        <h2 class="setup-h">Telegram</h2>
        <p class="setup-p">Connectez un compte Telegram pour récupérer des fichiers depuis des canaux. Récupérez <strong>api_id</strong> et <strong>api_hash</strong> sur <span class="setup-kbd">my.telegram.org</span>.</p>
        <div class="setup-note"><span class="ni">🔑</span><div class="nc">Sur <code>my.telegram.org</code> → <b>API development tools</b> → créez une application pour obtenir votre <b>api_id</b> et <b>api_hash</b>.</div></div>
        <div class="setup-block" id="tg-step1">
          <h4>Identifiants</h4>
          <div class="setup-row2">
            <div class="setup-field"><label>Nom</label><input type="text" class="text-input" id="tg-name" value="Telegram"></div>
            <div class="setup-field"><label>Téléphone</label><input type="text" class="text-input" id="tg-phone" placeholder="+33612345678"></div>
          </div>
          <div class="setup-row2">
            <div class="setup-field"><label>api_id</label><input type="text" class="text-input" id="tg-apiid" placeholder="12345678"></div>
            <div class="setup-field"><label>api_hash</label><input type="text" class="text-input" id="tg-apihash" placeholder="abc123…"></div>
          </div>
          <button class="btn btn-primary" id="tg-send">Envoyer le code</button>
          <div class="setup-status" id="tg-status"></div>
        </div>
        <div class="setup-block" id="tg-step2" style="display:none">
          <h4>Validation</h4>
          <div class="desc">Saisissez le code reçu sur Telegram (et votre mot de passe 2FA si activé).</div>
          <div class="setup-row2">
            <div class="setup-field"><label>Code reçu</label><input type="text" class="text-input" id="tg-code" placeholder="12345"></div>
            <div class="setup-field"><label>Mot de passe 2FA (si activé)</label><input type="password" class="text-input" id="tg-2fa" placeholder="optionnel"></div>
          </div>
          <button class="btn btn-primary" id="tg-signin">Valider la connexion</button>
          <div class="setup-status" id="tg-status2"></div>
        </div>`,
      bind: async (root) => {
        if (W.state.telegram && W.state.telegram.authed) {
          flashStatus('tg-status', 'ok', 'Compte Telegram déjà connecté ✓');
        }
        $('#tg-send', root).addEventListener('click', async (e) => {
          const api_id = $('#tg-apiid', root).value.trim();
          const api_hash = $('#tg-apihash', root).value.trim();
          const phone = $('#tg-phone', root).value.trim();
          const name = $('#tg-name', root).value.trim() || 'Telegram';
          if (!api_id || !api_hash || !phone) { flashStatus('tg-status', 'error', 'api_id, api_hash et téléphone requis.'); return; }
          setLoading(e.target, true);
          flashStatus('tg-status', 'load', 'Création de l\'indexer…', true);
          let id = W.state.telegram && W.state.telegram.id;
          if (!id) {
            const r = await sapi('/indexers/telegram', 'POST', { api_id, api_hash, phone, name });
            if (!r.ok) { setLoading(e.target, false); flashStatus('tg-status', 'error', r.message || 'Échec.'); return; }
            id = r.indexer.id; W.state.telegram = { id, authed: false };
          }
          flashStatus('tg-status', 'load', 'Envoi du code…', true);
          const sc = await sapi(`/indexers/telegram/${id}/send-code`, 'POST');
          setLoading(e.target, false);
          if (sc.ok) {
            flashStatus('tg-status', 'ok', 'Code envoyé sur Telegram ✓');
            $('#tg-step2', root).style.display = '';
          } else flashStatus('tg-status', 'error', sc.message || 'Échec d\'envoi du code.');
        });
        $('#tg-signin', root).addEventListener('click', async (e) => {
          const id = W.state.telegram && W.state.telegram.id;
          if (!id) { flashStatus('tg-status2', 'error', 'Envoyez d\'abord le code.'); return; }
          const code = $('#tg-code', root).value.trim();
          const password = $('#tg-2fa', root).value.trim();
          if (!code) { flashStatus('tg-status2', 'error', 'Code requis.'); return; }
          setLoading(e.target, true);
          flashStatus('tg-status2', 'load', 'Connexion…', true);
          const r = await sapi(`/indexers/telegram/${id}/sign-in`, 'POST', { code, password });
          setLoading(e.target, false);
          if (r.ok) { W.state.telegram.authed = true; flashStatus('tg-status2', 'ok', r.message || 'Connecté ✓'); renderRail(); }
          else flashStatus('tg-status2', 'error', r.message || 'Code invalide.');
        });
      },
    },

    // ── FIN ──
    done: {
      html: () => {
        const row = (lbl, ok, val) => `<div class="r"><span>${lbl}</span><span class="v">${ok ? `<span class="y">✓ ${esc(val)}</span>` : 'Non configuré'}</span></div>`;
        const s = W.state;
        return `
        <div class="setup-done">
          <div class="check"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></div>
          <h1>Tout est prêt&nbsp;! 🎉</h1>
          <p>MangaArr est configuré. Vous pouvez tout ajuster à tout moment depuis les Réglages. Cliquez sur Terminer pour lancer l'application.</p>
          <div class="setup-recap">
            ${row('Librairies', s.libraries.length > 0, s.libraries.length + ' librairie(s)')}
            ${row('Clients de téléchargement', s.clients.length > 0, s.clients.length + ' client(s)')}
            ${row('Source ebdz.net', s.cookieOk, s.ebdzLinked ? 'liée' : 'cookie OK')}
            ${row('Automatisation', true, 'configurée')}
            ${row('Métadonnées', s.metadata.length > 0, s.metadata.length + ' source(s)')}
            ${row('Torznab', s.torznab.length > 0, s.torznab.length + ' indexer(s)')}
            ${row('Telegram', !!(s.telegram && s.telegram.authed), 'connecté')}
          </div>
        </div>`;
      },
    },
  };

  // ══════════════════════════════════════════════════════
  // ACTIONS DE DONNÉES (reload + render des listes)
  // ══════════════════════════════════════════════════════
  async function reloadLibraries() {
    const r = await sapi('/libraries');
    W.state.libraries = (r && r.libraries) || [];
  }
  function renderLibList() {
    const el = $('#lib-list'); if (!el) return;
    el.innerHTML = W.state.libraries.map(l => itemHtml('📁', l.name, l.path, 'lib', l.id)).join('');
    bindRemovers(el, async id => { await sapi('/libraries/' + id, 'DELETE'); await reloadLibraries(); renderLibList(); renderRail(); });
  }

  async function reloadClients() {
    const r = await sapi('/settings/download-clients');
    W.state.clients = (r && r.clients) || [];
  }
  function renderDcList() {
    const el = $('#dc-list'); if (!el) return;
    el.innerHTML = W.state.clients.map(c => {
      const sub = c.type === 'amule' ? `aMule · ${c.host}:${c.ec_port || ''}` : `qBittorrent · ${c.host}:${c.port || ''}`;
      return itemHtml(c.type === 'amule' ? '🐶' : '🧲', c.name, sub, 'dc', c.id);
    }).join('');
    bindRemovers(el, async id => { await sapi('/settings/download-clients/' + id, 'DELETE'); await reloadClients(); renderDcList(); renderRail(); });
  }

  async function reloadMetadata() {
    const r = await sapi('/metadata/sources');
    W.state.metadata = (r && (r.sources || r.metadata_sources)) || (Array.isArray(r) ? r : []);
    if (!Array.isArray(W.state.metadata)) W.state.metadata = [];
  }
  function renderMetaList() {
    const el = $('#meta-list'); if (!el) return;
    el.innerHTML = W.state.metadata.map(m => itemHtml('🗂️', m.name || 'MangaDB', m.url, 'meta', m.id)).join('');
    bindRemovers(el, async id => { await sapi('/metadata/sources/' + id, 'DELETE'); await reloadMetadata(); renderMetaList(); renderRail(); });
  }

  async function reloadTorznab() {
    const r = await sapi('/indexers/torznab');
    W.state.torznab = (r && r.indexers) || [];
  }
  function renderTzList() {
    const el = $('#tz-list'); if (!el) return;
    el.innerHTML = W.state.torznab.map(t => itemHtml('🔗', t.name, t.url, 'tz', t.id)).join('');
    bindRemovers(el, async id => { await sapi('/indexers/torznab/' + id, 'DELETE'); await reloadTorznab(); renderTzList(); renderRail(); });
  }

  function itemHtml(icon, title, sub, kind, id) {
    return `<div class="setup-item ok-badge">
      <div class="ic">${icon}</div>
      <div class="meta"><b>${esc(title)}</b><span>${esc(sub || '')}</span></div>
      <button class="rm" data-rm="${esc(id)}" title="Supprimer">✕</button>
    </div>`;
  }
  function bindRemovers(root, fn) {
    $$('[data-rm]', root).forEach(b => b.addEventListener('click', () => fn(b.dataset.rm)));
  }

  // Sauvegarde automatisation (média)
  async function saveAutomation() {
    const root = $('#setup-body');
    const get = id => $('#' + id, root);
    if (!get('au-rename')) return;  // pas sur la page automatisation
    const fmtEl = $('input[name="au-fmt"]:checked', root);
    // Fusion avec la config existante pour préserver les clés hors-scope (ex. emulecollection_as_txt)
    let mm = {};
    try { const cfg = await sapi('/config'); mm = (cfg && cfg.media_management) || {}; } catch (_) {}
    Object.assign(mm, {
      auto_rename:            get('au-rename').checked,
      auto_convert_cbr:       get('au-cbr').checked,
      auto_convert_pdf:       get('au-pdf').checked,
      auto_replace:           get('au-replace').checked,
      force_organize_enabled: get('au-force').checked,
      rename_format:          fmtEl ? parseInt(fmtEl.value) : 1,
    });
    await sapi('/config', 'POST', { media_management: mm });
    W.state.automationTouched = false;
  }

  // ══════════════════════════════════════════════════════
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
