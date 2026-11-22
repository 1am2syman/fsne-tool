/* Source-backed DOH explanation; never infer seasonal decisions from chart shape. */
const DOH_AUDIT=SERIES.doh.audit;
const dohTraceKey=(t,p)=>JSON.stringify([t,String(p)]);
const dohAuditTraces=new Map(DOH_AUDIT.traces.map(t=>[dohTraceKey(t.t,t.p),t]));
let dohView=null,dohOpener=null;
function dohFixed(v,d=6){if(v==null)return '—';if(!Number.isFinite(v))return '∞';return f(v,d)}
const dohMonthLabel=m=>new Date(m+'T00:00:00Z').toLocaleDateString('en-GB',{month:'short',year:'2-digit',timeZone:'UTC'});
const dohCalendarIndex=m=>Number(m.slice(0,4))*12+Number(m.slice(5,7))-1;
function dohTraceMonth(pair,i){
  const trace=dohAuditTraces.get(dohTraceKey(pair.t,pair.p));
  if(!trace)return {raw_kl:null,adjusted_kl:null,reason:'missing_rate',seasonal_supported:false};
  const row=trace.m[i];
  if(!row)return {raw_kl:0,adjusted_kl:0,reason:'missing_month_zero',seasonal_supported:false};
  return Object.fromEntries(DOH_AUDIT.fields.map((name,j)=>[name,row[j]]));
}
function dohEvidence(pairs){
  return DOH_AUDIT.months.map((month,i)=>{
    const records=pairs.map(p=>({p,...dohTraceMonth(p,i)}));
    const known=records.filter(r=>r.raw_kl!=null),counts={};
    records.forEach(r=>{counts[r.reason]=(counts[r.reason]||0)+1;if(r.seasonal_supported)counts.seasonal_supported=(counts.seasonal_supported||0)+1});
    const complete=records.length>0&&records.every(r=>r.expected_kl!=null);
    return {month,records,counts,raw:known.length?sum(known.map(r=>r.raw_kl)):null,
      adjusted:known.length?sum(known.map(r=>r.adjusted_kl)):null,
      expected:complete?sum(known.map(r=>r.expected_kl)):null,
      upper:records.length&&records.every(r=>r.upper_kl!=null)?sum(records.map(r=>r.upper_kl)):null};
  });
}
let dohGeo=null,dohShowAll=false;
function dohChart(months){
  const W=1000,H=260,L=64,R=18,T=28,B=36;
  const values=months.flatMap(m=>[m.raw,m.adjusted,m.expected,m.upper]).filter(v=>v!=null);
  if(!values.length){dohGeo=null;return '<p class="doh-chart-note">No secondary-sales history matches this stock. Add the missing town–parent rate source to derive cover.</p>'}
  const dataMax=Math.max(0.000001,...months.flatMap(m=>[m.raw,m.adjusted,m.expected]).filter(v=>v!=null));
  const top=dataMax*1.15;
  const start=dohCalendarIndex(months[0].month),finish=dohCalendarIndex(months.at(-1).month),span=finish-start;
  const X=i=>L+(W-L-R)*(span?(dohCalendarIndex(months[i].month)-start)/span:0.5),Y=v=>T+(H-T-B)*(1-Math.min(v,top)/top);
  const xs=months.map((_,i)=>X(i));dohGeo={W,months,xs};
  let svg='<title>Original and adjusted monthly secondary sales</title><desc>Gray is original monthly sales; blue is winsorized sales; dashed green is peer-supported expectation; dotted amber is the cap. Red marks clipped months, green rings mark seasonal patterns allowed.</desc>';
  for(let j=0;j<=4;j++){const v=top*j/4;svg+=`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)" ${j?'stroke-dasharray="3 5"':''}/><text x="${L-8}" y="${Y(v)+4}" text-anchor="end" fill="var(--ink3)" font-size="12">${dohFixed(v,dataMax<1?3:2)}</text>`}
  svg+=`<text x="${L-8}" y="12" text-anchor="end" fill="var(--ink3)" font-size="11">KL / month</text>`;
  const observed=new Set(months.map(m=>dohCalendarIndex(m.month)));
  for(let index=start;index<=finish;index++)if(!observed.has(index)){
    const x=L+(W-L-R)*(index-start)/span,m=new Date(Date.UTC(Math.floor(index/12),index%12,1)).toISOString().slice(0,10);
    svg+=`<g data-doh-global-gap="${m}"><rect x="${x-14}" y="${T}" width="28" height="${H-T-B}" fill="var(--sunken)" opacity=".7"/><text x="${x}" y="${H-12}" text-anchor="middle" fill="var(--ink3)" font-size="12">${esc(dohMonthLabel(m))}</text><text x="${x}" y="${T+14}" text-anchor="middle" fill="var(--ink3)" font-size="10">no data</text><title>No source data for this month in any town; excluded from the rate denominator</title></g>`;
  }
  function path(key,color,dash,width){let run=[];const flush=()=>{if(run.length>1)svg+=`<polyline data-doh-line="${key}" points="${run.join(' ')}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linejoin="round" ${dash?`stroke-dasharray="${dash}"`:''}/>`;else if(run.length)svg+=`<polyline data-doh-line="${key}" points="${run[0]} ${run[0]}" fill="none" stroke="${color}"/>`;run=[]};months.forEach((m,i)=>{if(i&&dohCalendarIndex(m.month)-dohCalendarIndex(months[i-1].month)>1)flush();if(m[key]==null)flush();else run.push(`${xs[i]},${Y(m[key])}`)});flush()}
  path('upper','var(--warn)','2 5',1.5);path('expected','var(--ok)','6 4',1.75);path('raw','var(--ink3)','',2);path('adjusted','var(--accent)','',2.5);
  months.forEach((m,i)=>{
    svg+=`<text x="${xs[i]}" y="${H-12}" text-anchor="middle" fill="var(--ink2)" font-size="12">${esc(dohMonthLabel(m.month))}</text>`;
    if(m.raw==null)return;
    if(m.counts.winsorized)svg+=`<g data-doh-marker="clipped"><line x1="${xs[i]}" x2="${xs[i]}" y1="${Y(m.raw)}" y2="${Y(m.adjusted)}" stroke="var(--bad)" stroke-width="3"/><circle cx="${xs[i]}" cy="${Y(m.raw)}" r="4.5" fill="var(--bad)"/></g>`;
    if(m.counts.seasonal_supported)svg+=`<circle data-doh-marker="seasonal" cx="${xs[i]}" cy="${Y(m.raw)}" r="8" fill="none" stroke="var(--ok)" stroke-width="2.25"/>`;
    svg+=`<circle cx="${xs[i]}" cy="${Y(m.adjusted)}" r="3.5" fill="var(--accent)"/>`;
  });
  svg+=`<line id="dohCross" y1="${T}" y2="${H-B}" stroke="var(--ink3)" stroke-dasharray="2 3" visibility="hidden"/>`;
  return `<div class="doh-chart-scroll"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Original versus adjusted monthly secondary sales, with clipped and seasonal months marked">${svg}</svg><div class="doh-tip" id="dohTip" hidden></div></div>`;
}
function dohTipShow(clientX){
  const g=dohGeo,svg=$('#dohBody svg');if(!g||!svg)return;
  const box=svg.getBoundingClientRect(),vx=(clientX-box.left)/box.width*g.W;
  let best=0;g.xs.forEach((x,i)=>{if(Math.abs(x-vx)<Math.abs(g.xs[best]-vx))best=i});
  const m=g.months[best],tip=$('#dohTip'),cross=$('#dohCross');
  if(m.raw==null){tip.hidden=true;return}
  cross.setAttribute('x1',g.xs[best]);cross.setAttribute('x2',g.xs[best]);cross.setAttribute('visibility','visible');
  const capped=m.raw-m.adjusted;
  tip.innerHTML=`<b>${esc(dohMonthLabel(m.month))}</b><div>Original <span>${dohFixed(m.raw)}</span></div><div>Adjusted <span>${dohFixed(m.adjusted)}</span></div>${capped>1e-12?`<div class="cut">Capped <span>−${dohFixed(capped)}</span></div>`:''}<div class="tags">${dohDecision(m,false)}</div>`;
  tip.hidden=false;
  const pct=g.xs[best]/g.W*100;tip.style.left=pct+'%';tip.style.transform=pct>60?'translateX(calc(-100% - 12px))':'translateX(12px)';
  $$('#dohBody tr[data-doh-month]').forEach(r=>r.classList.toggle('hl',r.dataset.dohMonth===m.month));
}
function dohTipHide(){const t=$('#dohTip');if(t)t.hidden=true;$('#dohCross')?.setAttribute('visibility','hidden');$$('#dohBody tr.hl').forEach(r=>r.classList.remove('hl'))}
const dohReasonLabels={unchanged:'Ordinary demand · kept',zero_preserved:'Observed zero · kept',insufficient_history:'Short history · kept',insufficient_peers:'Too few peers · kept',insufficient_residual_history:'Limited supported history · kept',ambiguous_peak_preserved:'Ambiguous peak · kept',missing_month_zero:'No source row · counts as zero',missing_rate:'Missing rate source'};
function dohDecision(m,single){
  const tags=[];
  if(m.counts.winsorized)tags.push(`<span class="doh-tag clipped">${m.counts.winsorized} capped</span>`);
  if(m.counts.seasonal_supported)tags.push(`<span class="doh-tag season">${m.counts.seasonal_supported} seasonal pattern · allowed</span>`);
  for(const [reason,count] of Object.entries(m.counts)){
    if(reason==='winsorized'||reason==='seasonal_supported')continue;
    const ordinary=reason==='unchanged'?count-(m.counts.seasonal_supported||0):count;if(!ordinary)continue;
    tags.push(`<span class="doh-tag ${reason==='ambiguous_peak_preserved'?'review':''}">${ordinary} ${esc(dohReasonLabels[reason]||reason)}</span>`);
  }
  if(!single)return tags.join(' ');
  const r=m.records[0];let explanation='';
  if(r.seasonal_supported)explanation=`${dohFixed(r.season_factor,2)}× peer uplift across ${r.peer_towns} other towns. Sales exceed the no-season cap (${dohFixed(r.unseasoned_upper_kl)} KL) but stay within the seasonal cap (${dohFixed(r.upper_kl)} KL); kept in full.`;
  else if(r.reason==='winsorized')explanation=`Above seasonal cap ${dohFixed(r.upper_kl)} KL; both adjacent months support an isolated spike. Removed ${dohFixed(r.raw_kl-r.adjusted_kl)} KL. Peer uplift ${dohFixed(r.season_factor,2)}× across ${r.peer_towns} towns.`;
  else if(r.reason==='ambiguous_peak_preserved')explanation='Above the cap, but boundary/gap/sustained-peak evidence is ambiguous. Not labelled seasonal and not clipped.';
  else if(r.expected_kl!=null)explanation=`Peer expectation ${dohFixed(r.expected_kl)} KL; seasonal cap ${dohFixed(r.upper_kl)} KL. ${r.peer_towns} other towns; no clipping needed.`;
  return tags.join(' ')+(explanation?`<div class="doh-chart-note">${esc(explanation)}</div>`:'');
}
function dohDrivers(view,selectedIndex){
  if(view.pairs.length<2)return '';
  const stockOf=p=>sum(Object.values(p.stock||{}));
  const ranked=view.pairs.map((p,i)=>({p,i,stock:stockOf(p)})).sort((a,b)=>b.stock-a.stock),shown=ranked.slice(0,12);
  const total=view.cover.rate||0;
  const rows=shown.map(({p,i,stock})=>{
    const ev=p.rate==null?null:dohEvidence([p]),cut=ev?sum(ev.map(m=>m.raw))-sum(ev.map(m=>m.adjusted)):null,seas=ev?sum(ev.map(m=>m.counts.seasonal_supported||0)):0;
    const doh=p.rate==null?null:p.rate>0?stock/p.rate*30:Infinity;
    return `<tr data-doh-pick="${i}" tabindex="0" ${i===selectedIndex?'aria-selected="true" class="on"':''}><td>${esc(p.t)} · <span class="muted">parent ${esc(p.p)}</span></td><td class="num">${dohFixed(stock,3)}</td><td class="num">${p.rate==null?'<span class="doh-tag">missing</span>':dohFixed(p.rate,4)}</td><td class="num">${p.rate==null||!total?'—':f(p.rate/total*100,0)+'%'}</td><td class="num">${doh==null?'—':Number.isFinite(doh)?f(doh,0):'∞'}</td><td>${cut>1e-12?`<span class="doh-tag clipped">−${dohFixed(cut,4)} capped</span>`:''}${seas?`<span class="doh-tag season">${seas} seasonal</span>`:''}</td></tr>`}).join('');
  return `<details class="doh-drivers" open><summary>What drives this number · ${ranked.length>12?'top 12 of '+ranked.length:ranked.length} town–parents by stock <span class="muted">(click a row to inspect its sales)</span></summary><div class="doh-months"><table aria-label="Town–parent drivers"><thead><tr><th>Town · parent</th><th>Stock KL</th><th>Rate KL/mo</th><th>Rate share</th><th>DOH</th><th>Effect</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
}
function drawDohExplanation(){
  const view=dohView,selectedIndex=+$('#dohTrace').value;
  const selected=selectedIndex<0?view.pairs:[view.pairs[selectedIndex]],months=dohEvidence(selected);
  const knownRatePairs=view.pairs.filter(p=>p.rate!=null);
  const cohortMonths=dohEvidence(knownRatePairs),rawTotal=sum(cohortMonths.map(m=>m.raw)),adjustedTotal=sum(cohortMonths.map(m=>m.adjusted));
  const rawRate=rawTotal/DOH_AUDIT.months.length,c=view.cover;
  let result='Unavailable';
  if(c.value!=null)result=Number.isFinite(c.value)?dohFixed(c.value,1)+' days':'∞ · no demand';
  let warning='';
  if(c.missing){
    const missing=view.pairs.filter(p=>p.rate==null).map(p=>p.t+' · parent '+p.p);
    warning=`⚠ ${c.missing} town–parent rate(s) missing: ${dohFixed(c.missingStock)} KL excluded from DOH. ${c.value==null?'No stock with known demand; DOH remains unavailable.':'DOH uses only '+dohFixed(c.coveredStock)+' KL with known demand ('+dohFixed(c.coveredStock/c.stock*100,2)+'% of selected stock).'}<details><summary>Missing sources (${missing.length})</summary>${esc(missing.join('; '))}</details>`;
  }
  if(!c.stock)warning='No positive stock in the selected RSL groups. DOH is unavailable.';
  const inspectLabel=selectedIndex<0?'All relevant town–parents':selected[0].t+' · parent '+selected[0].p;
  $('#dohScope').textContent='Showing: '+inspectLabel;
  const removal=rawTotal-adjustedTotal,hasKnown=knownRatePairs.length>0;
  const derivedRateText=hasKnown?dohFixed(c.rate)+' KL/month':'— · no rate source';
  const rateFormulaText=hasKnown?`${dohFixed(rawTotal)} original − ${dohFixed(removal)} capped = ${dohFixed(adjustedTotal)} KL ÷ ${DOH_AUDIT.months.length} months`:'A rate cannot be derived without matching secondary-sales history.';
  const tags=months.reduce((s,m)=>({clipped:s.clipped+(m.counts.winsorized||0),seasonal:s.seasonal+(m.counts.seasonal_supported||0)}),{clipped:0,seasonal:0});
  const scopedTotal=sum(months.map(m=>m.adjusted)),scopedRaw=sum(months.map(m=>m.raw));
  const hasScopedData=months.some(m=>m.raw!=null);
  const scopedFormula=hasScopedData?`${dohFixed(scopedRaw)} KL original → ${dohFixed(scopedTotal)} KL adjusted ÷ ${DOH_AUDIT.months.length} = ${dohFixed(scopedTotal/DOH_AUDIT.months.length)} KL/month`:'Rate unavailable: no matching source history';
  const isEvent=m=>!!(m.counts.winsorized||m.counts.seasonal_supported||m.counts.ambiguous_peak_preserved);
  const hasEvents=months.some(isEvent),filtered=hasEvents&&!dohShowAll;
  const rows=months.map(m=>`<tr data-doh-month="${esc(m.month)}" ${isEvent(m)?'data-doh-event':''}><td>${esc(dohMonthLabel(m.month))}</td><td class="num">${dohFixed(m.raw)}</td><td class="num">${dohFixed(m.adjusted)}</td><td class="num">${dohFixed(m.raw==null?null:m.raw-m.adjusted)}</td><td>${dohDecision(m,selected.length===1)}</td></tr>`).join('');
  const chips=`<span class="doh-tag clipped">${tags.clipped} capped</span><span class="doh-tag season">${tags.seasonal} seasonal allowed</span>`;
  $('#dohBody').html=`<div class="doh-equation" aria-label="Clicked cell calculation">
    <div><small>Selected stock · ${esc(view.scope)}</small><strong data-doh-calc="stock">${dohFixed(c.stock)} KL</strong><p>${esc(SERIES.doh.date)} · ${esc(view.rslLabel)}</p></div>
    <div><small>Monthly run rate${c.missing?' · known sources only':''}</small><strong data-doh-calc="rate">${derivedRateText}</strong><p data-doh-calc="rate-formula">${esc(rateFormulaText)}</p><p>Before winsorization: ${hasKnown?dohFixed(rawRate)+' KL/month':'unavailable'}</p></div>
    <div class="result"><small>Days on hand${c.missing?' · known-demand stock only':''}</small><strong data-doh-calc="result">${result}</strong><p data-doh-calc="doh-formula">${!c.coveredStock?'Cannot calculate: no stock with known demand':dohFixed(c.coveredStock)+' KL ÷ '+dohFixed(c.rate)+' KL/month × 30'}</p></div></div>
    ${warning?`<div class="doh-callout" role="status">${warning}</div>`:''}
    <h3>Sales trend and what was adjusted <small>${esc(inspectLabel)}</small></h3>
    <div class="doh-chart"><div class="doh-legend"><span><i></i>Original</span><span><i class="adjusted"></i>Adjusted</span><span><i class="expected"></i>Peer expectation</span><span><i class="cap"></i>Cap</span><span class="doh-tag clipped">Capped</span><span class="doh-tag season">Seasonal · allowed</span></div>${dohChart(months)}</div>
    <p class="doh-chart-note">${chips} <b>${esc(scopedFormula)}</b></p>
    ${dohDrivers(view,selectedIndex)}
    <div class="doh-months-head"><h3>Month by month</h3>${hasEvents?`<label class="doh-switch"><input type="checkbox" id="dohAll" ${dohShowAll?'checked':''}> Show all ${months.length} months</label>`:''}</div>
    <div class="doh-months ${filtered?'events-only':''}"><table aria-label="Monthly winsorization decisions"><thead><tr><th>Month</th><th>Original KL</th><th>Adjusted KL</th><th>Capped KL</th><th>Action & evidence</th></tr></thead><tbody>${rows}</tbody></table></div>
    <details class="doh-policy"><summary>How these numbers are produced</summary><p>Figures shown rounded; all calculations use full precision. Stock and sales always use KL, independent of the display unit. Original means monthly secondary sales before winsorization, after the pipeline’s negative-sales clamp.</p><p>${DOH_AUDIT.months.length} supplied months span ${dohCalendarIndex(DOH_AUDIT.months.at(-1))-dohCalendarIndex(DOH_AUDIT.months[0])+1} calendar months. Global source gaps are breaks in the chart and are excluded from the denominator. Missing town–parent months contribute zero to the rate, but are labelled separately. Peer expectation is drawn only when every inspected pair has a supported estimate. RSL selects stock and relevant rates—not the sales history itself.</p><p>A seasonal tag means sales exceeded the bound without seasonality but stayed within the peer-season-adjusted bound, so the original value was allowed in full. It is not inferred from the chart’s shape.</p><p>At least ${SERIES.doh.bounds.min_window_months} source months; ${SERIES.doh.bounds.min_observed_months} observed and ${SERIES.doh.bounds.min_positive_months} positive local months; ${SERIES.doh.bounds.min_peer_towns} other eligible towns. Expected sales = local median × leave-one-town-out peer seasonal factor. Upper residual = max(median + ${SERIES.doh.bounds.robust_sigma} × 1.4826 × MAD, ${SERIES.doh.bounds.min_upper_ratio} × median). Only isolated upper spikes are capped.</p><p>Only one annual cycle is available. Cross-town patterns can reflect seasonality or campaigns, not prove recurrence. Ambiguous local peaks are kept without a seasonal tag. The result is a cleaned historical average, not a seasonal forecast.</p></details>`;
}
function openDohExplanation(view,opener){
  dohView=view;dohOpener=opener||null;$('#mv').hidden=true;$('#help').hidden=true;sel=null;
  $('#dohTitle').textContent='How DOH is calculated · '+view.title;
  $('#dohSub').textContent=view.scope+' · original demand, retained seasonal peaks, adjusted rate and days of cover';
  $('#dohTrace').html=`<option value="-1">All relevant town–parents (${view.pairs.length})</option>`+view.pairs.map((p,i)=>`<option value="${i}">${esc(p.t)} · parent ${esc(p.p)}${p.rate==null?' · missing source':''}</option>`).join('');
  $('#dohTrace').value='-1';drawDohExplanation();$('#dohBody').scrollTop=0;$('#doh').hidden=false;$('#dohClose').focus();
}
function openStatusDoh(row,opener){
  const selected=new Set(STATUS.groups),pairs=statusDohPairs(row,statusView,statusPairs());
  const title=(row.path||[row.town,row.brand,row.name].filter(Boolean)).join(' · ');
  openDohExplanation({title,scope:'Latest all-stock cohort',rslLabel:[...selected].join(', '),pairs,cover:dohSummary(pairs,selected)},opener);
}
function openBatchDoh(row,opener){
  const pair={t:row.Town,p:String(row.parent_material_code),rate:row.avg_sec_m,stock:{[row.rsl_group]:row.total_closing_stock_kl}};
  const selected=new Set([row.rsl_group]);
  openDohExplanation({title:row['Brand Name']+' · '+row['Batch code'],scope:'This batch only · '+row.Town,rslLabel:row.rsl_group,pairs:[pair],cover:dohSummary([pair],selected)},opener);
}
function closeDohExplanation(){
  $('#doh').hidden=true;dohView=null;if(dohOpener?.isConnected)dohOpener.focus({preventScroll:true});dohOpener=null;
}
$('#dohTrace').onchange=()=>{drawDohExplanation();$('#dohBody').scrollTop=0};
const dohPick=i=>{$('#dohTrace').value=String(i);drawDohExplanation();$('#dohBody').scrollTop=0};
$('#dohBody').addEventListener('change',e=>{if(e.target.id==='dohAll'){dohShowAll=e.target.checked;drawDohExplanation()}});
$('#dohBody').addEventListener('click',e=>{const r=e.target.closest('[data-doh-pick]');if(r)dohPick(r.dataset.dohPick)});
$('#dohBody').addEventListener('keydown',e=>{const r=e.target.closest?.('[data-doh-pick]');if(r&&(e.key==='Enter'||e.key===' ')){e.preventDefault();dohPick(r.dataset.dohPick)}});
$('#dohBody').addEventListener('pointermove',e=>{if(e.target.closest('.doh-chart-scroll'))dohTipShow(e.clientX);else dohTipHide()});
$('#dohBody').addEventListener('pointerleave',dohTipHide);
$$('#doh [data-doh-close]').forEach(b=>b.onclick=closeDohExplanation);
document.addEventListener('click',e=>{
  const button=e.target.closest('button.doh-value')||e.target.closest('td')?.querySelector('button.doh-value');if(!button)return;e.stopPropagation();
  if(button.hasAttribute('data-doh-batch')){const row=BR.find(r=>r.batch_index===button.dataset.dohBatch);if(row)openBatchDoh(row,button);return}
  if(button.hasAttribute('data-doh-status')){const row=statusNodes.get(button.dataset.dohStatus);if(row)openStatusDoh(row,button)}
},true);
addEventListener('keydown',e=>{
  if($('#doh').hidden)return;
  if(e.key==='Escape'){e.preventDefault();closeDohExplanation();return}
  if(e.key!=='Tab')return;
  const targets=$$('button,select,summary,[tabindex="0"]',$('#doh')).filter(el=>el.getClientRects().length),first=targets[0],last=targets.at(-1);
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
});
