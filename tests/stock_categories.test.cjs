const {test, beforeEach} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {matches} = require('../ui/report_filters.js');
const template = fs.readFileSync('ui/template.html', 'utf8');
const month = (stock, exposure = stock / 2, lnd = stock / 4) => [[stock, exposure, lnd]];
let context, selections;
beforeEach(() => {
  selections = {};
  const facets = [
    {town:'A',brand:'X',parent:'10',pdesc:'Parent',category:'Actionable',m:[month(200),month(100)],m_kl:[month(2),month(1)]},
    {town:'A',brand:'X',parent:'10',pdesc:'Parent',category:'Incorrect stock',m:[null,month(300)],m_kl:[null,month(3)]},
    {town:'A',brand:'X',parent:'10',pdesc:'Parent',category:'Good stock',m:[month(400),month(800)],m_kl:[month(4),month(8)]},
    {town:'B',brand:'Y',parent:'20',pdesc:'Other parent',category:'Non actionable',m:[month(500),month(200)],m_kl:[month(5),month(2)]},
  ];
  context = vm.createContext({
    STATUS: {labels:['Jul 2026','Aug 2026'],groups:['< 3M'],facets,
      total:{m:[month(1100),month(1400)],kids:[]},total_kl:{m:[month(11),month(14)],kids:[]}},
    SERIES:{doh:{pairs:[{t:'A',b:'X',p:'10',rate:2},{t:'B',b:'Y',p:'20',rate:1}]}},
    statusScope:null,statusSnap:null,statusGroup:null,statusCategoryScope:null,statusMetric:0,statusView:'town',reportUnit:'bdt',
    statusRslSelections:new Map(),normalizeEvolutionRsl:g=>g.replace(/\s/g,'').replaceAll('-','–'),
    statusFilters:{matches:(row,exclude)=>matches(row,selections,exclude),
      selection:()=>Object.fromEntries(Object.entries(selections).filter(([,s])=>s!=null).map(([k,s])=>[k,[...s]]))},
    sum:items=>items.reduce((a,b)=>a+b,0),
  });
  vm.runInContext(template.slice(template.indexOf('const statusLeafKey='),template.indexOf('function setStatusView(')),context);
  vm.runInContext(template.slice(template.indexOf('function statusEvolutionFilters('),template.indexOf('const statusHierarchy=')),context);
  vm.runInContext(template.slice(template.indexOf('function dohSummary('),template.indexOf('let B,TOT,TPTS;')),context);
});
const plain = value => JSON.parse(JSON.stringify(value));

test('all categories preserve stock and every selected metric in both units', () => {
  for (const [unit,expected] of [['bdt',1400],['kl',14]]) {
    context.reportUnit = unit;
    const tree = context.filteredStatusTree();
    assert.deepEqual(plain(tree.m[1]),month(expected));
    assert.equal(tree.kids.length,2);
  }
});

test('category OR combines with dimensional AND filters, rebuilding hierarchy totals', () => {
  selections.category = new Set(['Actionable','Incorrect stock']);
  for (const view of ['town','brand-town','brand-parent']) {
    context.statusView = view;
    const tree = context.filteredStatusTree();
    assert.equal(tree.kids.length,1);
    assert.deepEqual(plain(tree.m),[month(200),month(400)]);
    assert.deepEqual(plain(tree.kids[0].kids[0].kids[0].m),[month(200),month(400)]);
  }
  selections.town = new Set(['B']);
  const empty = context.filteredStatusTree();
  assert.equal(empty.kids.length,0);
  assert.deepEqual(plain(empty.m[1]),month(0));
});

test('category cohort disappearance is zero at root; a missing whole snapshot remains a gap', () => {
  selections.category = new Set(['Unclassified']);
  context.STATUS.total.m[0] = null;
  assert.deepEqual(plain(context.filteredStatusTree().m),[null,month(0)]);
});

