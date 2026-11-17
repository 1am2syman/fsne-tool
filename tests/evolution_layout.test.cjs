const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Run the real renderer through its layout boundary, before DOM painting.
function layout(payload, spacing = 240) {
  const context = vm.createContext({console, document: {}});
  for (const path of ['ui/evolution/d3.v7.9.2.min.js', 'ui/evolution/d3-sankey.v0.12.3.min.js']) {
    vm.runInContext(fs.readFileSync(path, 'utf8'), context);
  }
  const selection = {};
  for (const method of ['attr', 'style', 'classed', 'selectAll', 'remove', 'append', 'html', 'data', 'join', 'on', 'text']) {
    selection[method] = () => selection;
  }
  selection.node = () => ({getBoundingClientRect: () => ({height: 800})});
  context.d3.select = () => selection;
  const sankey = context.d3.sankey;
  const stop = new Error('layout complete');
  let graph;
  context.d3.sankey = () => {
    const generator = sankey();
    generator.update = value => { graph = value; throw stop; };
    return generator;
  };
  vm.runInContext(fs.readFileSync('ui/evolution/refchart.js', 'utf8'), context);
  const key = 'exposure_bdt';
  const controller = context.FSNE.refChart.create({spacing, unit: {div: 1e6}, layoutHeight: 800, scroller: {clientWidth: 1400}}, {
    default_metric: key, metric_options: [], metrics: {[key]: payload},
  });
  assert.throws(() => controller.update({lens: key, spacing}), error => error === stop);
  return graph;
}

function disconnectedHistory() {
  const node = (id, column, sort = 0) => ({id, column, sort, label: id, kind: 'category'});
  return {
    nodes: [node('Jun opening', 2), node('Sales push', 3), node('Rotation', 3, 1), node('Resolved after Jun', 4),
      node('Oct opening', 10), node('Added after Sep', 9), node('Resolved after Oct', 12)],
    links: [
      {source: 'Jun opening', target: 'Sales push', value: 20000},
      {source: 'Jun opening', target: 'Rotation', value: 18000},
      {source: 'Sales push', target: 'Resolved after Jun', value: 20000},
      {source: 'Rotation', target: 'Resolved after Jun', value: 18000},
      {source: 'Added after Sep', target: 'Oct opening', value: 620000},
      {source: 'Oct opening', target: 'Resolved after Oct', value: 130000},
    ],
  };
}

test('disconnected periods keep distinct chronological columns', () => {
  const {nodes, links} = layout(disconnectedHistory());
  for (const a of nodes) for (const b of nodes) {
    if (a.column < b.column) assert.ok(a.x1 < b.x0, `${a.id} and ${b.id} collapsed to x=${a.x0}/${b.x0}`);
    if (a.column === b.column) assert.equal(a.x0, b.x0);
  }
  for (const link of links) assert.ok(link.source.x1 < link.target.x0, 'flow must go forward');
});

test('long labels retain their lane at minimum spacing', () => {
  const payload = disconnectedHistory();
  payload.nodes[0].label = 'January 2026 opening exposure for a selected material';
  const {nodes} = layout(payload, 160);
  const left = nodes.find(node => node.id === 'Jun opening');
  const next = nodes.find(node => node.id === 'Sales push');
  assert.ok(next.x0 - left.x0 > left.label.length * 6.6, 'adjacent labels need horizontal clearance');
});

test('tiny and large amounts reserve space for both label lines in each column', () => {
  const payload = disconnectedHistory();
  payload.links[0].value = 0.6;
  payload.links[2].value = 0.6;
  const {nodes} = layout(payload, 160);
  const columns = new Map();
  for (const node of nodes) {
    const rows = columns.get(node.column) || [];
    rows.push(node); columns.set(node.column, rows);
    assert.ok([node.x0, node.x1, node.y0, node.y1].every(Number.isFinite));
  }
  for (const rows of columns.values()) {
    rows.sort((a, b) => a.y0 + a.y1 - b.y0 - b.y1);
    for (let i = 1; i < rows.length; i++) {
      const bottom = (rows[i - 1].y0 + rows[i - 1].y1) / 2 + (rows[i - 1].y1 - rows[i - 1].y0) * 0.125;
      const top = (rows[i].y0 + rows[i].y1) / 2 - (rows[i].y1 - rows[i].y0) * 0.125;
      assert.ok(top - bottom >= 40, 'two-line labels need clearance');
    }
  }
});
