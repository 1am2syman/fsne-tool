const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const template=fs.readFileSync('ui/template.html','utf8');
function renderer(metric){
  const ctx=vm.createContext({STATUS:{groups:['< 3M','3M - 6M','6M - 10M','> 10M']},statusMetric:metric,
    statusLatest:n=>n.m.at(-1),sum:a=>a.reduce((s,v)=>s+(v??0),0),esc:v=>v,amount:v=>String(v)});
  const amountStart=template.indexOf('const statusAmount=');
  vm.runInContext(template.slice(amountStart,template.indexOf('\n',amountStart)),ctx);
  vm.runInContext(template.slice(template.indexOf('function statusCell('),template.indexOf('\nconst statusTip=')),ctx);
  return ctx.statusCell;
}
const row={m:[[[10,2,1],[20,6,3],[30,12,4],[100,10,2]]]};
test('Exposure cells use light red with a red LND share against their own exposure',()=>{
  const render=renderer(1);
  for(let i=0;i<4;i++){
    const html=render(row,i),expected=i===0?0:row.m[0][i][2]/row.m[0][i][1];
    assert.match(html,/status-value exposure-value/);
    assert.match(html,/--fill:100%/);
    if(i===0)assert.doesNotMatch(html,/class="risk |has-exposure/);
    else {
      assert.match(html,/has-exposure/);
      assert.match(html,/class="risk lnd"/);
    }
    assert.doesNotMatch(html,/rsl-green|rsl-red|rsl-amber|rsl-exposure|risk exp|long-track/);
    assert.equal(Number(html.match(/--lnd:([\d.]+)/)[1]),expected);
    assert.match(html,/"exposureComposition":true/);
    assert.match(html,new RegExp('status-main">'+row.m[0][i][1]+'<'));
  }
});
test('LND mode shows unchanged values without coloured bars in every RSL band',()=>{
  const render=renderer(2);
  for(let i=0;i<4;i++){
    const html=render(row,i);
    assert.doesNotMatch(html,/rsl-|risk |long-track|exposure-value/);
    assert.match(html,/--fill:0%/);
    assert.match(html,new RegExp('status-main">'+row.m[0][i][2]+'<'));
  }
});
test('Stock >10M retains full stock composition with exposure and LND stock denominators',()=>{
  const html=renderer(0)(row,3);
  assert.match(html,/long-value has-stock.*rsl-green/);
  assert.match(html,/--fill:100%/);
  assert.match(html,/--exp:0.1;--lnd:0.02/);
  assert.match(html,/"stockComposition":true/);
});
test('zero or unavailable exposure has no red segment even when stock is present',()=>{
  for(const metric of [1,2]){
    const render=renderer(metric);
    assert.match(render({m:[[[0,0,0],[0,0,0],[0,0,0],[100,0,0]]]},3),/--fill:0%/);
    assert.match(render({m:[null]},3),/--fill:0%.*status-main">—/);
    assert.doesNotMatch(render({m:[[[0,0,0],[0,0,0],[0,0,0],[100,0,0]]]},3),/class="risk |has-exposure/);
  }
});
test('movement CSV uses filtered facts and selected RSL with both saved units',()=>{
  const ctx=vm.createContext({num:v=>v??0,sum:a=>a.reduce((s,v)=>s+(v??0),0)});
  vm.runInContext(template.slice(template.indexOf('function movementCsvRows('),template.indexOf('function exportMovement(')),ctx);
  const c=(lm,o,n)=>({lm,o,n,kl:{lm:lm/100,o:o/100,n:n/100}});
  const source={brands:[{b:'A',m:[[c(100,90,5),c(500,450,50)]]}],total:[[c(100,90,5),c(500,450,50)]]};
  const rows=ctx.movementCsvRows(source,new Set([0]),['2026-09-30'],'2026-08-31');
  assert.equal(rows.length,2);
  assert.equal(rows[0].opening_bdt,100);assert.equal(rows[0].closing_bdt,95);
  assert.equal(rows[0].net_change_pct_bdt,-5);assert.equal(rows[0].net_reduction_bdt,10);
  assert.equal(rows[0].opening_kl,1);assert.ok(Math.abs(rows[0].closing_kl-.95)<1e-12);
  assert.equal(rows[1].brand,'All stock');assert.equal(rows[1].closing_bdt,rows[0].closing_bdt);
});
