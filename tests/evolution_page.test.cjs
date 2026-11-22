const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Exercise the page's public actions with a fake network and renderer. Quantity allocation
// is covered by the Python tests; these checks cover request scope and navigation races.
function page() {
  class Element {
    constructor() {
      this.dataset = {};
      this.value = '';
      this.hidden = false;
      this.style = {};
      this.listeners = {};
      this.children = [];
      this.selectors = new Map();
      this.scrollLeft = this.scrollTop = 0;
      this.clientWidth = 1440;
      this.classList = {remove() {}, add() {}};
    }
    setAttribute(name, value) { this[name] = value; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    querySelector(selector) {
      if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
      return this.selectors.get(selector);
    }
    querySelectorAll() { return []; }
    addEventListener(event, handler) { this.listeners[event] = handler; }
    focus() { this.focused = true; }
    click() { this.clicked = true; return this.onclick?.(); }
    remove() { this.removed = true; }
    contains(target) { return this === target || this.children.some(child => child?.contains?.(target)); }
    getBoundingClientRect() { return {left: 20, bottom: 100, width: 200}; }
  }
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const document = {body: new Element(), querySelector: $, querySelectorAll: () => [], createElement: () => new Element(),
    createTextNode: text => text, addEventListener() {}};
  const requests = [];
  let host, data, locked, blob, intercept;
  function payload(url) {
    const path = JSON.parse(url.searchParams.get('path') || '[]');
    const baseRsl = url.pathname.includes('/rsl');
    const rsl = baseRsl !== Boolean(path.length % 2);
    const ids = rsl ? ['category:M2:rsl:3M–6M', 'category:M3:rsl:<3M', 'category:M1:rsl:>10M']
      : ['category:M2:sales_push', 'category:M3:rotation'];
    const nodes = ids.map(id => ({id, kind: 'category', label: id, month: 'Aug', color: '#2563eb'}));
    return {nodes, links: nodes.map(node => ({id: 'month:M2>' + node.id, source: 'month:M2', target: node.id, value: 500})), transitions: [],
      excluded: [], sales_unavailable_months: [], months: [{key: 'M2', date: '2026-07-28', label: 'Aug'}],
      unit_div: url.searchParams.get('unit') === 'kl' ? 1000 : 1e7, unit_label: url.searchParams.get('unit') === 'kl' ? 'KL' : 'Cr',
      selection: path.length ? {id: path.at(-1).node, month: 'Aug', value: 500, measure: 'Exposure'} : undefined};
  }
  const context = vm.createContext({console, document, structuredClone, URLSearchParams, innerHeight: 900, innerWidth: 1440, Blob,
    addEventListener() {},
    URL: {createObjectURL: value => { blob = value; return 'blob:export'; }, revokeObjectURL() {}},
    ResizeObserver: class { observe() {} },
    fetch: async path => {
      const url = new URL(path, 'http://local');
      requests.push(url);
      if (intercept) {
        const response = intercept(url);
        if (response) return response;
      }
      const result = url.pathname.endsWith('/filters') ? {town: [], parent: [], material: [], brand: [], rsl: [], material_labels: {}}
        : url.pathname.endsWith('/detail') ? {links: {}, transitions: {}, lineages: {brand: [], rsl: [], exp: []}, brands: []} : payload(url);
      return {ok: true, json: async () => result};
    },
    FSNE: {refChart: {create(value, datasets) {
      host = value;
      const ctrl = {
        current: {describe: () => locked ? {...locked, value: 500, share: 1, lineage: []} : null,
          hasId: id => data.nodes.some(node => node.id === id)},
        update() { data = Object.values(datasets.metrics)[0]; locked = null; return data; },
        render() {}, clear() { locked = null; }, data: () => data,
        async lockById(id) { locked = data.nodes.find(node => node.id === id); return Boolean(locked); },
      };
      return ctrl;
    }}},
  });
  const dropdowns = new Map();
  vm.runInContext(fs.readFileSync('ui/report_filters.js', 'utf8'), context);
  const SharedDropdown = context.FSNEFilters.Dropdown;
  context.FSNEFilters.Dropdown = class extends SharedDropdown {
    constructor(options) { super(options); dropdowns.set(options.key, this); }
  };
  vm.runInContext(fs.readFileSync('ui/evolution/page.js', 'utf8'), context);
  return {evolution: context.FSNE.evolution, $, requests, dropdowns,
    get host() { return host; }, get data() { return data; }, get blob() { return blob; },
    intercept(value) { intercept = value; },
    async open(lens = 'exposure') {
      await context.FSNE.evolution.open({root: $('#root'), api: path => '/api/' + path, unit: 'bdt',
        scope: {lens, asOf: '2026-08-30', reportUnits: true,
          filters: {brand: ['Brand A'], town: ['Dhaka'], parent: ['101'], material: ['1001'], rsl: ['3M–6M']}}});
    },
  };
}
const node = (id, month = 'Aug') => ({id, kind: 'category', label: id.split(':').at(-1), month});
const json = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));

