const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync('ui/template.html', 'utf8');
const source = template.slice(template.indexOf('const OVERVIEW_RSL='), template.indexOf('// Month buttons are rebuilt'));
const groups = ['> 10M', '6M - 10M', '3M - 6M', '< 3M'];

function makeContext({labels = ['Jul 2026', 'Aug 2026'], unit = 'bdt'} = {}) {
  const context = vm.createContext({
    STATUS: {groups, labels},
    unitDivisor: () => unit === 'bdt' ? 1e6 : 1,
    unitLabel: () => unit === 'bdt' ? 'M BDT' : 'KL',
    esc: value => String(value),
  });
  vm.runInContext(source, context);
  return context;
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
