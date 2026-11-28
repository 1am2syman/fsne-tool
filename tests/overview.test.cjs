const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('ui/template.html', 'utf8');
const source = template.slice(template.indexOf('const OVERVIEW_RSL='), template.indexOf('// Month buttons are rebuilt'));
const groups = ['> 10M', '6M - 10M', '3M - 6M', '< 3M'];

function makeContext({labels = ['Jul 2026', 'Aug 2026'], unit = 'bdt'} = {}) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => '&#' + ch.charCodeAt(0) + ';');
  const context = vm.createContext({
    STATUS: {groups, labels},
    unitDivisor: () => unit === 'bdt' ? 1e6 : 1,
    unitLabel: () => unit === 'bdt' ? 'M BDT' : 'KL',
    esc,
  });
  vm.runInContext(source, context);
  return context;
}

function parseDrills(html) {
  const unesc = s => s.replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n));
  return [...html.matchAll(/data-overview-drill="([^"]+)"/g)].map(m => JSON.parse(unesc(m[1])));
}

function cell(stock, exp, lnd) {
  return [[stock, exp, lnd], [0, 0, 0], [0, 0, 0], [0, 0, 0]];
}

function makeTree(towns) {
  const sumMonths = (nodes, len) => Array.from({length: len}, (_, i) => {
    let any = false;
    const acc = groups.map(() => [0, 0, 0]);
    for (const node of nodes) {
      if (!node.m[i]) continue;
      any = true;
      node.m[i].forEach((c, g) => {
        acc[g][0] += c[0];
        acc[g][1] += c[1];
        acc[g][2] += c[2];
      });
    }
    return any ? acc : null;
  });
  const monthLen = towns[0]?.brands[0]?.m.length || 0;
  const kids = towns.map((t, ti) => {
    const bKids = t.brands.map((b, bi) => ({name: b.name, id: `b${ti}_${bi}`, m: b.m, kids: []}));
    return {name: t.name, id: `t${ti}`, m: sumMonths(bKids, monthLen), kids: bKids};
  });
  return {name: 'All towns', id: 'total', m: sumMonths(kids, monthLen), kids};
}