test('nested drilling sends the full path and Back restores each parent selection and scroll', async () => {
  const p = page();
  await p.open();
  p.$('#evScroll').scrollLeft = 150;
  p.$('#evScroll').scrollTop = 20;
  await p.host.openSelection(node('category:M2:sales_push'));
  assert.equal(p.$('#evDrillTitle').textContent, 'RSL evolution');
  assert.equal(p.$('#evLens').hidden, true);
  p.$('#evGap').listeners.input({target: {value: 300}});
  p.$('#evScroll').scrollLeft = 700;
  p.$('#evScroll').scrollTop = 40;
  await p.host.openSelection(node('category:M3:rsl:<3M', 'Sep'));
  assert.equal(p.$('#evDrillTitle').textContent, 'Category evolution');
  assert.equal(p.$('#evBack').textContent, '← Back to RSL');
  assert.match(p.$('#evDrillScope').textContent, /sales_push.*<3M/);
  const request = p.requests.findLast(url => url.pathname.endsWith('/selection'));
  assert.deepEqual(JSON.parse(request.searchParams.get('path')), [
    {node: 'category:M2:sales_push'}, {node: 'category:M3:rsl:<3M'},
  ]);
  for (const [key, expected] of Object.entries({unit: 'bdt', brand: 'Brand A', town: 'Dhaka', parent: '101', material: '1001', rsl: '3M–6M', as_of: '2026-08-30'})) {
    assert.equal(request.searchParams.get(key), expected);
  }
  await p.$('#evBack').onclick();
  assert.equal(p.$('#evDrillTitle').textContent, 'RSL evolution');
  assert.equal(p.$('#evScroll').scrollLeft, 700);
  assert.equal(p.$('#evScroll').scrollTop, 40);
  assert.equal(p.evolution.captureScope().gap, 300);
  assert.equal(p.evolution.captureScope().drill.path.length, 1);
  assert.match(p.$('#evSel').html, /&lt;3M/);
  await p.$('#evBack').onclick();
  assert.equal(p.evolution.captureScope().drill, null);
  assert.equal(p.$('#evLens').hidden, false);
  assert.equal(p.$('#evScroll').scrollLeft, 150);
  assert.equal(p.$('#evScroll').scrollTop, 20);
  assert.equal(p.evolution.captureScope().gap, 240);
  assert.match(p.$('#evSel').html, /sales_push/);
});

test('RSL selections and hidden >10M transitions use unambiguous descriptors', async () => {
  const p = page();
  await p.open('rsl');
  await p.host.openSelection(node('category:M1:rsl:>10M'));
  assert.equal(p.$('#evDrillTitle').textContent, 'Category evolution');
  assert.deepEqual(json(p.evolution.captureScope().drill.path), [{node: 'category:M1:rsl:>10M'}]);
  await p.$('#evBack').onclick();
  p.$('#evTen').listeners.click();
  await flush();
  await p.host.openSelection({id: 'rsl-downgrade:M1:M2', kind: 'rsl_downgrade', label: 'Transitioned from >10M'});
  assert.deepEqual(json(p.evolution.captureScope().drill.path), [
    {source: 'category:M1:rsl:>10M', target: 'month:M2', hide10: true},
  ]);
});

test('Back and report Close prevent a delayed selected response from replacing the parent', async () => {
  for (const close of [false, true]) {
    const p = page();
    await p.open();
    const previous = p.evolution.captureScope();
    let release;
    p.intercept(url => url.pathname.endsWith('/selection') ? new Promise(resolve => { release = resolve; }) : null);
    const opening = p.host.openSelection(node('category:M2:sales_push'));
    assert.equal(p.$('#root').dataset.state, 'status');
    if (close) p.evolution.restoreScope(previous);
    else await p.$('#evBack').onclick();
    release({ok: true, json: async () => ({nodes: [], links: [], transitions: []})});
    await opening;
    assert.equal(p.evolution.captureScope().drill, null);
    assert.equal(p.data.nodes[0].id, 'category:M2:sales_push');
    assert.equal(p.$('#evLens').hidden, false);
  }
});

