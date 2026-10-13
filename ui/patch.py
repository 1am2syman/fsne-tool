import re,pathlib
p=pathlib.Path('template.html'); s=p.read_text()

# ---------- CSS ----------
css1='''.sheet{border:1px solid var(--line2);border-radius:8px;overflow:hidden;background:var(--surface)}
.fx{display:flex;height:28px;border-bottom:1px solid var(--line2);font-size:12.5px;background:var(--surface)}
.fx .nb{width:64px;display:grid;place-items:center;border-right:1px solid var(--line2);font:500 12px var(--mono);color:var(--ink2);background:var(--sunken)}
.fx .fi{width:34px;display:grid;place-items:center;border-right:1px solid var(--line2);font:italic 500 13px var(--serif);color:var(--ink3)}
.fx .fv{padding:0 10px;display:flex;align-items:center;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tw{overflow:auto;max-height:calc(100vh - 380px);min-height:220px;outline:0}
table{border-collapse:separate;border-spacing:0;width:max-content;min-width:100%;font-size:13px}
th,td{border-right:1px solid var(--line);border-bottom:1px solid var(--line);padding:0 8px;white-space:nowrap}
th{position:sticky;z-index:3;background:var(--sunken);font-weight:500;color:var(--ink3);text-align:left;user-select:none}
th.col{top:0;height:22px;text-align:center;font:500 11px var(--mono);border-color:var(--line2)}
th.hd{top:22px;height:30px;color:var(--ink);font-weight:600;font-size:12.5px;border-bottom:1px solid var(--line2)}
th.hd.s{cursor:pointer}th.hd.s:hover{background:var(--line)}
th.num{text-align:right}
th .ar{display:inline-block;width:12px;margin-left:4px;opacity:0;color:var(--accent)}
th[aria-sort] .ar{opacity:1}
th.hl{background:var(--accent-soft);color:var(--accent)}
td{height:26px;background:var(--surface)}
td.num{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:12.5px}
td.mono{font-size:12px}
.rn{position:sticky;left:0;z-index:2;width:44px;min-width:44px;max-width:44px;padding:0;text-align:center!important;background:var(--sunken)!important;color:var(--ink3);font:500 11px var(--mono)!important;border-right:1px solid var(--line2)}
th.rn{z-index:5}
td.rn.hl{background:var(--accent-soft)!important;color:var(--accent)}
tbody tr:hover td:not(.rn){background:var(--hover)}
td:nth-child(2),th:nth-child(2){position:sticky;left:44px;z-index:1;border-right:1px solid var(--line2)}
th:nth-child(2){z-index:4}
td.empty{position:static;white-space:normal;text-align:center;color:var(--ink3);padding:40px 16px}
td.sel{outline:2px solid var(--accent);outline-offset:-2px;background:var(--surface)!important}
td.dim{color:var(--ink3)}
td.name{font-weight:500}
'''
a=s.index('.tw{background'); b=s.index('.dot{display')
s=s[:a]+css1+s[b:]
a=s.index('tfoot td{position'); b=s.index('.mv{display')
s=s[:a]+'''tfoot td{position:sticky;bottom:0;z-index:2;background:var(--sunken)!important;font-weight:600;border-top:2px solid var(--line2);height:30px}
tfoot td.rn{z-index:4}
.rem{max-width:380px;overflow:hidden;text-overflow:ellipsis;color:var(--ink2)}
'''+s[b:]
s=s.replace('.empty{padding:48px;text-align:center;color:var(--ink3)}\n','')

# ---------- HTML ----------
s=s.replace('<div class="tw"><table><thead id="bHead"></thead><tbody id="bBody"></tbody></table></div>','<div data-sheet="bT"></div>')
s=s.replace('<div class="tw"><table><thead id="mHead"></thead><tbody id="mBody"></tbody><tfoot id="mFoot"></tfoot></table></div>','<div data-sheet="mT"></div>')
s=re.sub(r'<div id="ob-(runs|archive)" hidden>.*</table></div></div>',r'<div id="ob-\1" hidden><div data-sheet="\1T"></div></div>',s)