test('hero table percentages match Exposure and LND FC shares of group stock', () => {
  const ctx = makeContext({labels: ['Oct 2026']});
  const cells = [[1253e6, 37e6, 19.2e6], [19.5e6, 19.5e6, 3.26e6], [5.89e6, 5.89e6, 2.84e6], [1.77e6, 1.77e6, 1.77e6]];
  const hero = ctx.overviewHero(ctx.overviewFacts([cells], groups), 0, null, 0, () => '');
  const rows = [...hero.matchAll(/<div class="ov-m"[^>]*>([\s\S]*?)(?=<div class="ov-m|$)/g)]
    .slice(0, 4)
    .map(row => [...row[1].matchAll(/<em>(.*?)<\/em>/g)].slice(0, 3).map(match => match[1]));
  assert.deepEqual(rows[0], ['98%', '2.95%', '1.53%']);
  assert.deepEqual(rows[3], ['0.14%', '100%', '100%']);
});

test('Top Brand Risk Drivers ranks by LND, computes concentration, bar widths, pills and deltas', () => {
  const ctx = makeContext();
  const tree = makeTree([
    {
      name: 'Dhaka',
      brands: [
        {name: 'PA-BDYLOT', m: [cell(20e6, 3e6, 0.8e6), cell(20e6, 3e6, 0.85e6)]},
        {name: 'BPCNO-LP', m: [cell(500e6, 4e6, 0.67e6), cell(500e6, 6.17e6, 1.00e6)]},
        {name: 'JFB_MGBWS', m: [cell(4e6, 1.8e6, 1.02e6), cell(4.2e6, 1.67e6, 0.91e6)]},
        {name: 'SAF_HONEY', m: [cell(9e6, 5e6, 0.48e6), cell(9e6, 6.20e6, 0.63e6)]},
        {name: 'TINY_ZERO', m: [cell(10e6, 0, 0), cell(10e6, 0, 0)]},
      ],
    },
    {
      name: 'Chattogram',
      brands: [
        {name: 'PA-BDYLOT', m: [cell(8.5e6, 1.27e6, 0.46e6), cell(8.5e6, 1.27e6, 0.48e6)]},
        {name: 'BPCNO-LP', m: [cell(10e6, 0, 0), cell(10e6, 0, 0)]},
        {name: 'DROP_BRAND', m: [cell(5e6, 2e6, 0.90e6), null]},
      ],
    },
  ]);

  const data = ctx.overviewBrandDrivers(tree, 1, 0, 'largest');
  assert.deepEqual([...data.rows.map(r => r.brand)], ['PA-BDYLOT', 'BPCNO-LP', 'JFB_MGBWS', 'SAF_HONEY', 'TINY_ZERO']);
  assert.equal(data.rows[0].towns, 2); // LND > 0 in both Dhaka and Chattogram
  assert.equal(data.rows[1].towns, 1); // LND > 0 only in Dhaka (Chattogram has LND = 0)
  assert.equal(data.brandCount, 5);
  assert.equal(data.townCount, 2);
  assert.ok(Math.abs(data.topBrandShare - 1) < 1e-10);
  assert.ok(Math.abs(data.topTownShare - 1) < 1e-10);

  const html = ctx.overviewBrandDriversCard(tree, 1, 0, 'largest');
  assert.match(html, /Top Brand Risk Drivers/);
  assert.match(html, /Top 5\/5 brands = <b>100\.0%<\/b> of LND/);
  assert.match(html, /Top 2\/2 towns = <b>100\.0%<\/b>/);
  assert.match(html, /vs Jul/);
  assert.match(html, /Exp 4\.27.*2 towns/);
  assert.match(html, /Exp 6\.17.*1 town/);
  assert.match(html, /class="ov-bd-pill mid">15%<\/span>/);
  assert.match(html, /class="ov-bd-pill low">1\.2%<\/span>/);
  assert.match(html, /class="ov-bd-pill high">40%<\/span>/);
  assert.match(html, /class="ov-bd-pill high">69%<\/span>/);
  assert.match(html, /class="ov-bd-delta bad">\+0\.07<\/span>/);
  assert.match(html, /class="ov-bd-delta good">−0\.11<\/span>/);
  // Bar widths scale against the highest exposure/LND across displayed rows (SAF_HONEY exp 6.20M).
  const widths = [...html.matchAll(/class="(exp|lnd)" style="width:([\d.]+)%"/g)].map(m => [m[1], Number(m[2])]);
  assert.ok(Math.abs(widths[0][1] - (4.27 / 6.20) * 100) < 1e-9);
  assert.ok(Math.abs(widths[1][1] - (1.33 / 6.20) * 100) < 1e-9);
  assert.equal(widths[6][1], 100);
});

test('Movers sorts by absolute LND change and includes brands that fell to zero', () => {
  const ctx = makeContext();
  const tree = makeTree([
    {
      name: 'Dhaka',
      brands: [
        {name: 'RISER', m: [cell(10e6, 1e6, 0.30e6), cell(10e6, 2e6, 0.76e6)]},     // +0.46
        {name: 'STEADY', m: [cell(10e6, 2e6, 1.20e6), cell(10e6, 2e6, 1.22e6)]},    // +0.02
        {name: 'FALLER', m: [cell(10e6, 2e6, 0.90e6), null]},                        // -0.90
        {name: 'DROPPER', m: [cell(10e6, 1e6, 0.55e6), cell(10e6, 0.4e6, 0.22e6)]}, // -0.33
      ],
    },
  ]);

  const movers = ctx.overviewBrandDrivers(tree, 1, 0, 'movers');
  assert.deepEqual([...movers.rows.map(r => r.brand)], ['FALLER', 'RISER', 'DROPPER', 'STEADY']);
  assert.equal(movers.rows[0].towns, 0);
  const html = ctx.overviewBrandDriversCard(tree, 1, 0, 'movers');
  assert.match(html, /data-overview-brand-mode="movers" aria-pressed="true"/);
  assert.match(html, /class="ov-bd-delta good">−0\.90<\/span>/);
  assert.match(html, /class="ov-bd-delta bad">\+0\.46<\/span>/);
});

test('earliest month disables Movers, falls back to Largest, and handles KL and zero values cleanly', () => {
  const ctx = makeContext({labels: ['Feb 2026'], unit: 'kl'});
  const tree = makeTree([
    {
      name: 'Dhaka',
      brands: [
        {name: 'ZERO_STK', m: [cell(0, 0, 0)]},
        {name: 'KL_BRAND', m: [cell(200, 20, 5)]},
      ],
    },
  ]);

  const html = ctx.overviewBrandDriversCard(tree, 0, null, 'movers');
  assert.match(html, /data-overview-brand-mode="largest" aria-pressed="true"/);
  assert.match(html, /data-overview-brand-mode="movers" aria-pressed="false" disabled/);
  assert.match(html, /class="ov-bd-pill mid">10%<\/span>/);
  assert.match(html, /class="ov-bd-pill low">–<\/span>/);
  assert.doesNotMatch(html, /NaN|Infinity|vs Feb/);
});

test('Plan clearance card parses evolution links, computes monthly & avg rates, headline and KL scaling', () => {
  const ctx = makeContext({labels: ['Feb 2026', 'Mar 2026', 'Jul 2026', 'Aug 2026']});
  const evo = {
    months: [
      {key: 'M1', date: '2026-01-31', label: 'Feb'},
      {key: 'M2', date: '2026-02-28', label: 'Mar'},
      {key: 'M3', date: '2026-03-31', label: 'Apr'},
      {key: 'M4', date: '2026-07-31', label: 'Aug'},
      {key: 'M5', date: '2026-08-30', label: 'Sep'},
    ],
    links: [
      // M2 -> M3 (Feb plans observed in Mar): sales_push 25%, rotation 15%
      {source: 'category:M2:sales_push', target: 'outcome:M2:resolved', value: 5e6},
      {source: 'category:M2:sales_push', target: 'month:M3', value: 15e6},
      {source: 'category:M2:rotation', target: 'outcome:M2:resolved', value: 1.5e6},
      {source: 'category:M2:rotation', target: 'month:M3', value: 8.5e6},
      // M4 -> M5 (Jul plans observed in Aug): sales_push 23%, rotation 12%, not_actionable 78%
      {source: 'category:M4:sales_push', target: 'outcome:M4:resolved', value: 5.4e6},
      {source: 'category:M4:sales_push', target: 'month:M5', value: 18.1e6},
      {source: 'category:M4:rotation', target: 'outcome:M4:resolved', value: 0.96e6},
      {source: 'category:M4:rotation', target: 'month:M5', value: 7.08e6},
      {source: 'category:M4:not_actionable', target: 'outcome:M4:resolved', value: 0.99e6},
      {source: 'category:M4:not_actionable', target: 'month:M5', value: 0.28e6},
    ],
  };
  const parsed = ctx.parseOverviewPlans(evo, 'bdt');
  const html = ctx.overviewPlansCard({state: 'ready', data: parsed}, 3, 2, 'exposure');
  assert.match(html, /Did July’s plans clear it\?/);
  assert.match(html, /data-overview-plan-lens="exposure" aria-pressed="true"/);
  assert.match(html, /<b>Rotation<\/b> clears slowest: 12% this month and 14% a month since March, against 24% for Sales push\./);
  assert.match(html, /Sales push<\/span><b>23\.5<\/b>.*<b class="rt">23%<\/b><b class="av">24%<\/b>/);
  assert.match(html, /Rotation<\/span><b>8\.04<\/b>.*<b class="rt">12%<\/b><b class="av">14%<\/b>/);
  assert.match(html, /Not actionable<\/span><b>1\.27<\/b>.*<b class="rt">78%<\/b><b class="av">78%<\/b>/);
  assert.match(html, /data-overview-go="evolution">inventory-evolution ›<\/button>/);

  // Feb 2026 has M1 (January) as previous snapshot, which has no category links.
  const febHtml = ctx.overviewPlansCard({state: 'ready', data: parsed}, 0, null, 'exposure');
  assert.match(febHtml, /Did January’s plans clear it\?/);
  assert.match(febHtml, /January’s saved report has no action plans, so plan outcomes start in March\./);

  // KL unit divides evolution litres by 1000.
  const klCtx = makeContext({labels: ['Jul 2026', 'Aug 2026'], unit: 'kl'});
  const klParsed = klCtx.parseOverviewPlans({
    months: [{key: 'M1', date: '2026-07-31'}, {key: 'M2', date: '2026-08-30'}],
    links: [
      {source: 'category:M1:sales_push', target: 'outcome:M1:resolved', value: 2500},
      {source: 'category:M1:sales_push', target: 'month:M2', value: 7500},
    ],
  }, 'kl');
  const klHtml = klCtx.overviewPlansCard({state: 'ready', data: klParsed}, 1, 0, 'lnd');
  assert.match(klHtml, /Sales push<\/span><b>10\.0<\/b>.*<b class="rt">25%<\/b>/);
  assert.match(klHtml, /LND FC/);
  assert.match(klHtml, /\(KL\)/);
});

test('Overview visuals emit exact drilldown specs across hero cells, RSL cards, brand/town pills, trends, and plans', () => {
  const ctx = makeContext({labels: ['Jul 2026', 'Aug 2026']});
  const cells = [[1253e6, 37e6, 19.2e6], [19.5e6, 19.5e6, 3.26e6], [5.89e6, 5.89e6, 2.84e6], [1.77e6, 1.77e6, 1.77e6]];
  const facts = ctx.overviewFacts([cells, cells], groups);

  // Hero: headline + 4 RSL rows (label + 3 metrics each) + Total row (label + 3 metrics) = 1 + 5*4 = 21 drill targets
  const hero = ctx.overviewHero(facts, 1, 0, 0, () => '');
  const heroDrills = parseDrills(hero);
  assert.equal(heroDrills.length, 21);
  assert.deepEqual(heroDrills[0], {kind: 'status', metric: 0, title: 'National stock'});
  assert.deepEqual(heroDrills[2], {kind: 'status', metric: 0, rsl: ['> 10M'], title: '> 10M · Stock'});
  assert.deepEqual(heroDrills[3], {kind: 'status', metric: 1, rsl: ['> 10M'], title: '> 10M · Exposure'});
  assert.deepEqual(heroDrills[4], {kind: 'status', metric: 2, rsl: ['> 10M'], title: '> 10M · LND FC'});
  assert.deepEqual(heroDrills.at(-1), {kind: 'status', metric: 2, title: 'National · LND FC'});

  // RSL card: semantic header button (no role="button" on outer section) + accessible SVG group with per-month hit zones
  const rslPlots = {};
  const rslHtml = ctx.overviewRslCard(facts, 1, 0, '3M - 6M', (k, draw) => { rslPlots[k] = draw; return ''; });
  assert.doesNotMatch(rslHtml, /<section[^>]*role="button"/);
  assert.match(rslHtml, /<button type="button" class="ov-rc-head"/);
  assert.deepEqual(parseDrills(rslHtml), [{kind: 'status', metric: 0, rsl: ['3M - 6M'], title: '3M - 6M · Stock'}]);
  const rslSvg = rslPlots.rsl2(260, 120);
  assert.match(rslSvg, /<svg[^>]*role="group" aria-label="3M - 6M stock by month in M BDT"/);
  assert.doesNotMatch(rslSvg, /aria-hidden="true"/);
  assert.deepEqual(parseDrills(rslSvg), [
    {kind: 'status', metric: 0, rsl: ['3M - 6M'], month: 0, title: '3M - 6M · Stock'},
    {kind: 'status', metric: 0, rsl: ['3M - 6M'], month: 1, title: '3M - 6M · Stock'},
  ]);

  // Trends: semantic heading button (no nested buttons on outer section) + accessible SVG group with per-month hit zones
  const trendPlots = {};
  const trendHtml = ctx.overviewTrends(facts, 1, (k, draw) => { trendPlots[k] = draw; return ''; });
  assert.doesNotMatch(trendHtml, /<section[^>]*role="button"/);
  assert.match(trendHtml, /<button type="button" class="ov-trend-btn"/);
  assert.deepEqual(parseDrills(trendHtml), [
    {kind: 'status', metric: 1, title: 'Exposure · all RSL'},
    {kind: 'status', metric: 1, title: 'Exposure · all RSL'},
    {kind: 'status', metric: 1, rsl: ['> 10M'], title: 'Exposure · > 10M'},
    {kind: 'status', metric: 2, title: 'LND FC · all RSL'},
  ]);
  const expSvg = trendPlots.trendExp(240, 110);
  assert.match(expSvg, /<svg[^>]*role="group" aria-label="Exposure · all RSL by month in M BDT"/);
  assert.doesNotMatch(expSvg, /aria-hidden="true"/);
  assert.deepEqual(parseDrills(expSvg), [
    {kind: 'status', metric: 1, title: 'Exposure · all RSL', month: 0},
    {kind: 'status', metric: 1, title: 'Exposure · all RSL', month: 1},
  ]);
  assert.deepEqual(parseDrills(trendPlots.trendLnd(240, 110)), [
    {kind: 'status', metric: 2, title: 'LND FC · all RSL', month: 0},
    {kind: 'status', metric: 2, title: 'LND FC · all RSL', month: 1},
  ]);

  // Brand Drivers: summary pills (top brands + top towns) and brand rows open in Exposure mode (metric 1, color-coded)
  const tree = makeTree([
    {name: 'Dhaka', brands: [{name: 'ALPHA', m: [cell(10e6, 2e6, 1e6), cell(10e6, 2e6, 1.5e6)]}]},
    {name: 'Chattogram', brands: [{name: 'BETA', m: [cell(8e6, 1e6, 0.5e6), cell(8e6, 1e6, 0.4e6)]}]},
  ]);
  const bdDrills = parseDrills(ctx.overviewBrandDriversCard(tree, 1, 0, 'largest'));
  assert.deepEqual(bdDrills, [
    {kind: 'status', metric: 1, brands: ['ALPHA', 'BETA'], title: 'Top 2 brands by LND FC'},
    {kind: 'status', metric: 1, towns: ['Dhaka', 'Chattogram'], order: 'town-first', title: 'Top 2 towns by LND FC'},
    {kind: 'status', metric: 1, brands: ['ALPHA'], expandFirst: true, title: 'ALPHA'},
    {kind: 'status', metric: 1, brands: ['BETA'], expandFirst: true, title: 'BETA'},
  ]);
});

test('Plan clearance rows emit scoped inventory-evolution Sankey descriptors using Sankey month labels', () => {
  const ctx = makeContext({labels: ['Jul 2026', 'Aug 2026'], unit: 'bdt'});
  const evo = {
    months: [{key: 'M1', date: '2026-07-31', label: 'Aug'}, {key: 'M2', date: '2026-08-30', label: 'Sep'}],
    links: [
      {id: 'L1', source: 'category:M1:sales_push', target: 'outcome:M1:resolved', value: 2e6},
      {id: 'L2', source: 'category:M1:sales_push', target: 'month:M2', value: 6e6},
      {id: 'L3', source: 'category:M1:rotation', target: 'month:M2', value: 3e6},
    ],
  };
  const parsed = ctx.parseOverviewPlans(evo, 'bdt');
  const html = ctx.overviewPlansCard({state: 'ready', data: parsed}, 1, 0, 'exposure');
  assert.deepEqual(parseDrills(html), [
    {kind: 'plan', lens: 'exposure', cat: 'sales_push', node: 'category:M1:sales_push', name: 'Sales push', prevLabel: 'Aug', prevLong: 'July'},
    {kind: 'plan', lens: 'exposure', cat: 'rotation', node: 'category:M1:rotation', name: 'Rotation', prevLabel: 'Aug', prevLong: 'July'},
  ]);
});

test('Scope dialog lifecycle: pre-filled filters, undo isolation, close-before-focus, detached-opener recovery, unit sync, and plan evolution scope', () => {
  const FSNEFilters = require('../ui/report_filters.js');
  const events = [];
  const fakeEl = (id = '', extra = {}) => {
    const el = {
      id, textContent: '', value: '', hidden: false, open: false, innerHTML: '', isConnected: true,
      dataset: {}, classList: {add() {}, remove() {}, toggle() {}},
      attrs: {}, listeners: {}, children: [],
      setAttribute(k, v) { this.attrs[k] = String(v); },
      getAttribute(k) { return this.attrs[k] ?? null; },
      before(node) { this._before = node; },
      append(...nodes) { this.children.push(...nodes); },
      replaceChildren(...nodes) { this.children = nodes; },
      focus() { events.push(`focus:${this.id || this.attrs['data-overview-drill'] || 'anon'}:${dialog.open ? 'open' : 'closed'}`); },
      addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
      closest() { return this; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      ...extra,
    };
    Object.defineProperty(el, 'html', {
      get() { return this.innerHTML; },
      set(v) { this.innerHTML = v; },
    });
    return el;
  };
  const dialog = fakeEl('scopeDialog', {
    showModal() { this.open = true; events.push('dialog:showModal'); },
    close() { this.open = false; events.push('dialog:close'); },
  });
  const sOrder = fakeEl('sOrder', {value: 'town', innerHTML: '<option value="town">Brand → Town → Parent</option>'});
  const sMetric = fakeEl('sMetric', {value: '0'});
  const sSort = fakeEl('sSort', {value: 'total:-1'});
  const sScopeNote = fakeEl('sScopeNote', {hidden: true});
  const rStatus = fakeEl('r-status', {hidden: true});
  const unitButtons = [
    fakeEl('unit-bdt', {dataset: {unit: 'bdt'}}),
    fakeEl('unit-kl', {dataset: {unit: 'kl'}}),
  ];
  const reportUnitButtons = [
    fakeEl('rep-unit-bdt', {dataset: {unit: 'bdt'}}),
    fakeEl('rep-unit-kl', {dataset: {unit: 'kl'}}),
  ];
  const unitSeg = fakeEl('scopeDialogUnit', {hidden: true});
  const closeBtn = fakeEl('scopeDialogClose');
  const titleEl = fakeEl('scopeDialogTitle');
  const scopeEl = fakeEl('scopeDialogScope');
  const bodyEl = fakeEl('scopeDialogBody');
  const mvEl = fakeEl('mv', {hidden: true});
  const dohEl = fakeEl('doh', {hidden: true});
  const helpEl = fakeEl('help', {hidden: true});
  const evGapEl = fakeEl('evGap', {value: '300'});
  const evoTitleEl = fakeEl('evoDialogTitle');
  const evoScopeEl = fakeEl('evoDialogScope');
  const reRenderedOpener = fakeEl('reRenderedOpener');
  const rOverview = fakeEl('r-overview', {
    querySelector(sel) {
      return sel.includes('ALPHA') ? reRenderedOpener : null;
    },
  });
  const bySel = {
    '#scopeDialog': dialog, '#r-status': rStatus, '#sOrder': sOrder, '#sMetric': sMetric, '#sSort': sSort,
    '#sScopeNote': sScopeNote, '#scopeDialogUnit': unitSeg, '#scopeDialogClose': closeBtn,
    '#scopeDialogTitle': titleEl, '#scopeDialogScope': scopeEl, '#scopeDialogBody': bodyEl,
    '#mv': mvEl, '#doh': dohEl, '#help': helpEl, '#evGap': evGapEl, '#r-overview': rOverview,
    '#evoDialogTitle': evoTitleEl, '#evoDialogScope': evoScopeEl,
    '#sSort option[value="name:1"]': fakeEl(),
    '#sT': fakeEl('sT', {closest: () => ({querySelector: () => ({querySelector: () => fakeEl()})})}),
  };

  const facetRows = [
    {town: 'Dhaka', brand: 'ALPHA', parent: '10', pdesc: 'SKU 10', category: 'Actionable', m: [cell(10e6, 2e6, 1e6), cell(10e6, 2e6, 1.5e6)], m_kl: [cell(10, 2, 1), cell(10, 2, 1.5)]},
    {town: 'Chattogram', brand: 'BETA', parent: '20', pdesc: 'SKU 20', category: 'Actionable', m: [cell(8e6, 1e6, 0.5e6), cell(8e6, 1e6, 0.4e6)], m_kl: [cell(8, 1, 0.5), cell(8, 1, 0.4)]},
  ];
  const filterContainer = fakeEl('sFilters');
  const docStub = {
    createElement: tag => fakeEl(tag),
    createComment: () => ({replaceWith() {}}),
    createTextNode: t => ({textContent: t}),
    addEventListener() {},
  };
  let drawCount = 0, evoDialogCall = null, overviewRedraws = 0;
  const ctx = vm.createContext({
    document: docStub,
    CSS: {escape: s => s.replace(/["\\]/g, '\\$&')},
    STATUS: {labels: ['Jul 2026', 'Aug 2026'], groups, facets: facetRows, total: {m: [cell(18e6, 3e6, 1.5e6), cell(18e6, 3e6, 1.9e6)], kids: []}, total_kl: {m: [cell(18, 3, 1.5), cell(18, 3, 1.9)], kids: []}},
    OVERVIEW_PLAN_MEAS: {exposure: 'Exposure', lnd: 'LND FC'},
    overviewMonth: 1, overviewPlanLens: 'exposure',
    reportUnit: 'bdt', curRep: 'overview', minStock: 0, sel: null, B: [], SHEETS: {},
    statusMetricNames: ['stock-bdt', 'exposure-bdt', 'lnd-bdt'],
    statusView: 'brand-town', statusMetric: 0, statusSort: {k: 'total', d: -1}, statusOpen: new Set(),
    statusRoot: {kids: []}, statusStates: new Map(), statusRslSelections: new Map(),
    statusScope: null, statusSnap: null, statusGroup: null, statusCategoryScope: null,
    statusRslFilter: {refresh() {}},
    normalizeEvolutionRsl: g => g.replace(/\s/g, '').replaceAll('-', '–'),
    unitLabel: () => ctx.reportUnit === 'bdt' ? 'M BDT' : 'KL',
    $: sel => bySel[sel] || fakeEl(sel),
    $$: (sel, root) => {
      if (root === unitSeg && sel === 'button') return unitButtons;
      if (sel === '#scopeDialogUnit button') return unitButtons;
      if (sel === '#reportUnit button,#mvUnit button,#scopeDialogUnit button') return [...reportUnitButtons, ...unitButtons];
      if (sel === '#mSize button') return [fakeEl(), fakeEl('size1', {dataset: {s: '500000'}})];
      return [];
    },
    sum: a => a.reduce((s, v) => s + v, 0),
    toast() {}, buildData() {}, drawB() {}, drawNote() {}, drawScale() {}, drawM() {}, drawTransfer() {}, drawRescue() {}, drawNotActionables() {},
    drawOverview() { overviewRedraws++; },
    closeDohExplanation() { dohEl.hidden = true; },
    openEvolutionDialog(scope, opts) { evoDialogCall = {scope, opts}; },
    window: {},
  });
  // Create a real FSNEFilters.Group backed by our stub document.
  const prevDoc = global.document;
  global.document = docStub;
  try {
    ctx.statusFilters = new FSNEFilters.Group({
      container: filterContainer,
      dimensions: [...FSNEFilters.dimensions, {key: 'category', label: 'Stock category'}],
      rows: () => facetRows,
      onChange: () => {},
    });
  } finally {
    global.document = prevDoc;
  }
  ctx.statusFilters.states.set('brand', {category: new Set(['Actionable'])});
  ctx.statusFilters.use('brand');

  // Load real helper slices from ui/template.html.
  const focusStart = template.indexOf('function captureOverviewReturn(');
  const focusSlice = template.slice(focusStart, template.indexOf('function openEvolutionDialog(', focusStart));
  const statusStart = template.indexOf('const statusLeafKey=');
  const statusSlice = template.slice(statusStart, template.indexOf('function statusEvolutionFilters(', statusStart));
  const drillStart = template.indexOf('function buildOverviewPlanScope(');
  const drillSlice = template.slice(drillStart, template.indexOf("document.addEventListener('click',e=>{", drillStart));
  const scopeStart = template.indexOf('const SCOPE_MODALS=');
  const scopeSlice = template.slice(scopeStart, template.indexOf("$$('#reportUnit button').forEach", scopeStart));
  ctx.drawStatus = () => { drawCount++; ctx.statusRoot = ctx.filteredStatusTree(); };
  vm.runInContext(focusSlice, ctx);
  vm.runInContext(statusSlice, ctx);
  vm.runInContext(drillSlice, ctx);
  vm.runInContext(scopeSlice, ctx);

  // 1. Open ALPHA brand drilldown (auto-expanded, metric 1, plain title, Brand -> Town -> Parent)
  const alphaSpec = {kind: 'status', metric: 1, brands: ['ALPHA'], expandFirst: true, title: 'ALPHA'};
  const opener = fakeEl('alphaRow', {attrs: {'data-overview-drill': JSON.stringify(alphaSpec)}});
  ctx.openOverviewDrill(alphaSpec, opener);
  assert.equal(dialog.open, true);
  assert.equal(titleEl.textContent, 'ALPHA');
  assert.equal(ctx.statusView, 'brand-town');
  assert.equal(ctx.statusMetric, 1);
  assert.equal(ctx.statusSnap, 1);
  assert.deepEqual(ctx.statusFilters.selection(), {brand: ['ALPHA']});
  assert.equal(ctx.statusOpen.size, 1); // single brand row auto-expanded
  assert.equal(unitSeg.hidden, false);

  // 2. Toggle KL inside dialog: syncs page + dialog, redraws Overview (detaching opener), stays open
  opener.isConnected = false; // simulates drawOverview rebuilding DOM
  unitButtons[1].onclick();
  assert.equal(ctx.reportUnit, 'kl');
  assert.equal(reportUnitButtons[1].attrs['aria-pressed'], 'true');
  assert.equal(unitButtons[1].attrs['aria-pressed'], 'true');
  assert.equal(dialog.open, true);
  assert.equal(ctx.statusView, 'brand-town');
  assert.ok(overviewRedraws >= 1);

  // 3. Close via button: dialog.close() runs BEFORE focus, and detached opener recovers via data-overview-drill
  events.length = 0;
  closeBtn.onclick();
  assert.equal(dialog.open, false);
  assert.deepEqual(events.slice(0, 2), ['dialog:close', 'focus:reRenderedOpener:closed']);
  // Standalone brand filter state restored intact
  assert.deepEqual(ctx.statusFilters.selection(), {category: ['Actionable']});
  assert.equal(ctx.statusFilters.histories.has('scoped'), false);

  // 4. Undo isolation: open Top 5 towns, edit filter, undo -> must return to Dhaka+Chattogram, NEVER ALPHA!
  ctx.openOverviewDrill({kind: 'status', metric: 1, towns: ['Dhaka', 'Chattogram'], order: 'town-first', title: 'Top 2 towns'}, fakeEl('townsPill'));
  assert.equal(ctx.statusView, 'town');
  assert.deepEqual(ctx.statusFilters.selection(), {town: ['Dhaka', 'Chattogram']});
  ctx.statusFilters.apply({town: new Set(['Dhaka'])});
  assert.deepEqual(ctx.statusFilters.selection(), {town: ['Dhaka']});
  assert.equal(ctx.statusFilters.undo(), true);
  assert.deepEqual(ctx.statusFilters.selection(), {town: ['Chattogram', 'Dhaka']});
  assert.equal(ctx.statusFilters.undo(), false); // cannot undo into earlier ALPHA session!
  closeBtn.onclick();

  // 5. triggerOverviewDrill ignores malformed JSON cleanly without swallowing runtime errors
  ctx.triggerOverviewDrill(fakeEl('bad', {attrs: {'data-overview-drill': '{not-json'}}));
  assert.equal(dialog.open, false);

  // 6. Plan evolution scope: preserves current evGap, builds parentScope for <- Back to categories, updates header on drill change
  ctx.openOverviewDrill({kind: 'plan', lens: 'exposure', cat: 'sales_push', node: 'category:M7:sales_push', name: 'Sales push', prevLabel: 'Aug', prevLong: 'July'}, fakeEl('planRow'));
  assert.equal(evoDialogCall.scope.lens, 'exposure');
  assert.equal(evoDialogCall.scope.unit, 'kl');
  assert.equal(evoDialogCall.scope.drill.dimension, 'rsl_group');
  const plain = v => JSON.parse(JSON.stringify(v));
  assert.deepEqual(plain(evoDialogCall.scope.drill.path), [{node: 'category:M7:sales_push'}]);
  assert.deepEqual(plain(evoDialogCall.scope.drill.trail), ['Aug Sales push']);
  assert.equal(evoDialogCall.scope.drill.parent.scope.gap, 300);
  assert.equal(evoDialogCall.scope.drill.parent.scope.lens, 'exposure');
  assert.equal(evoDialogCall.scope.drill.parent.scope.drill, null);
  evoDialogCall.opts.onDrillChange(null, {id: 'exposure', short: 'Exposure'});
  assert.equal(evoTitleEl.textContent, 'Inventory evolution');
  assert.equal(evoScopeEl.textContent, 'Exposure · KL');
});
