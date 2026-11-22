const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('ui/template.html', 'utf8');
const source = template.slice(template.indexOf('const OVERVIEW_RSL='), template.indexOf('// Month buttons are rebuilt'));
const groups = ['> 10M', '6M - 10M', '3M - 6M', '< 3M'];

function overview(cells, unit = 'bdt') {
  const context = vm.createContext({
    STATUS: {groups, labels: ['Oct 2026']},
    unitDivisor: () => unit === 'bdt' ? 1e6 : 1,
    unitLabel: () => unit === 'bdt' ? 'M BDT' : 'KL',
    esc: value => String(value),
    cells,
  });
  vm.runInContext(source, context);
  return vm.runInContext(`(() => {
    const facts = overviewFacts([cells], STATUS.groups);
    return {hero: overviewHero(facts, 0, null, 0, () => ''), card: overviewExposureCard(facts[0])};
  })()`, context);
}

function percentages(html, className) {
  const rows = [...html.matchAll(new RegExp('<div class="' + className + '"[^>]*>([\\s\\S]*?)(?=<div class="' + className + '"|$)', 'g'))];
  return rows.slice(0, 4).map(row => [...row[1].matchAll(/<em>(.*?)<\/em>/g)].slice(0, className === 'ov-m' ? 3 : 2).map(match => match[1]));
}

test('lower Exposure and LND FC percentages match the table when Exposure differs from Stock', () => {
  const cells = [[1253e6, 37e6, 19.2e6], [19.5e6, 19.5e6, 3.26e6], [5.89e6, 5.89e6, 2.84e6], [1.77e6, 1.77e6, 1.77e6]];
  const {hero, card} = overview(cells);
  const expected = percentages(hero, 'ov-m').map(row => row.slice(1));
  assert.deepEqual(percentages(card, 'ov-br'), expected);
  assert.match(card, /% of group Stock/);
  const widths = [...card.matchAll(/style="width:([\d.]+)%"/g)].map(match => Number(match[1]));
  assert.ok(Math.abs(widths[0] - 37 / 1253 * 100) < 1e-10);
  assert.ok(Math.abs(widths[1] - 19.2 / 1253 * 100) < 1e-10);
  assert.deepEqual(widths.slice(-2), [100, 100]);
  assert.match(card, /Stock 1,253/);
});

test('KL uses its own Stock denominator and the same formatting as the table', () => {
  const {hero, card} = overview([[200, 20, 5], [10, 10, 2], [4, 4, 1], [2, 2, 2]], 'kl');
  assert.deepEqual(percentages(card, 'ov-br'), percentages(hero, 'ov-m').map(row => row.slice(1)));
  assert.match(card, />10%<\/em>/);
  assert.match(card, />2\.50%<\/em>/);
  assert.match(card, /KL/);
});

test('zero Stock has unavailable percentages and empty bars', () => {
  const {card} = overview(groups.map(() => [0, 0, 0]));
  assert.deepEqual(percentages(card, 'ov-br'), groups.map(() => ['–', '–']));
  assert.equal((card.match(/width:0%/g) || []).length, 8);
  assert.doesNotMatch(card, /NaN|Infinity/);
});