# ---------- JS ----------
setter='''
/* sanitising HTML setter: parses in element context, strips scripts and inline handlers */
Object.defineProperty(Element.prototype,'html',{set(h){const r=document.createRange();r.selectNodeContents(this);const fr=r.createContextualFragment(h);
  fr.querySelectorAll('script,iframe,object,embed').forEach(n=>n.remove());
  fr.querySelectorAll('*').forEach(n=>[...n.attributes].forEach(a=>/^on/i.test(a.name)&&n.removeAttribute(a.name)));this.replaceChildren(fr)}});
$$('[data-sheet]').forEach(el=>{el.className='sheet';el.html=`<div class="fx"><span class="nb">A1</span><span class="fi">ƒx</span><span class="fv"></span></div><div class="tw" tabindex="0"><table id="${el.dataset.sheet}"><thead></thead><tbody></tbody><tfoot></tfoot></table></div>`});
'''
s=s.replace("const esc=s=>",setter.lstrip('\n')+"const esc=s=>",1) if False else s
i=s.index("const esc=s=>")
s=s[:i]+setter+s[i:]

helper='''/* spreadsheet table: column letters, row numbers (header is row 1), frozen header + first column, cell selection, formula bar */
const L=i=>String.fromCharCode(65+i), SHEETS={};
function table({t,cols,rows,sort,onSort,foot,emptyMsg}){
  const T=$(t),th=T.tHead,tb=T.tBodies[0],tf=T.tFoot,wrap=T.closest('.tw'),fx=T.closest('.sheet').querySelector('.fx');
  th.html=`<tr><th class="rn col"></th>${cols.map((c,i)=>`<th class="col">${L(i)}</th>`).join('')}</tr><tr><th class="rn hd">1</th>${cols.map(c=>{const s=onSort&&!c.nosort,on=s&&sort.k===c.k;return `<th class="hd${c.num?' num':''}${s?' s':''}" ${on?`aria-sort="${sort.d>0?'ascending':'descending'}"`:''} data-k="${c.k}">${c.h}${s?`<span class="ar">${on&&sort.d>0?'↑':'↓'}</span>`:''}</th>`}).join('')}</tr>`;
  $$('th.s',th).forEach(h=>h.onclick=()=>onSort(h.dataset.k));
  tb.html=rows.map((r,i)=>`<tr><td class="rn">${i+2}</td>${cols.map(c=>`<td class="${(c.num?'num ':'')+(c.cls||'')}">${c.r(r)}</td>`).join('')}</tr>`).join('')||`<tr><td class="rn"></td><td class="empty" colspan="${cols.length}">${emptyMsg||'No rows match these filters.'}</td></tr>`;
  tf.html=foot?`<tr><td class="rn">Σ</td>${foot.map((h,i)=>`<td class="${cols[i].num?'num':''}">${h}</td>`).join('')}</tr>`:'';
  const st=SHEETS[t]||(SHEETS[t]={r:0,c:0,on:false});
  st.nr=rows.length;st.nc=cols.length;
  st.paint=scroll=>{
    $$('.sel,.hl',T).forEach(x=>x.classList.remove('sel','hl'));
    const row=st.on&&tb.rows[st.r],td=row&&row.cells[st.c+1];
    if(!td||td.classList.contains('empty')){return}
    td.classList.add('sel');row.cells[0].classList.add('hl');th.rows[0].cells[st.c+1].classList.add('hl');
    fx.querySelector('.nb').textContent=L(st.c)+(st.r+2);fx.querySelector('.fv').textContent=td.textContent.trim();
    if(scroll)td.scrollIntoView({block:'nearest',inline:'nearest'});
  };
  if(!st.bound){st.bound=1;
    tb.addEventListener('click',e=>{const td=e.target.closest('td');if(!td||td.classList.contains('rn')||td.classList.contains('empty'))return;
      st.r=td.parentNode.sectionRowIndex;st.c=td.cellIndex-1;st.on=true;st.paint();wrap.focus({preventScroll:true})});
    wrap.addEventListener('keydown',e=>{
      const d={ArrowUp:[-1,0],ArrowDown:[1,0],ArrowLeft:[0,-1],ArrowRight:[0,1]}[e.key];if(!d)return;
      e.preventDefault();st.on=true;st.r=Math.max(0,Math.min(st.nr-1,st.r+d[0]));st.c=Math.max(0,Math.min(st.nc-1,st.c+d[1]));st.paint(true)});
  }
  st.r=Math.min(st.r,Math.max(0,rows.length-1));st.paint();
}
'''
a=s.index('/* sortable table helper */'); b=s.index('const cmp=')
s=s[:a]+helper+s[b:]

