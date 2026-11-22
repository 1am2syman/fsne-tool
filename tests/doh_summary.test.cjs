const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {matches} = require('../ui/report_filters.js');
const template = fs.readFileSync('ui/template.html', 'utf8');
const summarySource = template.slice(template.indexOf('function dohSummary('), template.indexOf('let B,TOT,TPTS;'));
let selections = {};
const context = vm.createContext({statusFilters: {
  selection: () => Object.fromEntries(Object.entries(selections).map(([key, values]) => [key, [...values]])),
  matches: row => matches(row, selections),
}});
vm.runInContext(summarySource, context);
vm.runInContext(template.slice(template.indexOf('function statusEvolutionFilters('), template.indexOf('const statusHierarchy=')), context);
const summary = pairs => context.dohSummary(pairs, new Set(['< 3M']));
const pair = (t, rate, stock) => ({t, p:'10', rate, stock:{'< 3M':stock}});

test('mixed coverage excludes missing stock from DOH but preserves warning facts', () => {
  const c = summary([pair('known', 10, 20), pair('missing', null, 5)]);
  assert.equal(c.value, 60);
  assert.equal(c.stock, 25);
  assert.equal(c.coveredStock, 20);
  assert.equal(c.rate, 10);
  assert.equal(c.missing, 1);
  assert.equal(c.missingStock, 5);
});
test('all missing stock remains unavailable, not infinite', () => {
  assert.equal(summary([pair('missing', null, 5)]).value, null);
});
test('known zero demand remains infinite even alongside excluded missing sources', () => {
  assert.equal(summary([pair('zero', 0, 20), pair('missing', null, 5)]).value, Infinity);
});
test('town and both brand projections select identical underlying cohorts', () => {
  const pairs = [{t:'A',b:'X',p:'10'}, {t:'A',b:'X',p:'11'}, {t:'B',b:'X',p:'10'}, {t:'A',b:'Y',p:'20'}];
  const pick = (n,view) => Array.from(context.statusDohPairs(n,view,pairs));
  assert.equal(pick({id:'total'},'town').length,4);
  assert.equal(pick({id:'t',level:0,name:'A'},'town').length,3);
  assert.equal(pick({id:'b',level:1,name:'X',town:'A'},'town').length,2);
  assert.deepEqual(pick({id:'p',level:2,town:'A',brand:'X',code:'10'},'town'),[pairs[0]]);
  assert.equal(pick({id:'b',level:0,path:['X']},'brand-town').length,3);
  assert.deepEqual(pick({id:'p',level:2,path:['X','A','desc'],code:'10'},'brand-town'),[pairs[0]]);
  assert.equal(pick({id:'p',level:1,path:['X','desc'],code:'10'},'brand-parent').length,2);
  assert.deepEqual(pick({id:'t',level:2,path:['X','desc','A'],parentCode:'10'},'brand-parent'),[pairs[0]]);
});
test('active value filters narrow DOH totals and retain multi-brand town scope', () => {
  const pairs = [{t:'A',b:'X',p:'10'}, {t:'A',b:'Y',p:'20'}, {t:'B',b:'X',p:'10'}];
  const pick = n => Array.from(context.statusDohPairs(n, 'town', pairs));
  selections = {brand: new Set(['X', 'Y']), town: new Set(['A'])};
  assert.deepEqual(pick({id:'total'}), pairs.slice(0, 2));
  assert.deepEqual(pick({id:'t',level:0,name:'A',brand:'X'}), pairs.slice(0, 2));
  selections.parent = new Set(['20']);
  assert.deepEqual(pick({id:'total'}), [pairs[1]]);
  selections = {};
});
test('movement restores stock overview; status renders DOH', () => {
  const movement = template.slice(template.indexOf('const mcols='),template.indexOf('const full='));
  assert.ok(!movement.includes("k:'doh'"));
  assert.ok(movement.includes("k:'now'"));
  assert.ok(template.includes("help:'doh',num:1,r:statusDohCell"));
});
test('complete coverage and selected RSL calculation are unchanged', () => {
  const p = pair('known', 10, 20); p.stock['> 10M'] = 100;
  assert.equal(summary([p]).value, 60);
  assert.equal(summary([]).value, null);
  assert.throws(() => summary([p,p]), /Duplicate DOH/);
});
