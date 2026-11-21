// Storage path configuration, live preflight inspection, 12-month coverage matrix, and folder navigator.
(() => {
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const apiUrl = path => {
    const u = new URL('api/' + path, location.href);
    return u.pathname + u.search;
  };

  async function api(path, options) {
    const response = await fetch(apiUrl(path), {cache: 'no-store', ...options});
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = {error: text || response.statusText}; }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  const fmtBytes = b => {
    if (!b) return '0 B';
    if (b >= 1e9) return (b / 1e9).toFixed(2) + ' GB';
    if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
    if (b >= 1e3) return Math.round(b / 1e3) + ' KB';
    return b + ' B';
  };

  const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  const ICONS = {
    default: '<svg viewBox="0 0 24 24"><path d="M12 3l8 4v5c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V7l8-4z"/></svg>',
    documents: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    home: '<svg viewBox="0 0 24 24"><path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/></svg>',
    baseline: '<svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h10"/></svg>',
    drive: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="7.5" cy="12" r="1" style="fill:currentColor"/><path d="M12 12h5"/></svg>',
    folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  };

  let state = null;
  let draft = {runs: '', history: ''};
  let preview = {runs: null, history: null};
  const inspectTimers = {runs: 0, history: 0};
  const inspectSeq = {runs: 0, history: 0};
  let saving = false;

  // Folder browser dialog state
  let browserKind = 'runs';
  let browserState = null;
  let browseSeq = 0;

  const samePath = (a, b) => {
    const na = String(a || '').trim().replace(/[\\/]+$/, '');
    const nb = String(b || '').trim().replace(/[\\/]+$/, '');
    return na.toLowerCase() === nb.toLowerCase();
  };

  const isNested = (a, b) => {
    const na = String(a || '').trim().replace(/[\\/]+$/, '').toLowerCase();
    const nb = String(b || '').trim().replace(/[\\/]+$/, '').toLowerCase();
    if (!na || !nb) return false;
    if (na === nb) return true;
    return na.startsWith(nb + '\\') || na.startsWith(nb + '/') || nb.startsWith(na + '\\') || nb.startsWith(na + '/');
  };

  function pathSegments(raw) {
    const text = String(raw || '').trim();
    if (!text) return [];
    const winDrive = /^([A-Za-z]:)[\\/](.*)$/.exec(text);
    if (winDrive) {
      const root = winDrive[1] + '\\';
      const rest = winDrive[2].split(/[\\/]+/).filter(Boolean);
      const segs = [{label: winDrive[1], path: root}];
      let acc = root;
      for (const part of rest) {
        acc = acc.endsWith('\\') ? acc + part : acc + '\\' + part;
        segs.push({label: part, path: acc});
      }
      return segs;
    }
    if (text.startsWith('/')) {
      const rest = text.split('/').filter(Boolean);
      const segs = [{label: '/', path: '/'}];
      let acc = '';
      for (const part of rest) {
        acc += '/' + part;
        segs.push({label: part, path: acc});
      }
      return segs;
    }
    return [];
  }

  function showStorageError(msg) {
    const el = $('#stError');
    if (!el) return;
    el.textContent = msg || '';
    el.hidden = !msg;
  }

  function showStorageNotice(msg) {
    const el = $('#stNotice');
    if (!el) return;
    el.textContent = msg || '';
    el.hidden = !msg;
  }

  function sourceBadge(slot) {
    if (slot.locked) return ['is-lock', `Locked · ${slot.env_var}`];
    if (slot.source === 'settings') return ['is-custom', 'Custom path'];
    return ['is-def', 'Default'];
  }

  function renderTrail(kind) {
    const trail = $(`#stTrail-${kind}`);
    if (!trail || !state) return;
    const segs = pathSegments(draft[kind]);
    if (!segs.length) {
      trail.innerHTML = '<span class="st-trail-empty">Enter an absolute folder path</span>';
      return;
    }
    // Keep the trail concise on deep paths: root + ellipsis + last 3 segments
    const shown = segs.length > 4 ? [segs[0], {ellipsis: true}, ...segs.slice(-3)] : segs;
    const locked = state[kind].locked;
    trail.innerHTML = shown.map((seg, idx) => {
      const sep = idx ? '<span class="st-trail-sep" aria-hidden="true">›</span>' : '';
      if (seg.ellipsis) return `${sep}<span class="st-trail-ell">…</span>`;
      const isLast = idx === shown.length - 1;
      if (locked) return `${sep}<span class="st-trail-seg${isLast ? ' is-cur' : ''}">${esc(seg.label)}</span>`;
      return `${sep}<button type="button" class="st-trail-seg${isLast ? ' is-cur' : ''}" data-trail-kind="${kind}" data-trail-path="${esc(seg.path)}" title="Browse ${esc(seg.path)}">${esc(seg.label)}</button>`;
    }).join('');
  }

  function renderCalendarMatrix(info) {
    const box = $('#stCalendar');
    const yearEl = $('#stCalYear');
    const seedBox = $('#stSeedRow');
    if (!box || !seedBox || !state) return;

    const months = (info && info.valid && Array.isArray(info.months)) ? info.months : [];
    const latestYear = months.length ? months[months.length - 1].slice(0, 4) : String(new Date().getFullYear());
    const activeSet = new Set(months.filter(m => m.startsWith(latestYear + '-')).map(m => Number(m.slice(5, 7))));
    const otherYears = [...new Set(months.map(m => m.slice(0, 4)))].filter(y => y !== latestYear);

    if (yearEl) {
      yearEl.textContent = latestYear + (otherYears.length ? ` (+${otherYears.join(', ')})` : '');
    }
    box.innerHTML = MONTH_NAMES.map((name, i) => {
      const has = activeSet.has(i + 1);
      return `<div class="st-mcell${has ? ' is-hit' : ''}" title="${name} ${latestYear}: ${has ? 'Settled snapshot present' : 'No snapshot'}"><span>${name}</span><i></i></div>`;
    }).join('');

    const emptyTarget = info && info.valid && (info.will_create || info.count === 0);
    const changed = !samePath(draft.history, state.history.path);
    const canSeed = !state.history.locked && state.bundled_history_count > 0 && emptyTarget && changed;
    seedBox.hidden = !canSeed;
    if (canSeed) {
      $('#stSeedLabel').textContent = `Copy bundled baseline snapshots (${state.bundled_history_count} months) into this empty folder`;
    }
  }

  function renderTelemetry(kind) {
    const info = preview[kind];
    const active = state?.[kind];
    const el = $(`#stTel-${kind}`);
    const card = $(`#stCard-${kind}`);
    if (!el || !card || !active) return;

    renderTrail(kind);

    if (!info) {
      el.innerHTML = '<span class="st-tel-main"><span class="st-dot"></span><span>Checking folder…</span></span>';
      card.dataset.status = 'checking';
      return;
    }

    if (!info.valid) {
      card.dataset.status = 'bad';
      el.innerHTML = `<span class="st-tel-main"><span class="st-dot is-bad"></span><span class="st-tel-bad">${esc(info.error || 'Invalid folder path')}</span></span>`;
      if (kind === 'history') renderCalendarMatrix(info);
      return;
    }

    let mainHtml = '';
    if (info.will_create) {
      card.dataset.status = 'create';
      mainHtml = '<span class="st-tel-main"><span class="st-dot is-warn"></span><span>New folder will be created on apply</span></span>';
    } else {
      card.dataset.status = 'ok';
      mainHtml = `<span class="st-tel-main"><span class="st-dot is-ok"></span><span>Writable · <b>${esc(info.count_label)}</b>${info.bytes ? ' · ' + esc(fmtBytes(info.bytes)) : ''}</span></span>`;
    }
    let chipHtml = '';
    if (kind === 'history') {
      chipHtml = info.in_git_repo
        ? '<span class="st-chip is-git">Inside Git · settles commit</span>'
        : '<span class="st-chip">Outside Git · safe</span>';
      renderCalendarMatrix(info);
    } else {
      chipHtml = '<span class="st-chip">Live switch</span>';
    }
    el.innerHTML = mainHtml + chipHtml;
  }

  function renderTopologyAndDock() {
    if (!state) return;
    const rInfo = preview.runs;
    const hInfo = preview.history;
    const rChanged = !samePath(draft.runs, state.runs.path);
    const hChanged = !samePath(draft.history, state.history.path);
    const dirty = rChanged || hChanged;

    const rCount = rInfo?.valid ? rInfo.count : state.runs.count;
    const hCount = hInfo?.valid ? hInfo.count : state.history.count;
    const rBytes = rInfo?.valid ? rInfo.bytes : state.runs.bytes;
    const hBytes = hInfo?.valid ? hInfo.bytes : state.history.bytes;

    $('#stCountRuns').textContent = String(rCount);
    $('#stCountHistory').textContent = String(hCount);
    $('#stMetaRuns').textContent = rInfo?.will_create ? 'Will create' : (rBytes ? fmtBytes(rBytes) : 'Empty folder');
    $('#stMetaHistory').textContent = hInfo?.will_create ? 'Will create' : (hBytes ? fmtBytes(hBytes) : 'Empty folder');

    const inGit = Boolean(hInfo?.valid && hInfo.in_git_repo);
    $('#stIsoValue').textContent = inGit ? 'History in Git' : 'Outside Git';
    $('#stIsoSub').textContent = inGit ? 'Settles commit locally' : 'Protected from update.bat';
    $('#stIsoDot')?.classList.toggle('is-git', inGit);

    const settingsName = state.settings_file.split(/[\\/]/).pop() || 'settings.json';
    $('#stSettingsName').textContent = settingsName;
    $('#stSettingsPath').textContent = state.settings_file;
    $('#stSettingsPath').title = state.settings_file;

    // Sidebar custom indicator dot
    const customActive = state.runs.source === 'settings' || state.history.source === 'settings';
    $('#storageNavDot')?.classList.toggle('on', customActive);

    const rResolved = rInfo?.resolved || draft.runs;
    const hResolved = hInfo?.resolved || draft.history;
    const overlap = Boolean(rInfo?.valid && hInfo?.valid && isNested(rResolved, hResolved));

    const pill = $('#stVaultPill');
    const applyBtn = $('#stApply');
    const dock = $('#stDock');
    const hint = $('#stApplyHint');
    const resetAll = $('#stResetAll');

    const anyEditable = !state.runs.locked || !state.history.locked;
    const atDefault = (state.runs.locked || samePath(draft.runs, state.runs.default_path))
      && (state.history.locked || samePath(draft.history, state.history.default_path));
    resetAll.disabled = saving || !anyEditable || atDefault;

    if (state.busy) {
      pill.textContent = 'Run active';
      pill.className = 'st-hpill is-warn';
      dock.dataset.state = 'warn';
      applyBtn.disabled = true;
      hint.textContent = 'Wait for the running calculation to finish before changing storage folders.';
      return;
    }
    if (overlap) {
      pill.textContent = 'Path conflict';
      pill.className = 'st-hpill is-bad';
      dock.dataset.state = 'bad';
      applyBtn.disabled = true;
      hint.textContent = 'Working runs and settled history must be separate, non-nested folders.';
      return;
    }
    if ((rInfo && !rInfo.valid) || (hInfo && !hInfo.valid)) {
      pill.textContent = 'Check path';
      pill.className = 'st-hpill is-bad';
      dock.dataset.state = 'bad';
      applyBtn.disabled = true;
      hint.textContent = 'Fix the highlighted folder path before applying.';
      return;
    }
    if (!dirty) {
      pill.textContent = 'Synced';
      pill.className = 'st-hpill is-ok';
      dock.dataset.state = 'synced';
      applyBtn.disabled = true;
      hint.textContent = 'Active folders match your current selection. Switching never moves or deletes existing files.';
      return;
    }
    const changedLabels = [rChanged && 'Working runs', hChanged && 'Settled history'].filter(Boolean).join(' & ');
    pill.textContent = 'Unsaved';
    pill.className = 'st-hpill is-dirty';
    dock.dataset.state = 'dirty';
    applyBtn.disabled = saving || !rInfo || !hInfo;
    hint.textContent = `Ready to switch ${changedLabels} live without restarting FSNE.`;
  }

  function renderSlots() {
    if (!state) return;
    for (const kind of ['runs', 'history']) {
      const slot = state[kind];
      const [cls, label] = sourceBadge(slot);
      const badge = $(`#stBadge-${kind}`);
      badge.className = `st-src ${cls}`;
      badge.textContent = label;

      const input = $(`#stInput-${kind}`);
      if (document.activeElement !== input) input.value = draft[kind];
      input.disabled = slot.locked || saving;
      $(`#stBrowse-${kind}`).disabled = slot.locked || saving;
      const nativeBtn = $(`#stNative-${kind}`);
      if (nativeBtn) {
        nativeBtn.hidden = !state.can_native_browse;
        nativeBtn.disabled = slot.locked || saving;
      }
      const defBtn = $(`#stDefault-${kind}`);
      defBtn.disabled = slot.locked || saving || samePath(draft[kind], slot.default_path);
      renderTelemetry(kind);
    }
    renderTopologyAndDock();
  }

  function scheduleInspect(kind, value, immediate = false) {
    clearTimeout(inspectTimers[kind]);
    draft[kind] = value;
    if (state && samePath(value, state[kind].path)) {
      preview[kind] = {
        valid: state[kind].valid !== false,
        resolved: state[kind].path,
        exists: state[kind].exists,
        will_create: state[kind].will_create,
        writable: state[kind].writable,
        count: state[kind].count,
        count_label: state[kind].count_label,
        months: state[kind].months,
        in_git_repo: state[kind].in_git_repo,
        bytes: state[kind].bytes,
        error: state[kind].error,
      };
      renderSlots();
      return;
    }
    preview[kind] = null;
    renderSlots();
    const run = async () => {
      const seq = ++inspectSeq[kind];
      try {
        const res = await api('storage/inspect', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({kind, path: value}),
        });
        if (seq !== inspectSeq[kind]) return;
        preview[kind] = res;
        renderSlots();
      } catch (e) {
        if (seq !== inspectSeq[kind]) return;
        preview[kind] = {valid: false, error: e.message};
        renderSlots();
      }
    };
    if (immediate) run();
    else inspectTimers[kind] = setTimeout(run, 220);
  }

  async function loadStorage(silent = false) {
    try {
      const data = await api('storage');
      state = data;
      draft = {runs: data.runs.path, history: data.history.path};
      preview = {
        runs: {...data.runs, resolved: data.runs.path},
        history: {...data.history, resolved: data.history.path},
      };
      $('#stSeedCheck').checked = true;
      showStorageError('');
      renderSlots();
    } catch {
      if (!silent) showStorageError('Storage settings are only available when running the live FSNE app.');
    }
  }

  async function applyStorage() {
    if (!state || saving) return;
    saving = true;
    showStorageError('');
    showStorageNotice('');
    renderSlots();
    try {
      const payload = {seed_history: !$('#stSeedRow').hidden && $('#stSeedCheck').checked};
      if (!state.runs.locked) payload.runs_dir = samePath(draft.runs, state.runs.default_path) ? null : draft.runs.trim();
      if (!state.history.locked) payload.history_dir = samePath(draft.history, state.history.default_path) ? null : draft.history.trim();
      const updated = await api('storage', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      state = updated;
      draft = {runs: updated.runs.path, history: updated.history.path};
      preview = {
        runs: {...updated.runs, resolved: updated.runs.path},
        history: {...updated.history, resolved: updated.history.path},
      };
      showStorageNotice('Storage folders updated and saved. Active runs list refreshed.');
      window.dispatchEvent(new CustomEvent('fsne:storage-changed', {detail: updated}));
      if (typeof toast === 'function') toast('Storage folders updated');
    } catch (e) {
      showStorageError(e.message);
    } finally {
      saving = false;
      renderSlots();
    }
  }

  // ---- In-app Folder Navigator Dialog -----------------------------------
  async function loadBrowser(targetPath) {
    const seq = ++browseSeq;
    browserState = null;
    $('#fbError').hidden = true;
    $('#fbChoose').disabled = true;
    $('#fbNewBtn').disabled = true;
    $('#fbUp').disabled = true;
    $('#fbList').innerHTML = '<div class="fb-empty">Loading folders…</div>';
    try {
      const qs = new URLSearchParams({kind: browserKind});
      if (targetPath) qs.set('path', targetPath);
      const data = await api('storage/browse?' + qs.toString());
      if (seq !== browseSeq) return;
      browserState = data;
      $('#fbFilter').value = '';
      renderBrowser();
    } catch (e) {
      if (seq !== browseSeq) return;
      $('#fbError').textContent = e.message;
      $('#fbError').hidden = false;
      $('#fbList').innerHTML = '<div class="fb-empty">Could not read this folder.</div>';
    }
  }

  function renderBrowser() {
    if (!browserState) return;
    const {path, parent, writable, breadcrumbs, bookmarks} = browserState;
    $('#fbCurrentPath').textContent = path;
    $('#fbCurrentPath').title = path;
    $('#fbUp').disabled = !parent;
    $('#fbChoose').disabled = !writable;
    $('#fbNewBtn').disabled = !writable;
    $('#fbWriteNote').textContent = writable ? 'Writable folder' : 'Read-only — choose another folder';
    $('#fbWriteNote').className = 'fb-write ' + (writable ? 'is-ok' : 'is-bad');

    $('#fbCrumbs').innerHTML = breadcrumbs.map((c, idx) => {
      const sep = idx && breadcrumbs[idx - 1].label !== '/' ? '<span class="fb-sep">/</span>' : '';
      return `${sep}<button type="button" class="fb-crumb" data-bpath="${esc(c.path)}">${esc(c.label)}</button>`;
    }).join('');

    $('#fbBookmarks').innerHTML = bookmarks.map(b => {
      const on = samePath(b.path, path);
      return `<button type="button" class="fb-bm${on ? ' is-on' : ''}" data-bpath="${esc(b.path)}" title="${esc(b.path)}">${ICONS[b.role] || ICONS.folder}<span>${esc(b.label)}</span></button>`;
    }).join('');

    renderBrowserFolders();
  }

  function renderBrowserFolders() {
    if (!browserState) return;
    const q = $('#fbFilter').value.trim().toLowerCase();
    const list = browserState.folders.filter(f => !q || f.name.toLowerCase().includes(q));
    if (!list.length) {
      $('#fbList').innerHTML = `<div class="fb-empty">${q ? 'No subfolders match your filter.' : 'No subfolders here. Create one below or select this folder.'}</div>`;
      return;
    }
    $('#fbList').innerHTML = list.map(f =>
      `<button type="button" class="fb-row" data-fpath="${esc(f.path)}">${ICONS.folder}<span class="fb-name">${esc(f.name)}</span>${f.hint ? `<span class="fb-hint">${esc(f.hint)}</span>` : ''}<span class="fb-go" aria-hidden="true">›</span></button>`
    ).join('');
  }

  function openBrowser(kind, startPath) {
    if (!state || state[kind].locked) return;
    browserKind = kind;
    $('#fbTitle').textContent = kind === 'runs' ? 'Choose working runs folder' : 'Choose settled history folder';
    $('#fbNative').hidden = !state.can_native_browse;
    $('#fbNewRow').hidden = true;
    $('#fbNewName').value = '';
    $('#folderDialog').showModal();
    loadBrowser(startPath || draft[kind] || state[kind].path);
  }

  async function openNativePicker(kind) {
    if (!state || !state.can_native_browse || state[kind].locked) return;
    try {
      const res = await api('storage/pick', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          initial_path: draft[kind] || state[kind].path,
          title: kind === 'runs' ? 'Select FSNE working runs folder' : 'Select FSNE settled history folder',
        }),
      });
      if (!res.cancelled && res.path) {
        if ($('#folderDialog').open) $('#folderDialog').close();
        $(`#stInput-${kind}`).value = res.path;
        scheduleInspect(kind, res.path, true);
      }
    } catch (e) {
      showStorageError(e.message);
    }
  }

  // ---- Event wiring -----------------------------------------------------
  for (const kind of ['runs', 'history']) {
    const input = $(`#stInput-${kind}`);
    if (!input) continue;
    input.addEventListener('input', () => scheduleInspect(kind, input.value, false));
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !$('#stApply').disabled) applyStorage();
    });
    $(`#stBrowse-${kind}`).addEventListener('click', () => openBrowser(kind));
    $(`#stNative-${kind}`)?.addEventListener('click', () => openNativePicker(kind));
    $(`#stDefault-${kind}`).addEventListener('click', () => {
      if (!state || state[kind].locked) return;
      input.value = state[kind].default_path;
      scheduleInspect(kind, state[kind].default_path, true);
    });
    $(`#stCopy-${kind}`).addEventListener('click', async () => {
      const val = input.value || state?.[kind]?.path || '';
      if (!val) return;
      try {
        await navigator.clipboard.writeText(val);
        if (typeof toast === 'function') toast('Copied folder path');
      } catch {
        input.select();
        document.execCommand('copy');
        if (typeof toast === 'function') toast('Copied folder path');
      }
    });
    $(`#stTrail-${kind}`)?.addEventListener('click', e => {
      const btn = e.target.closest('[data-trail-path]');
      if (btn) openBrowser(btn.dataset.trailKind, btn.dataset.trailPath);
    });
  }

  $('#stApply')?.addEventListener('click', applyStorage);
  $('#stResetAll')?.addEventListener('click', () => {
    if (!state) return;
    for (const kind of ['runs', 'history']) {
      if (!state[kind].locked) {
        $(`#stInput-${kind}`).value = state[kind].default_path;
        scheduleInspect(kind, state[kind].default_path, true);
      }
    }
  });
  $('#stCopySettings')?.addEventListener('click', async () => {
    if (!state?.settings_file) return;
    try {
      await navigator.clipboard.writeText(state.settings_file);
      if (typeof toast === 'function') toast('Copied settings.json path');
    } catch {}
  });

  // Folder navigator interactions
  $('#fbClose')?.addEventListener('click', () => $('#folderDialog').close());
  $('#fbCancel')?.addEventListener('click', () => $('#folderDialog').close());
  $('#fbUp')?.addEventListener('click', () => browserState?.parent && loadBrowser(browserState.parent));
  $('#fbFilter')?.addEventListener('input', renderBrowserFolders);
  $('#fbCrumbs')?.addEventListener('click', e => {
    const b = e.target.closest('[data-bpath]');
    if (b) loadBrowser(b.dataset.bpath);
  });
  $('#fbBookmarks')?.addEventListener('click', e => {
    const b = e.target.closest('[data-bpath]');
    if (b) loadBrowser(b.dataset.bpath);
  });
  $('#fbList')?.addEventListener('click', e => {
    const row = e.target.closest('[data-fpath]');
    if (row) loadBrowser(row.dataset.fpath);
  });
  $('#fbNewBtn')?.addEventListener('click', () => {
    const row = $('#fbNewRow');
    row.hidden = !row.hidden;
    if (!row.hidden) { $('#fbNewName').value = ''; $('#fbNewName').focus(); }
  });
  $('#fbNewCreate')?.addEventListener('click', async () => {
    if (!browserState) return;
    const name = $('#fbNewName').value.trim();
    if (!name) return;
    $('#fbError').hidden = true;
    try {
      browserState = await api('storage/mkdir', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({parent: browserState.path, name}),
      });
      $('#fbNewRow').hidden = true;
      $('#fbNewName').value = '';
      renderBrowser();
    } catch (e) {
      $('#fbError').textContent = e.message;
      $('#fbError').hidden = false;
    }
  });
  $('#fbNewName')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); $('#fbNewCreate').click(); }
    if (e.key === 'Escape') { $('#fbNewRow').hidden = true; }
  });
  $('#fbChoose')?.addEventListener('click', () => {
    if (!browserState) return;
    $(`#stInput-${browserKind}`).value = browserState.path;
    scheduleInspect(browserKind, browserState.path, true);
    $('#folderDialog').close();
  });
  $('#fbNative')?.addEventListener('click', () => openNativePicker(browserKind));

  $$('.item[data-view="storage"]').forEach(b => b.addEventListener('click', () => loadStorage(false)));
  loadStorage(true);
})();