# runs/archive
a=s.index('function drawRuns(){'); b=s.index("$('#runBtn').onclick")
runs='''const rcols=[
 {k:'id',h:'Run',r:r=>r.id,cls:'mono'},{k:'label',h:'Label',r:r=>esc(r.label),cls:'name'},{k:'date',h:'Report date',r:r=>r.date,cls:'dim'},
 {k:'rows',h:'Batches',num:1,r:r=>f(r.rows)},{k:'dur',h:'Time',num:1,r:r=>r.dur,cls:'dim'},
 {k:'st',h:'Status',r:r=>`<span class="dot ${ST[r.st][0]}">${r.st==='run'?esc(r.step):ST[r.st][1]}</span>${r.err?`<span class="dim" style="margin-left:8px">${esc(r.err)}</span>`:''}`},
 {k:'a',h:'',r:r=>`<button class="btn sm ghost" data-a="${r.id}" ${r.st==='run'?'disabled':''}>Archive</button>`}];
const acols=[
 {k:'id',h:'Run',r:r=>r.id,cls:'mono'},{k:'label',h:'Label',r:r=>esc(r.label),cls:'name'},{k:'date',h:'Report date',r:r=>r.date,cls:'dim'},{k:'at',h:'Archived',r:r=>r.at,cls:'dim'},
 {k:'a',h:'',r:r=>`<button class="btn sm ghost" data-rs="${r.id}">Restore</button>`}];
function drawRuns(){
  table({t:'#runsT',cols:rcols,rows:runs,emptyMsg:'No runs yet. Add the source files and choose Start run.'});
  table({t:'#archiveT',cols:acols,rows:arch,emptyMsg:'Archived runs show up here. Archive a run to keep its results without cluttering Runs.'});
  $('#runsN').textContent=runs.length;$('#archN').textContent=arch.length;
  const n=runs.filter(r=>r.st==='run').length;$('#runCount').textContent=n?n+' running':'';
  $$('[data-a]').forEach(b=>b.onclick=()=>{const i=runs.findIndex(r=>r.id===b.dataset.a),r=runs.splice(i,1)[0];arch.unshift({...r,at:'Today'});drawRuns();toast(r.id+' archived')});
  $$('[data-rs]').forEach(b=>b.onclick=()=>{const i=arch.findIndex(r=>r.id===b.dataset.rs),r=arch.splice(i,1)[0];runs.push({id:r.id,label:r.label,date:r.date,rows:null,dur:'–',st:'ok'});drawRuns();toast(r.id+' restored')});
}
'''
s=s[:a]+runs+s[b:]

s=s.replace("head:'#bHead',body:'#bBody'","t:'#bT'")
a=s.index("  table({head:'#mHead'"); b=s.index("}\ndrawM();")
s=s[:a]+'''  const foot=mf==='all'&&!mq?['Grand total',f(GRAND.lm,2),f(GRAND.cmo,2),f(GRAND.cmn,2),`<span class="good">${f(GRAND.red,2)}</span>`,GRAND.pct+'%','',`<span style="font-weight:450">${esc(GRAND.rem)}</span>`]:null;
  table({t:'#mT',cols:mcols,rows,sort:ms,onSort:k=>{ms=ms.k===k?{k,d:-ms.d}:{k,d:-1};drawM()},foot});
'''+s[b:]
s=s.replace('.innerHTML=','.html=')
p.write_text(s)
print(s.count('innerHTML'))