test('Retry repeats the full selection and CSV identifies the path and displayed units', async () => {
  const p = page();
  await p.open();
  let fail = true;
  p.intercept(url => url.pathname.endsWith('/selection') && fail ? {ok: false, json: async () => ({error: 'Try again'})} : null);
  await p.host.openSelection(node('category:M2:sales_push'));
  assert.match(p.$('#evStatus').html, /Try again/);
  assert.equal(p.$('#evDrill').hidden, false);
  fail = false;
  await p.$('#evRetry').onclick();
  await p.host.openSelection(node('category:M3:rsl:<3M'));
  assert.ok(p.evolution.exportCsv() > 0);
  const csv = await p.blob.text();
  assert.match(csv, /selection_path/);
  assert.match(csv, /category:M2:sales_push/);
  assert.match(csv, /category:M3:rsl:<3M/);
  assert.match(csv, /amount_m_bdt/);
  assert.match(csv, /Selected category evolution/);
  p.evolution.setUnit('kl');
  await flush();
  await flush();
  assert.equal(p.evolution.captureScope().drill.path.length, 2);
  assert.equal(p.requests.findLast(url => url.pathname.endsWith('/selection')).searchParams.get('unit'), 'kl');
});

test('shared dropdowns keep report bounds, dependent catalogs, labels and cache', async () => {
  const p = page();
  await p.open();
  assert.deepEqual([...p.dropdowns.values()].map(d => d.label), ['Brand', 'Parent SKU', 'Town', 'Material', 'RSL']);
  p.intercept(url => url.pathname.endsWith('/filters') ? {ok: true, json: async () => ({
    brand: ['Brand A', 'Brand B'], parent: ['101'], town: ['Dhaka'], material: ['1001'], rsl: ['3M–6M'],
    material_labels: {'1001': 'Material description'},
  })} : null);
  const brand = p.dropdowns.get('brand');
  brand.apply(new Set(['Brand B']));
  await brand.open();
  const query = p.requests.findLast(url => url.pathname.endsWith('/filters')).searchParams;
  for (const [key, expected] of Object.entries({brand: 'Brand A', parent: '101', town: 'Dhaka', material: '1001', rsl: '3M–6M', as_of: '2026-08-30'})) {
    assert.equal(query.get(key), expected);
  }
  assert.deepEqual([...brand.selection()], ['Brand B']);
  const count = p.requests.length;
  await brand.open();
  assert.equal(p.requests.length, count, 'reopening uses the cached dependent catalog');
  await p.dropdowns.get('material').open();
  const dependent = p.requests.findLast(url => url.pathname.endsWith('/filters')).searchParams;
  assert.equal(dependent.get('brand'), 'Brand B');
  assert.equal(dependent.get('material'), '1001');
  assert.equal(p.dropdowns.get('material').allOptions[0].label, 'Material description');
  brand.apply(null);
  assert.deepEqual(json(p.evolution.captureScope().filters.brand), ['Brand A']);
  p.dropdowns.get('town').apply(new Set(['Chattogram']));
  p.$('#evFilters').children.find(child => child.id === 'evFilterClear').click();
  const scope = p.evolution.captureScope();
  assert.deepEqual(json(scope.filters), {...json(scope.bounds),category:[]});
  assert.ok([...p.dropdowns.values()].every(d => d.popup === null));
});

test('category report bounds survive filtering, Clear, drilling, Back and CSV export', async () => {
  const p = page();
  await p.open();
  const scope = p.evolution.captureScope();
  scope.filters.category = ['Actionable','Incorrect stock'];
  scope.bounds.category = ['Actionable','Incorrect stock'];
  p.evolution.restoreScope(scope);
  p.dropdowns.get('town').apply(new Set(['Chattogram']));
  await flush();
  assert.deepEqual(p.requests.findLast(url => url.pathname.endsWith('/exposure')).searchParams.getAll('category'), ['Actionable','Incorrect stock']);
  p.$('#evFilters').children.find(child => child.id === 'evFilterClear').click();
  await flush();
  assert.deepEqual(json(p.evolution.captureScope().filters.category), ['Actionable','Incorrect stock']);
  await p.host.openSelection(node('category:M2:sales_push'));
  assert.deepEqual(p.requests.findLast(url => url.pathname.endsWith('/selection')).searchParams.getAll('category'), ['Actionable','Incorrect stock']);
  await p.$('#evBack').onclick();
  assert.deepEqual(json(p.evolution.captureScope().filters.category), ['Actionable','Incorrect stock']);
  assert.ok(p.evolution.exportCsv() > 0);
  const csv = await p.blob.text();
  assert.match(csv, /category_filter/);
  assert.match(csv, /Actionable; Incorrect stock/);
});