test('DOH sums selected category stock and counts each town-parent rate once', () => {
  selections.category = new Set(['Actionable','Incorrect stock']);
  const pairs = context.statusPairs();
  assert.equal(pairs.length,1);
  const filtered = context.statusDohPairs({id:'total'},'town',pairs);
  const cover = context.dohSummary(filtered,new Set(['< 3M']));
  assert.equal(cover.stock,4);
  assert.equal(cover.rate,2);
  assert.equal(cover.value,60);
  selections.category = new Set(['Incorrect stock']);
  assert.equal(context.dohSummary(context.statusPairs(),new Set(['< 3M'])).value,45);
  context.reportUnit='kl';
  assert.equal(context.dohSummary(context.statusPairs(),new Set(['< 3M'])).value,45);
});

test('selected stock with a missing rate remains flagged instead of treating demand as zero', () => {
  selections.category = new Set(['Non actionable']);
  context.SERIES.doh.pairs.pop();
  const cover = context.dohSummary(context.statusPairs(),new Set(['< 3M']));
  assert.equal(cover.stock,2);
  assert.equal(cover.missingStock,2);
  assert.equal(cover.value,null);
});

test('linked evolution carries the category selection alongside the clicked row', () => {
  selections.category = new Set(['Actionable','Incorrect stock']);
  selections.brand = new Set(['X','Y']);
  const filters = plain(context.statusEvolutionFilters({id:'town-A',level:0,name:'A'},'town'));
  assert.deepEqual(filters,{category:['Actionable','Incorrect stock'],brand:['X','Y'],town:['A']});
});

test('RSL selection recomputes every hierarchy, metric and unit without changing saved facts', () => {
  context.STATUS.groups.push('> 10M');
  for (const row of context.STATUS.facets) {
    for (const key of ['m','m_kl']) row[key].forEach(cs => {if(cs) cs.push(cs[0].map(v => v * 3))});
  }
  const before = JSON.stringify(context.STATUS.facets);
  context.statusRslSelections.set(undefined,new Set(['> 10M']));
  selections.category = new Set(['Actionable','Incorrect stock']);
  for (const [unit,scale] of [['bdt',100],['kl',1]]) {
    context.reportUnit = unit;
    for (const view of ['town','brand-town','brand-parent']) {
      context.statusView = view;
      const tree = context.filteredStatusTree();
      assert.deepEqual(plain(tree.m),[[[0,0,0],[6*scale,3*scale,1.5*scale]],[[0,0,0],[12*scale,6*scale,3*scale]]]);
      assert.deepEqual(plain(tree.kids[0].kids[0].kids[0].m),plain(tree.m));
    }
  }
  assert.equal(JSON.stringify(context.STATUS.facets),before);
  const cover = context.dohSummary(context.statusPairs(),new Set(context.statusSelectedGroups()));
  assert.equal(cover.stock,12);
  assert.equal(cover.rate,2);
  assert.equal(cover.value,180);
  assert.deepEqual(plain(context.statusEvolutionFilters({id:'total'},'town')).rsl,['>10M']);
  context.statusRslSelections.delete(undefined);
  assert.equal(context.filteredStatusTree().m[1][0][0],4);
});

test('Show data bounds categories and applies one traced scale per leaf across category parts', () => {
  context.statusScope = new Map([[JSON.stringify(['A','X','10']),200]]);
  context.statusSnap = 1;
  context.statusCategoryScope = ['Actionable','Incorrect stock'];
  const rows = context.statusLeaves(context.STATUS.total);
  assert.equal(rows.length,2);
  assert.equal(rows.reduce((s,r)=>s+r.m[1][0][0],0),200);
  assert.deepEqual(plain(rows.map(r=>r.m[1][0][0])),[50,150]);
  selections.category = new Set(['Incorrect stock']);
  assert.equal(context.filteredStatusTree().m[1][0][0],150);
  const pairs = context.statusPairs();
  assert.equal(pairs.length,1);
  assert.equal(pairs[0].stock['< 3M'],3);
});
