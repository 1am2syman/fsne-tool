/* Inventory evolution page: lens, unit and spacing controls around the reference Sankey renderer (refchart.js).
 * Loaded on first visit by ui/template.html. Payloads come from api/evolution (src/evolution.py):
 * a small graph first, then the per-lineage lists that make hover and lock tracing exact. */
(function (root) {
  'use strict';
  const FSNE = root.FSNE || (root.FSNE = {});
  const $ = (selector) => document.querySelector(selector);
  const RSL_ORDER = ['>10M', '6M–10M', '3M–6M', '<3M', 'Unknown'];
  const GAP = { min: 160, max: 420, start: 240 };
  const LENSES = [
    { id: 'exposure', key: 'exposure_bdt', short: 'Exposure', label: 'Exposure — Mitigation Categories', dimension: 'mitigation_category', policy: 'excluded' },
    { id: 'lnd', key: 'lnd_fc_bdt', short: 'L&D FC', label: 'L&D FC — Mitigation Categories', dimension: 'mitigation_category', policy: 'excluded' },
    { id: 'stock', key: 'total_closing_stock_bdt', short: 'Closing stock', label: 'Total Closing Stock — Mitigation Categories', dimension: 'mitigation_category', policy: 'excluded' },
    { id: 'rsl', key: 'rsl_group_total_closing_stock_bdt', short: 'RSL', label: 'RSL Group — Total Closing Stock', dimension: 'rsl_group', policy: 'included' },
    { id: 'cohort', key: 'over_10m_exposure_cohort_bdt', short: '>10M cohort', label: 'Exposure Originating in >10M Cohort', dimension: 'mitigation_category', policy: 'excluded' },
  ];
  const state = { lens: 'exposure', unit: 'bdt', hide10: false, gap: GAP.start, asOf: null, reportUnits: false };
  const cache = new Map();
  const filters = { town: [], parent: [], material: [], brand: [], rsl: [] };
  let reportScope = {}, filterSeq = 0, materialLabels = {};
  const filterCatalogs = new Map();
  const DATASETS = { default_metric: LENSES[0].key, metric_options: LENSES.map((l) => ({ key: l.key, label: l.label, category_dimension: l.dimension, included_good_stock_policy: l.policy })), metrics: {} };
  let env = null, ctrl = null, mounted = false, seq = 0, lockSeq = 0, pendingLockId = null, current = null, observer = null, lastWidth = 0, drill = null;

  const host = {
    svg: null, tooltip: null, menu: null, legend: null, scroller: null,
    layoutHeight: 760, cropHeight: true, minHeight: 420,
    unit: { div: 1e7, label: 'Cr' }, pctOnly: false, includeOver10: true, spacing: GAP.start,
    onLock: () => syncSelection(),
    openSelection: (datum) => openSelection(datum),
    detailsReady: () => (current ? current.detailsPromise : Promise.resolve()),
    fillLineage: (records) => { if (current) current.fill(records); },
    lineageRows: (record, kind) => (current ? current.lineageRows(record, kind) : []),
  };

  /* ---------- data ---------- */
  const lensById = (id) => LENSES.find((l) => l.id === id);
  const activeLens = () => {
    const base = lensById(state.lens);
    if (!drill) return base;
    const rsl = drill.dimension === 'rsl_group';
    return { ...base, key: `selected_${rsl ? 'rsl' : 'category'}_${base.key}`, dimension: drill.dimension,
      label: `${base.id === 'rsl' ? 'Closing stock' : base.short} — Selected ${rsl ? 'RSL' : 'category'} evolution` };
  };
  const refHeight = () => Math.max(760, Math.floor(Math.min(innerHeight * 0.84, 920) * 64) / 64);
  const clampGap = (v) => Math.max(GAP.min, Math.min(GAP.max, Math.round(+v || GAP.start)));

  async function getJson(path) {
    const response = await fetch(env.api(path), { cache: 'no-cache' });
    if (!response.ok) {
      let message = 'Request failed';
      try { message = (await response.json()).error || message; } catch { /* keep the generic message */ }
      throw new Error(message);
    }
    return response.json();
  }

  function entryFor(lens, unit) {
    const query = filterQuery() + (drill ? '&path=' + encodeURIComponent(JSON.stringify(drill.path)) : '');
    const route = lens.id + (drill ? '/selection' : '');
    const key = route + '|' + unit + '|' + query;
    if (cache.has(key)) return cache.get(key);
    const entry = { key, lens, unit, payload: null, detail: null, detailsPromise: null, main: null };
    entry.main = getJson(`evolution/${route}?unit=${unit}${query}`).then((payload) => { entry.payload = payload; return payload; });
    entry.main.catch(() => cache.delete(key));
    entry.detailsPromise = entry.main.then(() => getJson(`evolution/${route}/detail?unit=${unit}${query}`)).then((detail) => attach(entry, detail));
    entry.detailsPromise.catch(() => {});
    entry.fill = (records) => fillRecords(entry, records);
    entry.lineageRows = (record, kind) => lineageRows(entry, record, kind);
    if (cache.size >= 16) cache.delete(cache.keys().next().value);
    cache.set(key, entry);
    return entry;
  }

  /* the detail payload holds [lineage ids, values] per link and transition id; the chart wants [id, value] pairs */
  function attach(entry, detail) {
    entry.detail = detail;
    const pairs = (ids, values) => ids.map((id, i) => [id, values[i]]);
    ['links', 'transitions'].forEach((name) => entry.payload[name].forEach((record) => {
      const row = detail[name][record.id];
      record.lineage_values = row ? pairs(row[0], row[1]) : [];
    }));
    return entry;
  }

  function fillRecords(entry, records) {
    const byId = new Map([...entry.payload.links, ...entry.payload.transitions].map((r) => [r.id, r]));
    (records || []).forEach((record) => {
      const source = byId.get(record.id);
      if (source && !(record.lineage_values && record.lineage_values.length)) record.lineage_values = source.lineage_values;
    });
  }

  function lineageRows(entry, record, kind) {
    const cacheKey = '_rows_' + kind;
    if (record[cacheKey]) return record[cacheKey];
    const detail = entry.detail;
    const rows = [];
    if (detail) {
      const { brand, rsl, exp } = detail.lineages;
      (record.lineage_values || []).forEach(([id, value]) => {
        if (kind === 'rsl') rows.push([id, RSL_ORDER[rsl[id]] || 'Unknown', value]);
        else if (kind === 'brand') rows.push([id, detail.brands[brand[id]], value]);
        else {
          rows.push([id, 'Exposure', value * exp[id]]);
          rows.push([id, 'Non-exposure closing stock', value * (1 - exp[id])]);
        }
      });
    }
    record[cacheKey] = rows;
    return rows;
  }

  /* ---------- dropdown filters ---------- */
  function filterQuery(values = filters) {
    const params = new URLSearchParams();
    Object.entries(values).forEach(([key, selected]) => selected.forEach(value => params.append(key, value)));
    if (state.asOf) params.set('as_of', state.asOf);
    return params.size ? '&' + params.toString() : '';
  }

  function filterLabel(key, value) {
    if (key === 'material') return materialLabels[value] || value;
    if (key === 'parent') return env.parents?.[value] || value;
    return value;
  }

  function updateFilterLabels() {
    Object.entries(filters).forEach(([key, values]) => {
      const dropdown = $(`#evFilter-${key}`);
      dropdown.dataset.active = String(values.length > 0);
      const valueLabel = value => filterLabel(key, value);
      dropdown.querySelector('summary span').textContent = values.length === 1 ? valueLabel(values[0]) : values.length ? `${values.length} selected` : 'All';
      dropdown.querySelector('summary').title = values.map(valueLabel).join(', ');
      dropdown.dataset.scoped = String(Boolean(reportScope[key]?.length));
    });
    $('#evFilterClear').hidden = !Object.entries(filters).some(([key, values]) => JSON.stringify(values) !== JSON.stringify(reportScope[key] || []));
  }

  function mountFilters() {
    const element = (tag, attrs, text) => {
      const node = document.createElement(tag);
      Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
      if (text) node.textContent = text;
      return node;
    };
    const controls = Object.keys(filters).map(key => {
      const title = key === 'rsl' ? 'RSL' : key[0].toUpperCase() + key.slice(1);
      const dropdown = element('details', { class: 'evo-filter', id: `evFilter-${key}`, 'data-filter': key });
      const summary = element('summary', { 'aria-label': `${title} filter` }, title);
      summary.append(element('span', {}, 'All'));
      const pop = element('div', { class: 'evo-filter-pop' });
      const search = element('input', { type: 'search', 'aria-label': `Search ${key} options`, placeholder: `Search ${key}`, autocomplete: 'off' });
      const options = element('div', { class: 'evo-options', role: 'group', 'aria-label': `${title} options` });
      options.append(element('p', { class: 'evo-filter-empty' }, 'Loading options…'));
      const actions = element('div', { class: 'evo-filter-actions' });
      actions.append(element('button', { type: 'button', class: 'btn', 'data-all': '' }, 'All in scope'), element('button', { type: 'button', class: 'btn primary', 'data-apply': '', disabled: '' }, 'Apply'));
      pop.append(search, options, actions);
      dropdown.append(summary, pop);
      return dropdown;
    });
    $('#evFilters').replaceChildren(...controls, element('button', { type: 'button', class: 'btn ghost', id: 'evFilterClear', hidden: '' }, 'Clear filters'), element('span', { id: 'evFilterStatus', role: 'status' }));
    document.querySelectorAll('#evFilters details').forEach(dropdown => {
      dropdown.addEventListener('toggle', () => {
        if (!dropdown.open) return;
        document.querySelectorAll('#evFilters details').forEach(other => { if (other !== dropdown) other.open = false; });
        const selected = filters[dropdown.dataset.filter];
        dropdown.querySelectorAll('input[type=checkbox]').forEach(input => { input.checked = selected.includes(input.value); });
        dropdown.querySelector('input[type=search]').value = '';
        searchFilterOptions(dropdown);
        dropdown.querySelector('input[type=search]').focus();
      });
      dropdown.querySelector('input[type=search]').oninput = () => searchFilterOptions(dropdown);
      dropdown.querySelector('[data-all]').onclick = () => dropdown.querySelectorAll('input[type=checkbox]').forEach(input => { input.checked = false; });
      dropdown.querySelector('[data-apply]').onclick = () => {
        const inputs = [...dropdown.querySelectorAll('input[type=checkbox]')];
        const selected = inputs.filter(input => input.checked).map(input => input.value).sort();
        const key = dropdown.dataset.filter;
        filters[key] = selected.length === inputs.length || !selected.length ? [...(reportScope[key] || [])] : selected;
        dropdown.open = false;
        dropdown.querySelector('summary').focus();
        applyFilters();
      };
      dropdown.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        dropdown.open = false;
        dropdown.querySelector('summary').focus();
        event.stopPropagation();
      });
    });
    document.addEventListener('click', event => {
      if (!event.target.closest('#evFilters')) document.querySelectorAll('#evFilters details').forEach(dropdown => { dropdown.open = false; });
    });
    $('#evFilterClear').onclick = () => {
      Object.keys(filters).forEach(key => { filters[key] = [...(reportScope[key] || [])]; });
      document.querySelectorAll('#evFilters details').forEach(dropdown => { dropdown.open = false; });
      applyFilters();
    };
  }

  function searchFilterOptions(dropdown) {
    const query = dropdown.querySelector('input[type=search]').value.trim().toLowerCase();
    let visible = 0;
    dropdown.querySelectorAll('.evo-options label').forEach(label => {
      label.hidden = !label.textContent.toLowerCase().includes(query);
      if (!label.hidden) visible++;
    });
    const empty = dropdown.querySelector('[data-empty]');
    if (empty) empty.hidden = visible > 0;
  }

  function loadFilters() {
    const request = ++filterSeq;
    const requests = Object.keys(filters).map(async key => {
      const dropdown = $(`#evFilter-${key}`);
      dropdown.querySelector('[data-apply]').disabled = true;
      dropdown.querySelector('.evo-options').textContent = 'Loading options…';
      // Keep the clicked report bounds while letting each dropdown expand its own selection.
      const context = { ...filters, [key]: reportScope[key] || [] };
      const path = 'evolution/filters?' + filterQuery(context).slice(1);
      const cacheKey = env.api(path);
      if (!filterCatalogs.has(cacheKey)) {
        const pending = getJson(path).catch(error => { filterCatalogs.delete(cacheKey); throw error; });
        filterCatalogs.set(cacheKey, pending);
        if (filterCatalogs.size > 64) filterCatalogs.delete(filterCatalogs.keys().next().value);
      }
      const options = await filterCatalogs.get(cacheKey);
      if (request !== filterSeq) return;
      materialLabels = { ...materialLabels, ...options.material_labels };
      const labels = (options[key] || []).map(value => {
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = value;
        input.checked = filters[key].includes(value);
        label.append(input, document.createTextNode(filterLabel(key, value)));
        return label;
      });
      const empty = document.createElement('p');
      empty.className = 'evo-filter-empty';
      empty.dataset.empty = '';
      empty.hidden = labels.length > 0;
      empty.textContent = labels.length ? 'No matches' : 'No options in this scope';
      dropdown.querySelector('.evo-options').replaceChildren(...labels, empty);
      dropdown.querySelector('[data-apply]').disabled = false;
      searchFilterOptions(dropdown);
    });
    return Promise.all(requests).then(() => {
      if (request !== filterSeq) return;
      updateFilterLabels();
      $('#evFilterStatus').textContent = '';
    }).catch(() => {
      if (request !== filterSeq) return;
      $('#evFilterStatus').textContent = 'Filters unavailable. ';
      const retry = document.createElement('button');
      retry.className = 'btn';
      retry.type = 'button';
      retry.textContent = 'Retry';
      retry.onclick = loadFilters;
      $('#evFilterStatus').append(retry);
    });
  }

  function applyFilters() {
    updateFilterLabels();
    clearLock();
    loadFilters();
    draw();
  }

  /* ---------- page ---------- */
  function setStatus(markup) {
    env.root.dataset.state = markup ? 'status' : 'chart';
    $('#evStatus').html = markup || '';
  }

  function syncToolbar() {
    document.querySelectorAll('#evLens button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.lens === state.lens)));
    $('#evFilters').hidden = Boolean(drill);
    $('#evLens').hidden = Boolean(drill);
    $('#evDrill').hidden = !drill;
    if (drill) {
      $('#evDrillScope').textContent = drill.trail.join(' → ');
      $('#evDrillScope').title = drill.trail.join(' → ');
      $('#evDrillTitle').textContent = drill.dimension === 'rsl_group' ? 'RSL evolution' : 'Category evolution';
      const parent = drill.parent.scope;
      const dimension = parent.drill?.dimension || lensById(parent.lens).dimension;
      $('#evBack').textContent = dimension === 'rsl_group' ? '← Back to RSL' : '← Back to categories';
    }
    const ten = $('#evTen');
    ten.hidden = Boolean(drill) || state.lens !== 'rsl';
    ten.setAttribute('aria-pressed', String(!state.hide10));
    $('#evGap').value = state.gap;
    $('#evGapOut').textContent = state.gap;
    $('#evGapReset').disabled = state.gap === GAP.start;
  }

  async function draw() {
    const mySeq = ++seq;
    const lens = activeLens();
    const entry = entryFor(lens, state.unit);
    syncToolbar();
    if (!entry.payload) { current = null; setStatus('<div class="evo-skel" role="status">Loading graph</div>'); }
    let payload;
    try {
      payload = await entry.main;
      if (mySeq !== seq) return;
      // The >10M scope is derived from lineage lists, so wait for them in that one case.
      if (!drill && lens.id === 'rsl' && state.hide10) await entry.detailsPromise;
    } catch (error) {
      if (mySeq !== seq) return;
      setStatus(`<div class="evo-state"><h3>Graph unavailable</h3><p>${escapeHtml(error.message)}</p><button class="btn primary" id="evRetry">Retry</button></div>`);
      $('#evRetry').onclick = () => { cache.delete(entry.key); return draw(); };
      return;
    }
    if (mySeq !== seq) return;
    if (!payload.nodes.length) {
      current = null;
      setStatus('<div class="evo-state"><h3>No matching stock</h3><p>Change the filters or choose another lens.</p></div>');
      return;
    }
    setStatus('');
    current = entry;
    DATASETS.metrics = { [lens.key]: payload };
    DATASETS.default_metric = lens.key;
    Object.assign(host, {
      layoutHeight: refHeight(),
      unit: state.reportUnits && state.unit === 'bdt' ? { div: 1e6, label: 'M BDT' } : { div: payload.unit_div, label: payload.unit_label },
      includeOver10: Boolean(drill) || !state.hide10, spacing: state.gap,
      drillDepth: drill ? drill.path.length : 0,
    });
    if (!ctrl) ctrl = FSNE.refChart.create(host, DATASETS);
    const visible = ctrl.update({ lens: lens.key, includeOver10: host.includeOver10, spacing: state.gap });
    if (!visible.nodes.length || !visible.links.length) {
      current = null;
      setStatus('<div class="evo-state"><h3>No matching stock in this view</h3><p>Show &gt;10M stock or change the filters.</p></div>');
      syncSelection();
      return;
    }
    if (drill && payload.selection) {
      const selected = payload.selection;
      const description = `${drill.trail.join(' → ')} · ${selected.month} · ${formatAmount(selected.value, host.unit.div)} ${host.unit.label} ${selected.measure}`;
      $('#evDrillScope').textContent = description;
      $('#evDrillScope').title = description;
    }
    drawNote(payload);
    syncSelection();
    return entry;
  }

  function drawNote(payload) {
    const lens = activeLens();
    const unit = host.unit.label;
    const parts = [`Values in ${unit}`];
    if (state.asOf) parts.push(`snapshots through ${state.asOf}`);
    Object.entries(filters).forEach(([key, values]) => { if (values.length && (!drill || key !== 'rsl')) parts.push(`${key}: ${values.join(', ')}`); });
    if (drill && filters.rsl.length) parts.push(`selected in RSL ${filters.rsl.join(', ')}`);
    const last = payload.excluded[payload.excluded.length - 1] || {};
    const shown = v => formatAmount(v, host.unit.div);
    if (drill) parts.push('Selected stock only; later increases excluded');
    else if (lens.id === 'cohort') parts.push(`outside the cohort ${shown(last.outside || 0)}`);
    else if (lens.dimension === 'mitigation_category') parts.push(`left out: good stock ${shown(last.good || 0)} · unclassified ${shown(last.unclassified || 0)}`);
    if (!drill && lens.id === 'rsl' && state.hide10) parts.push('>10M hidden, shown only where it moved into a lower bucket');
    if (payload.sales_unavailable_months.length) parts.push(`no secondary sales for exits after ${payload.sales_unavailable_months.join(', ')}`);
    $('#evNote').textContent = parts.join(' · ');
  }

  function formatAmount(raw, divisor) {
    const value = raw / divisor;
    const small = value > 0 && value < 0.01;
    return value.toLocaleString('en-US', { minimumFractionDigits: small ? 0 : 2, maximumFractionDigits: small ? 8 : 2 });
  }

  function syncSelection() {
    const bar = $('#evSel');
    const info = ctrl && ctrl.current ? ctrl.current.describe() : null;
    if (!info) { bar.hidden = true; bar.html = ''; return; }
    const unit = host.unit.label;
    const value = formatAmount(info.value, host.unit.div);
    bar.hidden = false;
    bar.html = `<span class="evo-sw" style="background:${escapeHtml(info.color || '#64748b')}"></span><b class="stitle">${escapeHtml(info.label)}</b>` +
      `<span class="sv">${value} ${unit} · ${(info.share * 100).toFixed(1)}% of selection</span><span class="meta">${info.lineage.length.toLocaleString('en-US')} batch lineages</span>` +
      '<span class="sp"></span><button class="btn" id="evClear" type="button">Clear <kbd>Esc</kbd></button>';
    $('#evClear').onclick = () => { ctrl.clear(); syncSelection(); };
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function mount() {
    if (mounted) return;
    mounted = true;
    mountFilters();
    $('#evLens').html = LENSES.map((l) => `<button type="button" data-lens="${l.id}" aria-pressed="false" title="${escapeHtml(l.label)}">${escapeHtml(l.short)}</button>`).join('');
    $('#evBack').onclick = backToParent;
    $('#evGap').min = GAP.min; $('#evGap').max = GAP.max;
    $('#evLens').addEventListener('click', (e) => { const b = e.target.closest('button[data-lens]'); if (b && b.dataset.lens !== state.lens) { state.lens = b.dataset.lens; clearLock(); draw(); } });
    $('#evTen').addEventListener('click', () => { state.hide10 = !state.hide10; clearLock(); draw(); });
    $('#evGap').addEventListener('input', (e) => setGap(e.target.value));
    $('#evGapReset').addEventListener('click', () => setGap(GAP.start));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !$('#r-evolution').hidden && ctrl && ctrl.current && ctrl.current.describe()) { ctrl.clear(); syncSelection(); }
    });
    observer = new ResizeObserver(() => {
      const width = $('#evScroll').clientWidth;
      if (!width || Math.abs(width - lastWidth) < 3) return;
      lastWidth = width;
      if (ctrl && current && !$('#r-evolution').hidden) { host.layoutHeight = refHeight(); keepingLock(() => ctrl.render()); }
    });
    observer.observe($('#evScroll'));
    Object.assign(host, { svg: $('#evSvg'), tooltip: $('#evTip'), menu: $('#evMenu'), legend: $('#evLegend'), scroller: $('#evScroll') });
  }

  function setGap(value) {
    state.gap = clampGap(value);
    syncToolbar();
    if (!ctrl || !current) return;
    host.spacing = state.gap;
    keepingLock(() => ctrl.update({ lens: activeLens().key, includeOver10: Boolean(drill) || !state.hide10, spacing: state.gap }));
  }

  /* a re-layout rebuilds the SVG, so remember the locked node or link and lock it again afterwards */
  async function keepingLock(redraw) {
    const info = ctrl.current && ctrl.current.describe();
    const id = (info && info.id) || pendingLockId;
    pendingLockId = id || null;
    const mySeq = ++lockSeq;
    redraw();
    syncSelection();
    if (!id || !ctrl.current || !ctrl.current.hasId(id)) { pendingLockId = null; return; }
    try { await ctrl.lockById(id); } finally { if (mySeq === lockSeq) pendingLockId = null; }
    if (mySeq === lockSeq) syncSelection();
  }

  function setUnit(unit) {
    if (unit === state.unit) return;
    state.unit = unit;
    if (mounted && env) { clearLock(); draw(); }
  }

  function clearLock() {
    pendingLockId = null;
    if (ctrl) ctrl.clear();
    syncSelection();
  }

  function exportCsv() {
    if (!ctrl || !current) return false;
    const data = ctrl.data();
    const labels = new Map(data.nodes.map((n) => [n.id, n.label]));
    const digits = state.unit === 'kl' ? 6 : 9;
    const quote = (v) => (/[",\n]/.test(v) ? '"' + String(v).replace(/"/g, '""') + '"' : v);
    const months = new Map(data.nodes.map((n) => [n.id, n.month || '']));
    const header = ['lens', 'unit', ...Object.keys(filters).map(key => key + '_filter'), 'as_of', 'selection_id', 'selection_month', 'selection_path', 'month', 'source_id', 'source', 'target_id', 'target', state.unit === 'kl' ? 'amount_kl' : state.reportUnits ? 'amount_m_bdt' : 'amount_cr_bdt'];
    const lines = [header.join(',')].concat(data.links.map((l) => [
      activeLens().label, state.unit, ...Object.entries(filters).map(([key, values]) => drill && key === 'rsl' ? 'All (selection anchored in ' + (values.join('; ') || 'All') + ')' : values.join('; ') || 'All'), state.asOf || '', current.payload.selection?.id || '', current.payload.selection?.date || '', drill ? JSON.stringify(drill.path) : '', months.get(l.source) || months.get(l.target), l.source, labels.get(l.source), l.target, labels.get(l.target), (l.value / host.unit.div).toFixed(digits),
    ].map(quote).join(',')));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n') + '\n'], { type: 'text/csv' }));
    a.download = `inventory-evolution-${state.lens}${drill ? '-selected-' + (drill.dimension === 'rsl_group' ? 'rsl' : 'category') : ''}-${state.unit}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    return data.links.length;
  }

  async function openSelection(datum) {
    if (!current || (drill && drill.path.length >= 32)) return;
    const rsl = activeLens().dimension === 'rsl_group';
    let step;
    if (datum.kind === 'rsl_downgrade') {
      const [, source, target] = datum.id.split(':');
      step = { source: `category:${source}:rsl:>10M`, target: `month:${target}` };
    } else if (datum.source && datum.target) {
      const edge = datum.raw_edges?.[0];
      step = { source: edge?.source || datum.source.id, target: edge?.target || datum.target.id };
    } else {
      if (datum.kind !== 'category') return;
      step = { node: datum.id };
    }
    if (!drill && rsl && state.hide10) step.hide10 = true;
    const label = datum.source ? `${datum.source.label} → ${datum.target.label}` : datum.label;
    const selection = {
      id: datum.id, label,
      dimension: rsl ? 'mitigation_category' : 'rsl_group',
      path: [...(drill?.path || []), step],
      trail: [...(drill?.trail || []), `${datum.month || datum.source?.month || ''} ${label}`.trim()],
      parent: { scope: captureScope(), id: datum.id, x: $('#evScroll').scrollLeft, y: $('#evScroll').scrollTop },
    };
    drill = selection;
    clearLock();
    $('#evMenu').classList.remove('open');
    await draw();
    if (drill === selection) { $('#evScroll').scrollLeft = 0; $('#evScroll').scrollTop = 0; $('#evBack').focus({ preventScroll: true }); }
  }

  async function backToParent() {
    if (!drill) return;
    const parent = drill.parent;
    restoreScope(parent.scope);
    const restoredDrill = drill;
    const entry = await draw();
    if (!entry || current !== entry || drill !== restoredDrill) return;
    const backSeq = seq;
    try { await ctrl.lockById(parent.id); } catch { /* the source graph remains usable if details fail */ }
    if (backSeq !== seq || current !== entry || drill !== restoredDrill) return;
    syncSelection();
    $('#evTip').style.opacity = 0;
    $('#evScroll').scrollLeft = parent.x;
    $('#evScroll').scrollTop = parent.y;
    [...$('#evSvg').querySelectorAll('[data-id]')].find(node => node.dataset.id === parent.id)?.focus({ preventScroll: true });
  }

  function captureScope() {
    return { ...state, drill: structuredClone(drill), bounds: structuredClone(reportScope), filters: Object.fromEntries(Object.entries(filters).map(([key, values]) => [key, [...values]])) };
  }

  function restoreScope(scope) {
    ++seq;
    drill = structuredClone(scope.drill || null);
    clearLock();
    ['lens', 'unit', 'hide10', 'gap', 'asOf', 'reportUnits'].forEach(key => { state[key] = scope[key]; });
    reportScope = structuredClone(scope.bounds || {});
    Object.keys(filters).forEach(key => { filters[key] = [...(scope.filters[key] || [])]; });
    loadFilters();
    $('#evTip').style.opacity = 0;
    $('#evMenu').classList.remove('open');
    document.querySelectorAll('#evFilters details').forEach(dropdown => { dropdown.open = false; });
    updateFilterLabels();
    syncToolbar();
  }

  FSNE.evolution = {
    open(options) {
      env = options;
      mount();
      if (options.scope) restoreScope({ ...state, hide10: false, ...options.scope, bounds: options.scope.filters || {} });
      else loadFilters();
      state.unit = options.unit || state.unit;
      updateFilterLabels();
      $('#evTip').style.opacity = 0;
      return draw();
    },
    captureScope,
    restoreScope,
    setUnit,
    exportCsv,
  };
})(typeof window !== 'undefined' ? window : globalThis);
