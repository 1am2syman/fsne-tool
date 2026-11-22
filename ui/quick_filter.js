/* Quick filter: select cells or type a query in a sheet's formula bar to set that report's value filters.
 * Every result is an ordinary FSNEFilters.Group state (one value set per field), so the dropdowns, exports and
 * drill-downs always agree with what the bar shows. Pure parsing is exported for tests; the controller needs a DOM. */
(function(root){
'use strict';
const norm=s=>String(s??'').toLowerCase().trim();
const compact=s=>norm(s).replace(/[^\p{L}\p{N}]/gu,'');
const words=s=>norm(s).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
const text=raw=>({lower:norm(raw),compact:compact(raw),words:words(raw)});
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
const BLANK='(Blanks)';

/* ---------- fuzzy matching ---------- */
function oneEdit(a,b){
  if(a===b)return true;const la=a.length,lb=b.length;if(Math.abs(la-lb)>1)return false;
  let i=0;while(i<la&&i<lb&&a[i]===b[i])i++;
  if(la===lb)return a.slice(i+1)===b.slice(i+1)||(a[i]===b[i+1]&&a[i+1]===b[i]&&a.slice(i+2)===b.slice(i+2));
  return la>lb?a.slice(i+1)===b.slice(i):a.slice(i)===b.slice(i+1);
}
function subsequence(q,t){let j=0,gaps=0,last=-1;for(let i=0;i<t.length&&j<q.length;i++)if(t[i]===q[j]){if(last>=0)gaps+=i-last-1;last=i;j++}return j===q.length?Math.max(1,60-gaps):0}
/* Tiers: 1000 exact, 850 prefix, 760 word start, 740/660 all words, 640 contains, 480 one typo, 200+ letters in order. */
function score(q,e){
  if(!q.compact)return 0;
  if(e.lower===q.lower||e.compact===q.compact)return 1000;
  let s=0;
  if(e.lower.startsWith(q.lower)||e.compact.startsWith(q.compact))s=850;
  else if(q.words.length>1&&q.words.every(w=>e.words.some(x=>x.startsWith(w))))s=740;
  else if(e.words.some(w=>w.startsWith(q.compact)))s=760;
  else if(q.words.length>1&&q.words.every(w=>e.lower.includes(w)))s=660;
  else if(q.compact.length>=2&&e.compact.includes(q.compact))s=640;
  else if(q.compact.length>=4&&(oneEdit(q.compact,e.compact)||e.words.some(w=>w.length>=4&&oneEdit(q.compact,w))||oneEdit(q.compact,e.compact.slice(0,q.compact.length))))s=480;
  else if(q.compact.length>=3){const k=subsequence(q.compact,e.compact);if(k)s=200+k}
  // Punctuation the user typed (e.g. "actv+") prefers names that contain it literally.
  if(s&&s<1000&&/[^\p{L}\p{N}\s]/u.test(q.lower)&&e.lower.includes(q.lower))s+=60;
  return s;
}
function tier(list){if(!list.length)return [];const best=list[0].s,min=best>=640?640:best>=480?480:best;return list.filter(x=>x.s>=min).slice(0,80)}
const WEIGHT={brand:30,town:20,category:10,parent:0};
const ALIASES={brand:['b','brand','brands'],parent:['p','parent','sku','parent sku','material'],town:['t','town','towns'],category:['c','cat','category','stock category']};

/* ---------- vocabulary: the values each field can take in this report ---------- */
function vocabulary(defs,rows){
  const dims=new Map(),aliases=new Map();
  for(const {key,label} of defs){
    const values=new Map();
    for(const r of rows){
      const v=String(r[key]??'');let e=values.get(v);
      if(!e){
        const name=v===''?BLANK:key==='parent'&&r.pdesc&&r.pdesc!==v?String(r.pdesc):v;
        const full=key==='parent'&&name!==v?name+' · '+v:name;
        e={value:v,name,label:full,weight:0,lowerLabel:norm(full),texts:[...new Set([name,v,full].filter(Boolean))].map(text)};
        values.set(v,e);
      }
      e.weight++;
    }
    dims.set(key,{key,label,values,list:[...values.values()].sort((a,b)=>b.weight-a.weight)});
    for(const a of [...(ALIASES[key]||[]),key,norm(label)])if(!aliases.has(a))aliases.set(a,key);
  }
  return {defs,dims,aliases,order:defs.map(d=>d.key)};
}
const dimLabel=(V,d)=>V.dims.get(d)?.label||d;
const valueName=(V,d,v)=>V.dims.get(d)?.values.get(v)?.name??(v===''?BLANK:v);
const valueLabel=(V,d,v)=>V.dims.get(d)?.values.get(v)?.label??(v===''?BLANK:v);
const allValues=(V,d)=>V.dims.get(d)?.list.map(e=>e.value)||[];
function fuzzy(V,raw,dims=V.order){
  const q=text(raw),out={};
  for(const d of dims){
    const list=[];
    for(const e of V.dims.get(d).list){let s=0;for(const t of e.texts)s=Math.max(s,score(q,t));if(s)list.push({v:e.value,s,w:e.weight})}
    out[d]=list.sort((a,b)=>b.s-a.s||b.w-a.w);
  }
  return out;
}

/* ---------- query language ----------
 * "a, b" = a or b (one field) · "a+b" = and (spaces optional) · "!a" or "-a" = not ·
 * "town:raj" forces a field · "\"Exact\"" names one value; quotes also keep a literal "+" or "," inside a name. */
function scan(q){
  const groups=[];let g={start:0,parts:[]},ps=0,quoted=false;
  const endPart=i=>g.parts.push({raw:q.slice(ps,i),start:ps,end:i});
  for(let i=0;i<=q.length;i++){
    const ch=q[i];
    if(i===q.length){endPart(i);g.end=i;groups.push(g);break}
    if(ch==='"'){quoted=!quoted;continue}
    if(quoted)continue;
    if(ch==='+'){endPart(i);g.end=i;groups.push(g);ps=i+1;g={start:i+1,parts:[]};continue}
    if(ch===','){endPart(i);ps=i+1}
  }
  return groups;
}
function resolve(V,raw,override){
  let s=raw.trim();if(!s)return null;
  const P={raw,neg:false};
  if(s[0]==='!'||(s[0]==='-'&&s.length>1&&!/\s/.test(s[1]))){P.neg=true;s=s.slice(1).trim()}
  const m=/^([a-z][a-z ]{0,20}):\s*(.*)$/i.exec(s);
  if(m&&V.aliases.has(norm(m[1]))){P.force=V.aliases.get(norm(m[1]));s=m[2].trim()}
  P.core=s;if(!s)return {...P,kind:'empty'};
  const dims=P.force?[P.force]:V.order;
  if(s.length>1&&s[0]==='"'&&s.endsWith('"')){
    const t=norm(s.slice(1,-1));
    for(const d of dims){
      const vals=V.dims.get(d).list.filter(e=>e.lowerLabel===t||e.texts.some(x=>x.lower===t)).map(e=>e.value);
      if(vals.length)return {...P,kind:'exact',dim:d,vals:new Set(vals),label:vals.length===1?valueName(V,d,vals[0]):s.slice(1,-1)+' ('+vals.length+')'};
    }
    return {...P,kind:'none',label:'no exact match for '+s};
  }
  const word=s.replace(/^"/,'');if(!compact(word))return {...P,kind:'empty'};
  const found=fuzzy(V,word,dims),picks={};let best=null,bestScore=-1;
  for(const d of dims){picks[d]=tier(found[d]);if(picks[d].length){const sc=picks[d][0].s+(WEIGHT[d]??10);if(sc>bestScore){bestScore=sc;best=d}}}
  const chosen=override?.get(norm(word));if(!P.force&&chosen&&picks[chosen]?.length)best=chosen;
  if(!best)return {...P,kind:'none',label:'no match for “'+word+'”'};
  const pick=picks[best],vals=new Set(pick.map(x=>x.v));
  const others=Object.fromEntries(dims.filter(d=>d!==best&&picks[d].length&&picks[d][0].s>=480).map(d=>[d,picks[d].length]));
  return {...P,kind:'fuzzy',dim:best,vals,word,typo:pick[0].s===480,others,label:vals.size===1?valueName(V,best,[...vals][0]):word+' → '+vals.size};
}
function compile(V,q,override){
  const groups=scan(q),res={groups:[],hints:[],state:{},empty:[]},positive=new Map(),negative=new Map();
  groups.forEach((g,gi)=>{
    const parts=g.parts.map(p=>({...p,res:resolve(V,p.raw,override)})).filter(p=>p.res&&p.res.kind!=='empty');
    const G={start:g.start,end:g.end,parts};res.groups.push(G);
    const ok=parts.filter(p=>p.res.kind!=='none');
    for(const p of ok.filter(p=>p.res.neg)){const set=negative.get(p.res.dim)||new Set();p.res.vals.forEach(v=>set.add(v));negative.set(p.res.dim,set)}
    const pos=ok.filter(p=>!p.res.neg),dims=[...new Set(pos.map(p=>p.res.dim))];
    if(dims.length>1){G.mixed=true;res.hints.push({type:'mixed',group:gi,text:'“,” means or inside one field — '+dims.map(d=>dimLabel(V,d)).join(' and ')+' are different fields.'});return}
    if(!dims.length)return;
    const d=dims[0],union=new Set(pos.flatMap(p=>[...p.res.vals])),prior=positive.get(d);
    if(!prior){positive.set(d,{set:union,group:gi});return}
    prior.set=new Set([...prior.set].filter(v=>union.has(v)));
    if(!prior.set.size)res.hints.push({type:'onedim',dim:d,first:prior.group,second:gi,text:'A row has one '+dimLabel(V,d).toLowerCase()+', so “+” between two of them never matches.'});
  });
  for(const d of V.order){
    const pos=positive.get(d)?.set,neg=negative.get(d);
    if(!pos&&!neg)continue;
    const all=allValues(V,d),set=new Set(pos||all);neg?.forEach(v=>set.delete(v));
    if(set.size===all.length&&all.every(v=>set.has(v)))continue;
    res.state[d]=set;if(!set.size)res.empty.push(d);
  }
  return res;
}
const groupTexts=q=>scan(q).map(g=>q.slice(g.start,g.end).trim());
function mergeFix(q,first,second){const parts=groupTexts(q);parts[first]+=', '+parts[second];parts.splice(second,1);return parts.filter(Boolean).join(' + ')}
function splitFix(V,q,group,override){
  const parts=groupTexts(q),g=scan(q)[group],byDim=new Map();
  for(const p of g.parts){const r=resolve(V,p.raw,override);if(!r||r.kind==='empty')continue;const k=r.dim||'?';byDim.set(k,[...(byDim.get(k)||[]),p.raw.trim()])}
  parts[group]=[...byDim.values()].map(list=>list.join(', ')).join(' + ');
  return parts.filter(Boolean).join(' + ');
}
/* A token names one value exactly; the field prefix is added only when an earlier field has the same text. */
function token(V,d,v){
  const label=valueLabel(V,d,v),t=norm(label);
  const earlier=V.order.slice(0,V.order.indexOf(d)).some(o=>V.dims.get(o).list.some(e=>e.lowerLabel===t||e.texts.some(x=>x.lower===t)));
  const alias=(ALIASES[d]||[d]).find(a=>a.length>1&&!a.includes(' '))||d;
  return (earlier?alias+':':'')+'"'+label+'"';
}
/* One chip per field. A field that keeps most values is written as exclusions, so "!Incorrect stock" stays short. */
function serialize(V,state){
  const chips=[];
  for(const d of V.order){
    const set=state[d];if(set==null)continue;
    // Values without rows here (e.g. a default category no row has) do not change the result, so they do not block the short form.
    const all=allValues(V,d),present=[...set].filter(v=>V.dims.get(d).values.has(v)),rest=all.filter(v=>!set.has(v));
    const names=vals=>vals.slice(0,1).map(v=>valueName(V,d,v)).join('')+(vals.length>1?' +'+(vals.length-1):'');
    if(present.length>1&&rest.length&&rest.length<present.length)chips.push({dim:d,neg:true,text:rest.map(v=>'!'+token(V,d,v)).join(', '),label:'Not '+dimLabel(V,d)+': '+names(rest),title:rest.map(v=>valueLabel(V,d,v)).join(', ')});
    else if(set.size)chips.push({dim:d,neg:false,text:[...set].map(v=>token(V,d,v)).join(', '),label:dimLabel(V,d)+': '+names([...set]),title:[...set].map(v=>valueLabel(V,d,v)).join(', ')});
    else chips.push({dim:d,neg:false,text:'',label:dimLabel(V,d)+': none',title:'No '+dimLabel(V,d).toLowerCase()+' selected'});
  }
  return chips;
}
const serializeText=(V,state)=>serialize(V,state).map(c=>c.text).filter(Boolean).join(' + ');
const cloneState=s=>Object.fromEntries(Object.entries(s||{}).filter(([,v])=>v!=null).map(([k,v])=>[k,new Set(v)]));
/* Selection → state. Items carry {facts:{field:value}, primary:field|null}. */
function unionOf(items){const u={};for(const it of items)for(const [d,v] of Object.entries(it.facts))(u[d]||(u[d]=new Set())).add(v);return u}
function keepState(base,items){const s=cloneState(base);for(const [d,vals] of Object.entries(unionOf(items)))s[d]=new Set(vals);return s}
function addState(base,items){const s=cloneState(base);for(const [d,vals] of Object.entries(unionOf(items)))if(s[d])vals.forEach(v=>s[d].add(v));return s}
function excludeState(V,base,items){
  const s=cloneState(base);
  for(const it of items){
    const d=it.primary;if(d==null)continue;
    const all=allValues(V,d),set=s[d]||new Set(all);set.delete(it.facts[d]);
    s[d]=set.size===all.length&&all.every(v=>set.has(v))?null:set;if(s[d]==null)delete s[d];
  }
  return s;
}

const api={norm,score,vocabulary,fuzzy,scan,resolve,compile,mergeFix,splitFix,token,serialize,serializeText,keepState,addState,excludeState,unionOf,valueName,valueLabel,dimLabel};
if(typeof module!=='undefined')module.exports=api;
if(typeof document==='undefined'){root.FSNEQuick=api;return}

/* ======================= controller (browser only) ======================= */
const $=(s,el=document)=>el.querySelector(s),$$=(s,el=document)=>[...el.querySelectorAll(s)];
const isMac=/Mac|iPhone|iPad/.test(navigator.platform||navigator.userAgent);
const MOD=isMac?'⌘':'Ctrl';
const kbd=k=>`<kbd>${k}</kbd>`;
/* The only HTML sink: callers build markup from constants and esc()-escaped data values (names, codes, counts). */
const parser=typeof DOMParser==='undefined'?null:new DOMParser();
const setHTML=(el,markup)=>el.replaceChildren(...parser.parseFromString(markup,'text/html').body.childNodes);
const bindings=[];
let editing=null,toast=()=>{},fDown=null,sugg=null,sIdx=-1,items=[],guide=null,guideTab='try',liveTimer=0;

function vocab(b){
  const rows=b.group.source();
  if(b.vocab&&b.vocabRows===rows.length&&b.vocabFirst===rows[0]&&b.vocabLast===rows.at(-1))return b.vocab;
  b.vocabRows=rows.length;b.vocabFirst=rows[0];b.vocabLast=rows.at(-1);
  return b.vocab=vocabulary(b.group.defs,rows);
}
const count=(b,state)=>b.group.source().filter(r=>root.FSNEFilters.matches(r,state)).length;
const visible=b=>b.sheet.getClientRects().length>0;
const modal=()=>document.querySelector('dialog[open]');
const sheetRows=b=>b.sheetState()?.rows||[];
const pathOf=(b,row)=>(row?b.path(row):[]).filter(([,v])=>v!=null).map(([d,v])=>[d,String(v)]);
const pathKey=path=>JSON.stringify(path);

function item(b,r,c,own){
  const row=sheetRows(b)[r];if(!row)return null;
  const path=pathOf(b,row);if(!path.length)return null;
  const colDim=c>=0?b.colDim?.(c):null,V=vocab(b);
  let facts=path;
  if(colDim){const hit=path.find(([d])=>d===colDim);if(hit)facts=[hit]}
  else if(own)facts=path.slice(-1);
  const primary=colDim||own||b.tree?facts.at(-1)[0]:null;
  return {key:pathKey(path)+'|'+c+(own?'|own':''),path:pathKey(path),c,facts:Object.fromEntries(facts),primary,
    label:facts.map(([d,v])=>valueName(V,d,v)).join(' · ')};
}
function toggle(b,r,c,own){
  const it=item(b,r,c,own);if(!it){toast('This row has no Brand, Parent SKU or Town to filter on');return}
  if(b.sel.has(it.key))b.sel.delete(it.key);else b.sel.set(it.key,it);
}
function range(b,from,to){
  b.sel.clear();
  const r1=Math.min(from.r,to.r),r2=Math.max(from.r,to.r),c1=Math.min(from.c,to.c),c2=Math.max(from.c,to.c);
  for(let r=r1;r<=r2&&b.sel.size<2000;r++)for(let c=c1;c<=c2;c++){const it=item(b,r,c,false);if(it)b.sel.set(it.key,it)}
}
function paint(b){
  $$('.qf-sel',b.T).forEach(x=>x.classList.remove('qf-sel'));$$('tr.qf-dim',b.T).forEach(x=>x.classList.remove('qf-dim'));
  const rows=sheetRows(b),trs=b.T.tBodies[0].rows,index=new Map();
  rows.forEach((row,i)=>index.set(pathKey(pathOf(b,row)),i));
  for(const it of b.sel.values()){const i=index.get(it.path);const td=i!=null&&trs[i]?.cells[it.c+1];if(td)td.classList.add('qf-sel')}
  if(b.sel.size&&editing!==b&&!b.group.previewing){
    const u=unionOf(b.sel.values());
    rows.forEach((row,i)=>{if(pathOf(b,row).some(([d,v])=>u[d]&&!u[d].has(v)))trs[i]?.classList.add('qf-dim')});
  }
  b.fx.classList.toggle('qf-has-sel',b.sel.size>0&&editing!==b);
}
function example(b){
  const V=vocab(b);
  return V.example??=buildExample(V);
}
function buildExample(V){
  const frag=(d,i=0)=>{const list=(V.dims.get(d)?.list||[]).slice(0,40);const seen=new Set();for(const e of list){const w=words(e.name).find(x=>x.length>=3&&!/^\d+$/.test(x));if(!w||seen.has(w))continue;const r=resolve(V,w);if(r?.kind==='fuzzy'&&r.dim===d){seen.add(w);if(seen.size>i)return w}}return null};
  const brands=[frag('brand',0),frag('brand',1)].filter(Boolean),town=frag('town',0);
  return [brands.join(', '),town].filter(Boolean).join(' + ')||'type a brand, parent or town';
}
function drawIdle(b){
  if(editing===b)return;
  const V=vocab(b),chips=serialize(V,b.group.committed());
  let h='';
  if(b.sel.size){
    const sel=[...b.sel.values()],u=unionOf(sel),n=count(b,keepState(b.group.committed(),sel));
    const parts=Object.entries(u).map(([d,s])=>`${esc(dimLabel(V,d))}: ${esc([...s].slice(0,2).map(v=>valueName(V,d,v)).join(', '))}${s.size>2?' +'+(s.size-2):''}`);
    h+=`<span class="qf-hud"><b>Keep</b> ${parts.join(' · ')} → <b>${n.toLocaleString('en-US')}</b> ${esc(b.noun)}</span><span class="qf-keys">${kbd('F')} keep ${kbd('X')} exclude ${kbd('+')} add ${kbd('Esc')}</span>`;
  }
  h+=chips.map((c,i)=>`<span class="qf-chip${c.neg?' neg':''}" data-chip="${i}" title="${esc(c.title)} — click to edit">${esc(c.label)}<button type="button" data-rm="${i}" aria-label="Remove ${esc(c.label)}">×</button></span>`).join('');
  if(chips.length)h+=`<button type="button" class="qf-chip qf-clear" data-clear title="Clear all filters (Shift+Backspace)">Clear</button>`;
  else if(!b.sel.size)h+=`<span class="qf-ph">${kbd('/')} quick filter · e.g. <code>${esc(example(b))}</code></span>`;
  setHTML(b.qf,h);
  paint(b);
}
function bindingAt(el){const sheet=el?.closest?.('.sheet');return bindings.find(b=>b.sheet===sheet)||null}
function activeBinding(target){
  const own=bindingAt(target),dlg=modal();
  if(own)return !dlg||dlg.contains(own.sheet)?own:null;
  return bindings.find(b=>visible(b)&&(!dlg||dlg.contains(b.sheet)))||null;
}
function focusSheet(b){b.wrap.focus({preventScroll:true})}

/* ---------- committing ---------- */
function commit(b,state,label,{fly,keep}={}){
  const g=b.group,before=g.snapshot(g.committed());
  g.apply(state);
  if(g.snapshot(state)===before){toast('No change — the filter already looks like that');return false}
  if(keep)b.last={before,after:g.snapshot(state),on:true};else b.last=null;
  toast(label);
  const old=g.restore(before),key=s=>JSON.stringify(s?[...s].sort():null);
  const changed=[...new Set([...Object.keys(state),...Object.keys(old)])].filter(d=>key(state[d])!==key(old[d]));
  if(fly)requestAnimationFrame(()=>flyTo(b,fly,changed));
  return true;
}
function act(b,kind){
  const sel=[...b.sel.values()],V=vocab(b),base=b.group.committed(),fly=sel.slice(0,8).map(it=>({label:it.label,rect:cellRect(b,it)})).filter(x=>x.rect);
  const names=sel.slice(0,3).map(it=>it.label).join(', ')+(sel.length>3?' …':'');
  let state,label;
  if(kind==='keep'){state=keepState(base,sel);label='Kept '+names+' — F again shows everything'}
  if(kind==='add'){state=addState(base,sel);label='Added '+names+' to the filter'}
  if(kind==='exclude'){
    const usable=sel.filter(it=>it.primary);
    if(!usable.length){toast('X excludes one value — Ctrl+click a Town, Brand or Material cell');return}
    state=excludeState(V,base,usable);
    const empty=Object.entries(state).find(([,s])=>s&&!s.size);if(empty){toast('That would hide every '+dimLabel(V,empty[0]).toLowerCase());return}
    label='Excluded '+usable.slice(0,3).map(it=>valueName(V,it.primary,it.facts[it.primary])).join(', ');
  }
  b.sel.clear();
  if(!commit(b,state,label,{fly:fly.map(f=>({...f,neg:kind==='exclude'})),keep:kind==='keep'}))drawIdle(b);
}
function cellRect(b,it){
  const rows=sheetRows(b),i=rows.findIndex(row=>pathKey(pathOf(b,row))===it.path);
  return i>=0?b.T.tBodies[0].rows[i]?.cells[it.c+1]?.getBoundingClientRect():null;
}
function removeChip(b,i){
  const V=vocab(b),chips=serialize(V,b.group.committed()),c=chips[i];if(!c)return;
  const s=cloneState(b.group.committed());delete s[c.dim];
  commit(b,s,'Removed '+c.label+' — Ctrl+Z brings it back');
}
function clearAll(b){
  if(!Object.keys(b.group.committed()).some(k=>b.group.committed()[k]!=null)){toast('No filters to clear');return}
  commit(b,{},'Cleared all filters — Ctrl+Z brings them back');
}
function undo(b,redo){
  const ok=redo?b.group.redo():b.group.undo();b.last=null;
  toast(ok?(redo?'Redo':'Undo')+': '+(serialize(vocab(b),b.group.committed()).map(c=>c.label).join(' · ')||'no filters'):redo?'Nothing to redo':'Nothing to undo');
  drawIdle(b);
}

/* ---------- chip flight ---------- */
function flyTo(b,sources,dims){
  const targets=[...new Set(dims)].map(d=>b.group.controls.find(c=>c.key===d)?.button).filter(t=>t&&t.getClientRects().length);
  if(!targets.length||!sources.length)return;
  const layer=modal()||document.body;
  sources.forEach((src,i)=>{
    const t=targets[i%targets.length].getBoundingClientRect(),el=document.createElement('div');
    el.className='qf-fly'+(src.neg?' neg':'');el.textContent=src.label.length>28?src.label.slice(0,27)+'…':src.label;layer.append(el);
    const x=src.rect.left+src.rect.width/2,y=src.rect.top+src.rect.height/2;el.style.left=x+'px';el.style.top=y+'px';
    requestAnimationFrame(()=>requestAnimationFrame(()=>{el.style.transitionDelay=i*40+'ms';el.style.transform=`translate(calc(-50% + ${t.left+t.width/2-x}px),calc(-50% + ${t.top+t.height/2-y}px)) scale(.55)`;el.style.opacity='.1'}));
    setTimeout(()=>el.remove(),1000);
  });
  setTimeout(()=>targets.forEach(t=>{t.classList.remove('qf-pulse');void t.offsetWidth;t.classList.add('qf-pulse')}),560);
}

/* ---------- formula-bar editing ---------- */
function seed(b){
  const V=vocab(b),chips=serialize(V,b.group.committed());
  if(!b.sel.size)return chips.map(c=>c.text).filter(Boolean).join(' + ');
  const u=unionOf(b.sel.values());
  return [...chips.filter(c=>!u[c.dim]).map(c=>c.text),...Object.entries(u).map(([d,s])=>[...s].map(v=>token(V,d,v)).join(', '))].filter(Boolean).join(' + ');
}
function openBar(b,{append,chip}={}){
  if(editing&&editing!==b)closeBar(editing,false);
  if(editing===b){$('.qf-input',b.qf)?.focus();return}
  b.vocab=null;vocab(b);
  let q=seed(b);b.sel.clear();
  if(append!=null)q=q?q+' + '+append:append;
  editing=b;b.override=b.override||new Map();
  b.fx.classList.add('qf-editing');b.fx.classList.remove('qf-has-sel');
  setHTML(b.qf,`<div class="qf-edit"><input class="qf-input" spellcheck="false" autocomplete="off" aria-label="Quick filter query" placeholder="Brand, parent or town — “,” or · “+” and · “!” not"><div class="qf-pills" aria-live="polite"></div></div><div class="qf-readout"></div>`);
  const input=$('.qf-input',b.qf);input.value=q;input.focus();
  const g=chip!=null&&scan(q)[chip];
  if(g)input.setSelectionRange(g.start+(q[g.start]===' '?1:0),g.end);else input.setSelectionRange(q.length,q.length);
  // Heavy reports redraw slowly, so the preview waits for a typing pause at least as long as the last redraw.
  input.oninput=()=>{sIdx=-1;clearTimeout(liveTimer);liveTimer=setTimeout(()=>live(b),Math.min(400,Math.max(70,b.cost||0)));suggest(b)};
  input.onkeydown=e=>barKey(b,e);
  input.onclick=input.onkeyup=e=>{if(!['ArrowDown','ArrowUp','Enter','Tab','Escape'].includes(e.key))suggest(b)};
  input.onblur=()=>setTimeout(()=>{if(editing===b&&document.activeElement!==input&&!sugg?.matches(':hover'))closeBar(b,false)},150);
  live(b);paint(b);
}
function live(b){
  const input=$('.qf-input',b.qf);if(!input||editing!==b)return;
  b.res=compile(vocab(b),input.value,b.override);
  const key=b.group.snapshot(b.res.state);
  if(key!==b.previewKey){const t=performance.now();b.group.preview(b.res.state);b.cost=performance.now()-t;b.previewKey=key}
  drawPills(b);suggest(b);
}
function zeroHint(b,state){
  const V=vocab(b);let best=null;
  for(const d of Object.keys(state)){const s={...state};delete s[d];const n=count(b,s);if(n&&(!best||n>best.n))best={d,n}}
  return best?` Removing ${esc(dimLabel(V,best.d))} gives ${best.n.toLocaleString('en-US')}.`:'';
}
function drawPills(b){
  const V=vocab(b),res=b.res,el=$('.qf-pills',b.qf);if(!el)return;
  let h='';
  res.groups.forEach(g=>{
    if(!g.parts.length)return;
    if(h)h+='<span class="qf-op">AND</span>';
    h+=g.parts.map(p=>{
      const r=p.res,cls=r.kind==='none'?' err':r.neg?' neg':g.mixed?' err':'';
      const dim=r.dim?`<em>${esc(dimLabel(V,r.dim))}${r.kind==='fuzzy'&&r.vals.size>1?' ▾':''}:</em> `:'';
      const typo=r.typo?' <em title="Matched despite a typo">~</em>':'';
      const others=r.kind==='fuzzy'&&!r.force&&Object.keys(r.others).length?` <em title="Tab switches field">⇥ ${Object.entries(r.others).map(([d,n])=>n+' '+esc(dimLabel(V,d).toLowerCase())).join(', ')}</em>`:'';
      return `<span class="qf-pill${cls}">${r.neg?'NOT ':''}${dim}${esc(r.label||r.core)}${typo}${others}</span>`;
    }).join('<span class="qf-op">or</span>');
  });
  for(const hint of res.hints)h+=`<span class="qf-hint">⚠ ${esc(hint.text)} ${kbd('Tab')} <button type="button" data-fix>${hint.type==='onedim'?'use “,” instead':'split with “+”'}</button></span>`;
  const n=count(b,res.state),total=b.group.source().length;
  if(!n&&res.groups.some(g=>g.parts.length))h+=`<span class="qf-hint">No ${esc(b.noun)} match.${zeroHint(b,res.state)}</span>`;
  if(!res.groups.some(g=>g.parts.length))h+=`<span class="qf-tip">Type brands, parents or towns · <code>,</code> or · <code>+</code> and · <code>!</code> not · ${kbd('Enter')} apply · ${kbd('Esc')} cancel</span>`;
  setHTML(el,h);
  const fix=$('[data-fix]',el);if(fix)fix.onmousedown=e=>{e.preventDefault();applyFix(b)};
  setHTML($('.qf-readout',b.qf),`<b>${n.toLocaleString('en-US')}</b> of ${total.toLocaleString('en-US')} ${esc(b.noun)}<small>live preview</small>`);
}
function applyFix(b){
  const hint=b.res?.hints[0];if(!hint)return false;
  const input=$('.qf-input',b.qf);
  input.value=hint.type==='onedim'?mergeFix(input.value,hint.first,hint.second):splitFix(vocab(b),input.value,hint.group,b.override);
  input.setSelectionRange(input.value.length,input.value.length);live(b);
  toast(hint.type==='onedim'?'Changed “+” to “,” — either value now matches':'Split into separate “+” groups');
  return true;
}
function partAtCaret(input){
  const pos=input.selectionStart;
  for(const g of scan(input.value))for(const p of g.parts)if(pos>=p.start&&pos<=p.end)return p;
  return null;
}
function cycleField(b){
  const input=$('.qf-input',b.qf),p=partAtCaret(input),r=p&&resolve(vocab(b),p.raw,b.override);
  if(!r||r.kind!=='fuzzy'){toast('Tab switches the field of a typed word; this one is exact');return}
  const options=[r.dim,...Object.keys(r.others)];if(options.length<2){toast('“'+r.word+'” only matches '+dimLabel(vocab(b),r.dim));return}
  const next=options.length>1?options[1]:r.dim;
  // Keep cycling in field order: the current choice moves to the back.
  const order=vocab(b).order.filter(d=>options.includes(d)),to=order[(order.indexOf(r.dim)+1)%order.length]||next;
  b.override.set(norm(r.word),to);live(b);toast('“'+r.word+'” now means '+dimLabel(vocab(b),to));
}
function closeBar(b,keepPreview){
  clearTimeout(liveTimer);editing=null;hideSuggest();b.previewKey=null;
  b.fx.classList.remove('qf-editing');
  if(!keepPreview)b.group.endPreview();
  drawIdle(b);
}
function commitBar(b){
  const input=$('.qf-input',b.qf);const V=vocab(b),res=compile(V,input.value,b.override);
  if(res.empty.length){toast('Nothing is left for '+res.empty.map(d=>dimLabel(V,d)).join(', ')+' — change the query');return}
  const fly=$$('.qf-pill:not(.err)',b.qf).slice(0,8).map(el=>({rect:el.getBoundingClientRect(),label:el.textContent.replace(/\s*⇥.*$/,''),neg:el.classList.contains('neg')}));
  closeBar(b,true);
  if(!commit(b,res.state,'Applied: '+(serialize(V,res.state).map(c=>c.label).join(' · ')||'no filters'),{fly}))drawIdle(b);
  focusSheet(b);
}
function barKey(b,e){
  if(e.key==='ArrowDown'&&items.length){e.preventDefault();sIdx=Math.min(items.length-1,sIdx+1);suggest(b);return}
  if(e.key==='ArrowUp'&&items.length){e.preventDefault();sIdx=Math.max(-1,sIdx-1);suggest(b);return}
  if(e.key==='Enter'){e.preventDefault();if(sIdx>=0&&sugg&&!sugg.hidden){pick(b,sIdx);return}clearTimeout(liveTimer);commitBar(b);return}
  if(e.key==='Escape'){e.preventDefault();e.stopPropagation();if(sIdx>=0){sIdx=-1;suggest(b);return}closeBar(b,false);focusSheet(b);toast('Cancelled — filters restored');return}
  if(e.key==='Tab'){e.preventDefault();clearTimeout(liveTimer);live(b);if(applyFix(b))return;cycleField(b)}
}

/* ---------- suggestions ---------- */
function hideSuggest(){if(sugg)sugg.hidden=true;items=[];sIdx=-1}
function suggest(b){
  const input=$('.qf-input',b.qf);if(!input||editing!==b){hideSuggest();return}
  const V=vocab(b),p=partAtCaret(input);items=[];
  let raw=p?p.raw.trim().replace(/^[!-]/,'').trim():'',dims=V.order;
  const m=/^([a-z][a-z ]{0,20}):\s*(.*)$/i.exec(raw);if(m&&V.aliases.has(norm(m[1]))){dims=[V.aliases.get(norm(m[1]))];raw=m[2]}
  raw=raw.replace(/^"|"$/g,'');
  if(!compact(raw)){hideSuggest();return}
  const found=fuzzy(V,raw,dims);let h='';
  for(const d of dims){
    const list=found[d].slice(0,d==='parent'?6:4);if(!list.length)continue;
    h+=`<div class="qf-sg-h"><span>${esc(dimLabel(V,d))}</span><span>${found[d].length} match${found[d].length===1?'':'es'}</span></div>`;
    for(const x of list){items.push({d,v:x.v,p});const e=V.dims.get(d).values.get(x.v);
      h+=`<div class="qf-sg-i${items.length-1===sIdx?' on':''}" data-i="${items.length-1}"><span>${esc(e.name)}${d==='parent'&&e.name!==e.value?` <small>${esc(e.value)}</small>`:''}</span><small>${e.weight.toLocaleString('en-US')} ${esc(b.noun)}</small></div>`}
  }
  if(!items.length){hideSuggest();return}
  h+=`<div class="qf-sg-f"><span>${kbd('↑')}${kbd('↓')} pick</span><span>${kbd('Enter')} ${sIdx>=0?'use pick':'apply'}</span><span>${kbd('Tab')} switch field</span><span>${kbd('Esc')} cancel</span></div>`;
  if(!sugg){sugg=document.createElement('div');sugg.className='qf-sugg';sugg.setAttribute('role','listbox')}
  const layer=modal()||document.body;if(sugg.parentNode!==layer)layer.append(sugg);
  setHTML(sugg,h);sugg.hidden=false;
  $$('.qf-sg-i',sugg).forEach(el=>el.onmousedown=e=>{e.preventDefault();pick(b,+el.dataset.i)});
  $('.qf-sg-i.on',sugg)?.scrollIntoView({block:'nearest'});
  const rect=input.getBoundingClientRect(),ctx=document.createElement('canvas').getContext('2d');ctx.font=getComputedStyle(input).font;
  const x=rect.left+ctx.measureText(input.value.slice(0,p?p.start:0)).width-input.scrollLeft;
  sugg.style.left=Math.max(8,Math.min(x,innerWidth-sugg.offsetWidth-8))+'px';sugg.style.top=b.fx.getBoundingClientRect().bottom+4+'px';
}
function pick(b,i){
  const it=items[i];if(!it)return;const input=$('.qf-input',b.qf),p=it.p,V=vocab(b);
  const lead=p.raw.match(/^\s*/)[0],tail=p.raw.match(/\s*$/)[0],neg=/^\s*[!-]/.test(p.raw)?'!':'';
  const t=lead+neg+token(V,it.d,it.v)+tail;
  input.value=input.value.slice(0,p.start)+t+input.value.slice(p.end);
  const pos=p.start+t.length;input.setSelectionRange(pos,pos);sIdx=-1;live(b);
}

/* ---------- mouse ---------- */
function onMouseDown(e){
  if(e.button!==0||!(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey))return;
  if(e.target.closest('tbody td'))e.preventDefault();
}
function onClick(b,e){
  // Expand toggles, DOH buttons and note badges keep their own click.
  if(e.target.closest('button,a,input,select,summary,[role="button"]'))return;
  const td=e.target.closest('td');if(!td||td.parentNode.parentNode!==b.T.tBodies[0]||td.classList.contains('empty'))return;
  const r=td.parentNode.sectionRowIndex,c=td.cellIndex-1,mod=isMac?e.metaKey:e.ctrlKey||e.metaKey;
  if(editing===b)closeBar(b,false);
  if(td.classList.contains('rn')){e.stopPropagation();if(!mod)b.sel.clear();toggle(b,r,-1,false);b.anchor={r,c:-1};focusSheet(b);drawIdle(b);return}
  if(mod||e.altKey){e.stopPropagation();e.preventDefault();toggle(b,r,c,e.altKey);b.anchor={r,c};focusSheet(b);drawIdle(b);return}
  if(e.shiftKey&&b.anchor){e.stopPropagation();e.preventDefault();range(b,b.anchor,{r,c});focusSheet(b);drawIdle(b);return}
  b.anchor={r,c};
  if(b.sel.size){b.sel.clear();drawIdle(b)}
}

/* ---------- keyboard ---------- */
const typeable=/^[\p{L}\p{N}"!]$/u;
function onKey(e){
  if(e.defaultPrevented||e.isComposing)return;
  if(guide&&guide.classList.contains('on')&&e.key==='Escape'&&!e.target.closest?.('.qf-input')){e.preventDefault();closeGuide();return}
  if(e.target.closest?.('input,textarea,select,[contenteditable="true"]'))return;
  const b=activeBinding(e.target),inSheet=b&&b===bindingAt(e.target),ctrl=isMac?e.metaKey:e.ctrlKey,k=e.key;
  if(ctrl&&!e.altKey&&(k==='z'||k==='Z')){if(!b)return;e.preventDefault();undo(b,e.shiftKey);return}
  if(!isMac&&e.ctrlKey&&!e.altKey&&!e.shiftKey&&(k==='y'||k==='Y')){if(!b)return;e.preventDefault();undo(b,true);return}
  if(e.ctrlKey||e.metaKey||e.altKey)return;
  if(k==='?'){if(!b&&!guide?.classList.contains('on'))return;e.preventDefault();toggleGuide();return}
  if(k==='/'){if(!b)return;e.preventDefault();openBar(b);return}
  if(!inSheet)return;
  const st=b.sheetState(),lower=k.toLowerCase();
  // Shift+Esc is Chrome's task manager; the page never acts on it.
  if(k==='Escape'){if(!e.shiftKey&&b.sel.size){b.sel.clear();drawIdle(b)}return}
  if(k===' '){e.preventDefault();if(st?.on){toggle(b,st.r,st.c,false);b.anchor={r:st.r,c:st.c};drawIdle(b)}else toast('Click a cell first, then Space selects it');return}
  if(k==='Backspace'||k==='Delete'){e.preventDefault();if(e.shiftKey)clearAll(b);else{const n=serialize(vocab(b),b.group.committed()).length;if(n)removeChip(b,n-1);else toast('No filters to remove')}return}
  if(k==='+'){e.preventDefault();if(b.sel.size)act(b,'add');else toast('Ctrl+click cells first, then + adds them to the filter');return}
  if(lower==='x'&&b.sel.size){e.preventDefault();act(b,'exclude');return}
  if(lower==='f'){
    if(e.repeat){if(fDown)e.preventDefault();return}
    if(b.sel.size){e.preventDefault();fDown={b,t:performance.now()};b.group.preview(keepState(b.group.committed(),[...b.sel.values()]));return}
    const last=b.last,now=b.group.snapshot(b.group.committed());
    if(last&&now===(last.on?last.after:last.before)){
      e.preventDefault();b.group.apply(b.group.restore(last.on?last.before:last.after));last.on=!last.on;
      toast(last.on?'Filtered again — F shows everything':'Showing everything — F filters again');drawIdle(b);return;
    }
  }
  if(typeable.test(k)){e.preventDefault();openBar(b,{append:k})}
}
function onKeyUp(e){
  if(!fDown||e.key.toLowerCase()!=='f')return;
  const {b,t}=fDown,held=performance.now()-t;fDown=null;
  if(held<350)act(b,'keep');
  else{b.group.endPreview();drawIdle(b);toast('Peek ended — tap F to apply')}
}

/* ---------- guide ---------- */
function steps(b){
  const V=b&&vocab(b),ex=b?example(b):'',bt=b?.group.defs.map(d=>d.key)||[];
  const first=(d,i=0)=>V?.dims.get(d)?.list[i]??null;
  const b1=first('brand',0),b2=first('brand',1),t1=first('town',0),t2=first('town',1);
  const frag=e=>e?words(e.name).find(w=>w.length>=3)||norm(e.name):'';
  const typo=w=>w&&w.length>=5?w.slice(0,2)+w[3]+w[2]+w.slice(4):w;
  return [
    {t:'Type a filter',d:`Press ${kbd('/')} (or click the formula bar) and type. The table filters as you type; ${kbd('Enter')} applies, ${kbd('Esc')} restores.`,q:ex,e:'Pills name each match (Brand ▾ = several brands). On Enter, chips fly into the dropdowns above the table.'},
    bt.includes('town')&&t1&&{t:'Exclude and fix typos',d:'Start a word with <code>!</code> to exclude it. A one-letter typo still matches (shown with ~).',q:[frag(b1),'!'+typo(frag(t1))].filter(Boolean).join(' + '),e:`Keeps ${esc(b1?.name||'')}, excludes ${esc(t1.name)}.`},
    b1&&b2&&{t:'An impossible AND gets a one-key fix',d:'A row has one brand, so “+” between two brands never matches.',q:`"${b1.label}" + "${b2.label}"`,e:`Warning and 0 rows. Press ${kbd('Tab')}: the + becomes a comma and rows come back.`},
    {t:'A word in two fields',d:`When a word matches several fields the pill shows ⇥ with the alternatives. Put the caret on it and press ${kbd('Tab')} to switch field, or write <code>town:</code>, <code>brand:</code>, <code>sku:</code>${bt.includes('category')?', <code>cat:</code>':''} in front.`,q:bt.includes('town')&&t2?'town:'+frag(t2).slice(0,3):'',e:'The field prefix limits the word to that field.'},
    {t:'Select cells, then one key',d:`${kbd(MOD)}+click two or three names. The bar previews “Keep … → N” and other rows dim. Tap ${kbd('F')} to keep them. <b>Hold</b> ${kbd('F')} to peek and release to go back. Tap ${kbd('F')} again to flip between filtered and everything.`,e:'Chips fly from the cells to the dropdowns.'},
    {t:'Exclude, add, own level',d:`${kbd(MOD)}+click a value and press ${kbd('X')} to exclude it. ${kbd('+')} adds the selection to the current filter. In a tree, ${kbd('Alt')}+click picks only that row’s own level (a town without its brand). ${kbd('Shift')}+click selects a block. Click a row number for the whole row. ${kbd('Space')} adds the clicked cell.`,e:'Red “Not …” chips show exclusions.'},
    {t:'Undo, remove, clear',d:`${kbd(MOD)}+${kbd('Z')} undoes any filter change, including dropdown changes; ${kbd(MOD)}+${kbd('Shift')}+${kbd('Z')}${isMac?'':' or '+kbd('Ctrl')+'+'+kbd('Y')} redoes. ${kbd('Backspace')} removes the last chip and ${kbd('Shift')}+${kbd('Backspace')} clears all. Click a chip to edit it as text.`,e:'Each toast says what changed.'},
  ].filter(Boolean);
}
function guideHTML(){
  const b=bindings.find(visible);
  if(guideTab==='try')return `<p>${b?`Examples use this report’s data. <b>Try</b> types the query into the table’s formula bar.`:'Open a report with a table to try these.'}</p>`+steps(b).map((s,i)=>`<section class="qf-step"><h4>${i+1}. ${s.t}</h4><p>${s.d}</p>${s.q&&b?`<p><code>${esc(s.q)}</code> <button type="button" class="btn sm" data-try="${i}">Try ▸</button></p>`:''}<p class="qf-expect">Expect: ${s.e}</p></section>`).join('');
  if(guideTab==='language')return `<table class="qf-gt"><tr><th>Type</th><th>Means</th></tr>
    <tr><td><code>a, b</code></td><td>a <b>or</b> b — values of one field</td></tr>
    <tr><td><code>a+b</code> · <code>a + b</code></td><td>a <b>and</b> b — spaces are optional everywhere</td></tr>
    <tr><td><code>!a</code> · <code>-a</code></td><td><b>not</b> a (a minus only at the start; BPCNO-LP stays one name)</td></tr>
    <tr><td><code>shmp</code></td><td>every value that contains, starts with or nearly spells it</td></tr>
    <tr><td><code>"Sylhet"</code></td><td>exactly one value (choosing a suggestion writes this). Quote a name that contains <code>+</code> or <code>,</code>, e.g. <code>"SAF ACTV+"</code></td></tr>
    <tr><td><code>town:raj</code></td><td>only that field: <code>brand:</code> <code>sku:</code> <code>parent:</code> <code>town:</code> <code>cat:</code></td></tr></table>
    <p>The bar text <b>is</b> the filter, so deleting a part removes it. Each field holds one list of values, as in the dropdowns. Two different fields joined by “,” are not possible; the bar suggests “+” instead. When nothing matches, the bar says which part to remove.</p>`;
  return `<table class="qf-gt"><tr><th>Key / mouse</th><th>Does</th></tr>
    <tr><td>${kbd('/')} · click the formula bar</td><td>type a filter (starts from the current one)</td></tr>
    <tr><td>type a letter in a table</td><td>starts a filter with it</td></tr>
    <tr><td>${kbd(MOD)}+click · ${kbd('Space')}</td><td>select / unselect a cell</td></tr>
    <tr><td>${kbd('Shift')}+click · row number</td><td>select a block · a whole row</td></tr>
    <tr><td>${kbd('Alt')}+click</td><td>only the row’s own level</td></tr>
    <tr><td>${kbd('F')} tap · hold · again</td><td>keep selection · peek · flip filtered ↔ all</td></tr>
    <tr><td>${kbd('X')} · ${kbd('+')}</td><td>exclude · add to filter</td></tr>
    <tr><td>${kbd('Backspace')} · ${kbd('Shift')}+${kbd('Backspace')}</td><td>remove last chip · clear all</td></tr>
    <tr><td>${kbd(MOD)}+${kbd('Z')} · ${kbd(MOD)}+${kbd('Shift')}+${kbd('Z')}${isMac?'':' · '+kbd('Ctrl')+'+'+kbd('Y')}</td><td>undo · redo filter changes</td></tr>
    <tr><td>${kbd('Esc')}</td><td>clear selection · cancel typing</td></tr>
    <tr><td>In the bar: ${kbd('↑')}${kbd('↓')} ${kbd('Enter')} ${kbd('Tab')}</td><td>pick a suggestion · apply · switch field or fix</td></tr>
    <tr><td>${kbd('?')}</td><td>this guide</td></tr></table>
    <p class="qf-note">Plain click still opens each table’s details. Keys work while a table has focus, so they never fire in text boxes. None of them is a Chrome shortcut: ${kbd('Ctrl')}+${kbd('K')}/${kbd('E')}/${kbd('P')}/${kbd('F')}/${kbd('L')}, ${kbd('Ctrl')}+1–9, ${kbd('Shift')}+${kbd('Esc')}, ${kbd('Alt')}+letters and the F-keys stay with the browser.</p>`;
}
function drawGuide(){
  if(!guide)return;
  $$('[data-gtab]',guide).forEach(t=>t.setAttribute('aria-selected',t.dataset.gtab===guideTab));
  setHTML($('.qf-gbody',guide),guideHTML());
  $$('[data-try]',guide).forEach(btn=>btn.onclick=()=>tryStep(+btn.dataset.try));
}
function openGuide(){
  if(!guide){
    guide=document.createElement('aside');guide.className='qf-guide';guide.setAttribute('aria-label','Quick filter guide');
    setHTML(guide,`<header><div><h2>Quick filter</h2><p>Filter any table by typing or selecting.</p></div><button type="button" class="btn sm ghost" data-gclose aria-label="Close guide">✕</button></header>
      <div class="qf-gtabs" role="tablist"><button type="button" role="tab" data-gtab="try">Try it</button><button type="button" role="tab" data-gtab="language">Query language</button><button type="button" role="tab" data-gtab="keys">Keys &amp; mouse</button></div><div class="qf-gbody"></div>`);
    document.body.append(guide);
    $('[data-gclose]',guide).onclick=closeGuide;
    $$('[data-gtab]',guide).forEach(t=>t.onclick=()=>{guideTab=t.dataset.gtab;drawGuide()});
  }
  drawGuide();guide.classList.add('on');document.body.classList.add('qf-guide-open');
  root.document.getElementById('qfGuideBtn')?.setAttribute('aria-expanded','true');
}
function closeGuide(){guide?.classList.remove('on');document.body.classList.remove('qf-guide-open');root.document.getElementById('qfGuideBtn')?.setAttribute('aria-expanded','false')}
function toggleGuide(){if(guide?.classList.contains('on'))closeGuide();else openGuide()}
function tryStep(i){
  const b=bindings.find(visible),s=b&&steps(b)[i];if(!s?.q)return;
  if(editing)closeBar(editing,false);
  b.sel.clear();b.sheet.scrollIntoView({block:'nearest'});openBar(b);
  const input=$('.qf-input',b.qf);input.value='';live(b);let n=0;
  const type=()=>{if(editing!==b)return;input.value=s.q.slice(0,++n);input.setSelectionRange(n,n);input.oninput();if(n<s.q.length)setTimeout(type,40);else toast('Typed for you — press Enter to apply'+(i===2?' (or Tab to fix it)':''))};
  type();
}

/* ---------- attach ---------- */
function attach(selector,opt){
  const T=$(selector),sheet=T.closest('.sheet'),fx=$('.fx',sheet),wrap=T.closest('.tw');
  const qf=document.createElement('div');qf.className='qf';fx.append(qf);
  const b={selector,T,sheet,fx,wrap,qf,noun:'rows',tree:false,...opt,sel:new Map(),anchor:null,last:null,override:new Map()};
  bindings.push(b);
  T.addEventListener('sheet:render',()=>{if(editing===b)paint(b);else drawIdle(b)});
  wrap.addEventListener('mousedown',onMouseDown,true);
  wrap.addEventListener('click',e=>onClick(b,e),true);
  $('.fv',fx).addEventListener('click',()=>openBar(b));
  qf.addEventListener('click',e=>{
    if(editing===b)return;
    const rm=e.target.closest('[data-rm]');if(rm){e.stopPropagation();removeChip(b,+rm.dataset.rm);return}
    if(e.target.closest('[data-clear]')){clearAll(b);return}
    const chip=e.target.closest('[data-chip]');openBar(b,chip?{chip:+chip.dataset.chip}:{});
  });
  drawIdle(b);
  return b;
}
function init({toast:t,guideButton}={}){
  if(t)toast=t;
  guideButton?.addEventListener('click',toggleGuide);
}
document.addEventListener('keydown',onKey);
document.addEventListener('keyup',onKeyUp);
addEventListener('blur',()=>{if(fDown){const {b}=fDown;fDown=null;b.group.endPreview();drawIdle(b)}});
document.addEventListener('scroll',e=>{if(sugg&&!sugg.hidden&&!sugg.contains(e.target))hideSuggest()},{capture:true,passive:true});
root.FSNEQuick={...api,attach,init,openGuide,closeGuide,bindings};
})(typeof window!=='undefined'?window:globalThis);