test('search, Cancel and unavailable current values retain explicit selections and export scope', async () => {
  const p = page();
  await p.open();
  p.intercept(url => url.pathname.endsWith('/filters') ? {ok: true, json: async () => ({brand: ['Brand B', 'Brand C']})} : null);
  const brand = p.dropdowns.get('brand');
  await brand.open();
  assert.equal(brand.allOptions.find(o => o.value === 'Brand A').unavailable, true);
  assert.deepEqual([...brand.result()], ['Brand A']);
  brand.search.value = 'Brand B';
  brand.search.oninput();
  brand.add.checked = true;
  brand.add.onchange();
  brand.ok.click();
  await flush();
  assert.deepEqual(json(p.evolution.captureScope().filters.brand), ['Brand A', 'Brand B']);
  assert.equal(p.requests.findLast(url => url.pathname.endsWith('/exposure')).searchParams.getAll('brand').join('|'), 'Brand A|Brand B');
  await brand.open();
  brand.search.value = 'Brand C';
  brand.search.oninput();
  const footer = brand.popup.children.at(-1);
  footer.children[0].click();
  assert.deepEqual(json(p.evolution.captureScope().filters.brand), ['Brand A', 'Brand B'], 'Cancel discards the draft');
  assert.ok(p.evolution.exportCsv() > 0);
  assert.match(await p.blob.text(), /Brand A; Brand B/);
  await p.host.openSelection(node('category:M2:sales_push'));
  await p.$('#evBack').onclick();
  assert.deepEqual(json(p.evolution.captureScope().filters.brand), ['Brand A', 'Brand B']);
});

test('scope changes close pending catalogs and stale responses cannot replace a new popup', async () => {
  const p = page();
  await p.open();
  let release;
  p.intercept(url => url.pathname.endsWith('/filters') ? new Promise(resolve => { release = resolve; }) : null);
  const brand = p.dropdowns.get('brand');
  const pending = brand.open();
  const oldPopup = brand.popup;
  p.evolution.restoreScope({...p.evolution.captureScope(), asOf: '2026-07-31'});
  assert.equal(oldPopup.removed, true);
  assert.equal(brand.popup, null);
  p.intercept(url => url.pathname.endsWith('/filters') ? {ok: true, json: async () => ({brand: ['Fresh']})} : null);
  await brand.open();
  assert.equal(p.requests.at(-1).searchParams.get('as_of'), '2026-07-31');
  const freshPopup = brand.popup;
  release({ok: true, json: async () => ({brand: ['Stale']})});
  await pending;
  assert.equal(brand.popup, freshPopup);
  assert.ok(brand.allOptions.some(o => o.value === 'Fresh'));
  assert.ok(!brand.allOptions.some(o => o.value === 'Stale'));
  assert.deepEqual(json(p.evolution.captureScope().filters.brand), ['Brand A']);
});

test('rejected catalog promises are handled both while open and after a scope change', async () => {
  const p = page();
  await p.open();
  const brand = p.dropdowns.get('brand');
  p.intercept(url => {
    if (url.pathname.endsWith('/filters')) throw new Error('Network disconnected');
    return null;
  });
  await assert.doesNotReject(brand.open());
  assert.match(brand.list.textContent, /Could not load values/);
  let reject;
  p.intercept(url => url.pathname.endsWith('/filters') ? new Promise((resolve, fail) => { reject = fail; }) : null);
  const pending = brand.open();
  p.evolution.restoreScope(p.evolution.captureScope());
  reject(new Error('Delayed network failure'));
  await assert.doesNotReject(pending);
  assert.equal(brand.popup, null);
  p.intercept(url => url.pathname.endsWith('/filters') ? {ok: true, json: async () => ({brand: ['Brand A']})} : null);
  await brand.open();
  assert.deepEqual(brand.allOptions.map(o => typeof o.value), ['string']);
  assert.ok([...brand.selection()].every(value => typeof value === 'string'));
});

test('unbounded All maps to empty filters and failed catalogs can retry', async () => {
  const p = page();
  await p.open();
  p.evolution.restoreScope({...p.evolution.captureScope(), bounds: {}, filters: {}});
  const brand = p.dropdowns.get('brand');
  assert.equal(brand.selection(), null);
  let fail = true;
  p.intercept(url => url.pathname.endsWith('/filters') ? {ok: !fail, json: async () => fail ? {error: 'Unavailable'} : {brand: ['A', 'B']}} : null);
  await brand.open();
  assert.match(brand.list.textContent, /Could not load values/);
  fail = false;
  await brand.open();
  brand.search.value = 'A';
  brand.search.oninput();
  brand.ok.click();
  assert.deepEqual(json(p.evolution.captureScope().filters.brand), ['A']);
  await brand.open();
  brand.all.checked = true;
  brand.all.onchange();
  brand.ok.click();
  assert.deepEqual(json(p.evolution.captureScope().filters.brand), []);
  assert.equal(brand.selection(), null);
});
