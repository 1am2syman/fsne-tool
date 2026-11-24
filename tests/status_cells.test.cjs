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
    }
    // The LND share is painted as the cell background, never as a positioned child element.
    assert.doesNotMatch(html,/<i /);
    assert.doesNotMatch(html,/rsl-green|rsl-red|rsl-amber|rsl-exposure|class="risk|long-track/);
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
  assert.match(html,/has-exp has-lnd/);
  assert.doesNotMatch(html,/<i |long-track/);
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
function statusTable(metric,months,scopeEnd=null,selected=['< 3M','> 10M']){
  months=months.map(cs=>cs&&cs.map((c,i)=>selected.includes(['< 3M','> 10M'][i])?c:[0,0,0]));
  const leaf={id:'row',name:'Example',level:0,kids:[],m:months};
  const root={...leaf,id:'total',kids:[leaf]};
  const element={classList:{add(){}},dataset:{},closest(){return this}};
  let rendered;
  const ctx=vm.createContext({
    STATUS:{labels:['Apr 2026','Oct 2026'].slice(0,months.length),groups:['< 3M','> 10M']},
    statusMetric:metric,statusMetricNames:['stock-bdt','exposure-bdt','lnd-bdt'],
    statusScope:scopeEnd==null?null:new Map(),statusSnap:scopeEnd,statusView:'town',
    statusSort:{k:'total',d:-1},statusOpen:new Set(),statusTip:{},
    filteredStatusTree:()=>root,indexStatus(){},statusPairs:()=>[],statusDohPairs:()=>[],dohSummary:()=>({}),
    statusHierarchy:()=>'Town → Brand → Parent',statusName:n=>n.name,statusCell:()=>'',statusDohCell:()=>'',
    statusCategoryLabel:()=>'All stock categories',statusSelectedGroups:()=>selected,bindEvolutionCells(_t,_rows,specs){rendered.evolution=specs},normalizeEvolutionRsl:g=>g.replace(/\s/g,''),
    sum:a=>a.reduce((s,v)=>s+(v??0),0),amount:v=>String(v),esc:v=>v,unitLabel:()=>'BDT',
    spark:p=>JSON.stringify(p),cmp:()=>()=>0,$:()=>element,$$:()=>[],table:options=>{rendered=options},
  });
  vm.runInContext(template.slice(template.indexOf('const statusEnd='),template.indexOf('function statusCell(')),ctx);
  vm.runInContext(template.slice(template.indexOf('function drawStatus('),template.indexOf("\n$('#sT').closest('.tw').addEventListener('click'")),ctx);
  ctx.drawStatus();
  return rendered;
}
const trendMonths=[[[100,20,5],[200,60,15]],[[50,10,2],[150,30,8]]];
for(const [metric,label,points] of [[0,'Stock',[300,200]],[1,'Exposure',[80,40]],[2,'LND FC',[20,10]]]){
  test(`${label} status history follows the selected metric in rows and the grand total`,()=>{
    const rendered=statusTable(metric,trendMonths),row=rendered.rows[0];
    const last=rendered.cols.at(-1),trend=rendered.cols.find(c=>c.k==='spark');
    assert.equal(last.r(row),`${points[0]} → <b style="font-weight:600">${points[1]}</b>`);
    assert.equal(rendered.foot.at(-1),last.r(row));
    assert.equal(trend.r(row),JSON.stringify(points));
    assert.equal(rendered.foot.at(-3),trend.r(row));
    assert.equal(trend.h,label+' trend');
    assert.equal(rendered.groups.at(-1).label,label+' overview');
  });
}
test('deselected RSL columns vanish and footer, trend and evolution columns stay aligned',()=>{
  for(const metric of [0,1,2]){
    const rendered=statusTable(metric,trendMonths,null,['> 10M']);
    assert.deepEqual(Array.from(rendered.cols,c=>c.k),['name','g1','total','spark','doh','now']);
    assert.equal(rendered.foot.length,rendered.cols.length);
    assert.equal(rendered.groups[1].span,2);
    const values=trendMonths.map(cs=>cs[1][metric]);
    assert.equal(rendered.rows[0].total,values.at(-1));
    assert.equal(rendered.cols.find(c=>c.k==='spark').r(rendered.rows[0]),JSON.stringify(values));
    if(metric)assert.deepEqual(Array.from(rendered.evolution,s=>s.column),[1,2]);
  }
});

test('status history preserves missing snapshots, zero values and scoped month endpoints',()=>{
  const missing=statusTable(1,[null,[[0,0,0],[0,0,0]]]);
  assert.equal(missing.cols.at(-1).r(missing.rows[0]),'— → <b style="font-weight:600">0</b>');
  const scoped=statusTable(2,trendMonths,0);
  assert.equal(scoped.cols.at(-1).r(scoped.rows[0]),'<b style="font-weight:600">20</b>');
  assert.equal(scoped.cols.find(c=>c.k==='spark').r(scoped.rows[0]),'—');
  const single=statusTable(1,trendMonths.slice(0,1));
  assert.equal(single.cols.at(-1).r(single.rows[0]),'<b style="font-weight:600">80</b>');
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
