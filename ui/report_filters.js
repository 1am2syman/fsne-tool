/* Shared Excel-style value filters. Null means all; explicit selections never broaden implicitly. */
(function(root){
'use strict';
const dimensions=[{key:'brand',label:'Brand'},{key:'parent',label:'Parent SKU'},{key:'town',label:'Town'}];
const value=v=>String(v??'');
const optionLabel=v=>v===''?'(Blanks)':v;
const compare=(a,b)=>a.label.localeCompare(b.label,undefined,{numeric:true,sensitivity:'base'});
function searchMatches(text,query){
  const source=text.toLowerCase(),tokens=[];
  for(let i=0;i<query.length;i++){
    const c=query[i].toLowerCase();
    if(c==='~'&&i+1<query.length)tokens.push({literal:query[++i].toLowerCase()});
    else tokens.push(c==='*'||c==='?'?{wildcard:c}:{literal:c});
  }
  if(tokens.every(t=>t.literal!=null))return source.includes(tokens.map(t=>t.literal).join(''));
  // Unanchored wildcard search uses bounded dynamic programming, never a user-built regex.
  let previous=Array(source.length+1).fill(true);
  for(const token of tokens){
    const next=Array(source.length+1).fill(false);next[0]=token.wildcard==='*'&&previous[0];
    for(let i=1;i<=source.length;i++)next[i]=token.wildcard==='*'?(previous[i]||next[i-1]):previous[i-1]&&(token.wildcard==='?'||source[i-1]===token.literal);
    previous=next;
  }
  return previous.some(Boolean);
}
function matches(row,selections,exclude){
  return Object.entries(selections).every(([key,selected])=>key===exclude||selected==null||selected.has(value(row[key])));
}
function catalog(rows,selections,key){
  const options=new Map();
  for(const row of rows){
    if(!matches(row,selections,key))continue;
    const id=value(row[key]);
    if(!options.has(id))options.set(id,{value:id,label:key==='parent'?(row.pdesc&&row.pdesc!==id?row.pdesc+' · '+id:optionLabel(id)):optionLabel(id)});
  }
  return [...options.values()].sort(compare);
}
function mergeMovement(rows,months,groups){
  const empty=()=>Array.from({length:months},()=>null),brands=new Map(),total=empty();
  function add(target,source){
    source.forEach((month,i)=>{if(!month)return;if(!target[i])target[i]=Array.from({length:groups},()=>null);
      month.forEach((cell,g)=>{if(!cell)return;
        if(!target[i][g])target[i][g]={lm:null,o:null,n:null,r:null,rem:null,kl:{lm:null,o:null,n:null,r:null}};
        const dest=target[i][g];
        for(const k of ['lm','o','n','r']){if(cell[k]!=null)dest[k]=(dest[k]??0)+cell[k];if(cell.kl?.[k]!=null)dest.kl[k]=(dest.kl[k]??0)+cell.kl[k]}
        const remarks=new Set([dest.rem,cell.rem].filter(Boolean).flatMap(s=>s.split(', ')));dest.rem=[...remarks].join(', ')||null;
      });
    });
  }
  for(const row of rows){
    if(!brands.has(row.brand))brands.set(row.brand,{b:row.brand,m:empty(),parents:new Map()});
    const brand=brands.get(row.brand);
    if(!brand.parents.has(row.parent))brand.parents.set(row.parent,{c:row.parent,d:row.pdesc,m:empty()});
    add(brand.m,row.m);add(brand.parents.get(row.parent).m,row.m);add(total,row.m);
  }
  return {total,brands:[...brands.values()].map(b=>({b:b.b,m:b.m,p:[...b.parents.values()]}))};
}
function element(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!=null)el.textContent=text;return el}
let active=null,nextId=0;
class Dropdown{
  constructor({container,key,label,options,selection,apply}){
    Object.assign(this,{key,label,options,selection,apply});
    this.button=element('button','btn value-filter');this.button.type='button';
    this.button.setAttribute('aria-haspopup','dialog');this.button.setAttribute('aria-expanded','false');
    this.name=element('span','value-filter-name',label);this.summary=element('span','value-filter-summary');
    this.button.append(this.name,this.summary,element('span','value-filter-arrow'));
    container.append(this.button);this.button.onclick=()=>active===this?this.close():this.open();this.refresh();
  }
  refresh(){
    const selected=this.selection(),n=selected?.size;
    const text=selected==null?'All':n===1?optionLabel([...selected][0]):'Multiple items';
    this.summary.textContent=text;this.button.dataset.active=String(selected!=null);
    this.button.title=this.label+': '+(selected==null?'All':[...selected].map(optionLabel).join(', '));
    this.button.setAttribute('aria-label',this.label+': '+text);
  }
  close(focus=false){
    this.version=(this.version||0)+1;this.popup?.remove();this.popup=null;
    this.button.setAttribute('aria-expanded','false');if(active===this)active=null;
    if(focus)this.button.focus({preventScroll:true});
  }
  async open(){
    active?.close();active=this;const version=this.version=(this.version||0)+1;
    const popup=this.popup=element('div','value-filter-popup');popup.id='valueFilter'+(++nextId);
    popup.setAttribute('role','dialog');popup.setAttribute('aria-label',this.label+' filter');
    this.button.setAttribute('aria-controls',popup.id);this.button.setAttribute('aria-expanded','true');
    const clear=element('button','value-filter-clear','Clear filter from '+this.label);clear.type='button';clear.disabled=this.selection()==null;
    clear.onclick=()=>{this.apply(null);this.refresh();this.close(true)};
    this.search=element('input','value-filter-search');this.search.type='search';this.search.placeholder='Search';this.search.autocomplete='off';this.search.setAttribute('aria-label','Search '+this.label);
    this.list=element('div','value-filter-list','Loading…');this.list.setAttribute('role','group');this.list.setAttribute('aria-label',this.label+' values');
    this.addLabel=element('label','value-filter-add');this.add=element('input');this.add.type='checkbox';this.addLabel.append(this.add,document.createTextNode('Add current selection to filter'));this.addLabel.hidden=true;
    const footer=element('div','value-filter-footer'),cancel=element('button','btn','Cancel');cancel.type='button';cancel.onclick=()=>this.close(true);
    this.ok=element('button','btn primary','OK');this.ok.type='button';this.ok.disabled=true;
    this.ok.onclick=()=>this.commit();
    footer.append(cancel,this.ok);popup.append(clear,this.search,this.list,this.addLabel,footer);
    const modal=this.button.closest?.('dialog[open]');(modal||document.body).append(popup);
    const rect=this.button.getBoundingClientRect();popup.style.width=Math.max(300,Math.min(390,rect.width+100))+'px';
    popup.style.left=Math.max(8,Math.min(rect.left,innerWidth-popup.offsetWidth-8))+'px';
    popup.style.top=(rect.bottom+6)+'px';popup.style.maxHeight=Math.max(220,innerHeight-rect.bottom-16)+'px';
    popup.onkeydown=e=>this.keydown(e);
    this.search.focus();
    await this.populate(version);
  }
  commit(){
    const selected=this.result();if(!selected.size)return;
    const all=this.allOptions.every(o=>!o.unavailable&&selected.has(o.value))&&selected.size===this.allOptions.length;
    this.apply(all&&!this.search.value?null:selected);this.refresh();this.close(true);
  }
  keydown(e){
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();this.close(true);return}
    if(e.key==='Enter'&&e.target===this.search&&!this.ok.disabled){e.preventDefault();this.ok.click();return}
    if(e.key==='ArrowDown'&&e.target===this.search){e.preventDefault();this.list.querySelector('input')?.focus();return}
    if(e.key!=='Tab')return;
    const targets=[...this.popup.querySelectorAll('button:not(:disabled),input:not(:disabled)')].filter(el=>!el.closest('[hidden]'));
    const first=targets[0],last=targets.at(-1);
    if(e.shiftKey&&e.target===first){e.preventDefault();last?.focus()}
    else if(!e.shiftKey&&e.target===last){e.preventDefault();first?.focus()}
  }
  searchChanged(){
    const query=this.search.value.trim();
    if(query)this.queried=new Set(this.allOptions.filter(o=>!o.unavailable&&searchMatches(o.label,query)).map(o=>o.value));
    else if(this.queried){this.draft=this.result();this.queried=null;this.add.checked=false}
    this.addLabel.hidden=!query;this.draw();
  }
  async populate(version){
    try{
      const options=await this.options();if(active!==this||version!==this.version)return;
      this.allOptions=options.slice().sort(compare);const selected=this.selection();
      this.draft=new Set(selected??this.allOptions.map(o=>o.value));this.queried=null;
      const available=new Set(this.allOptions.map(o=>o.value));
      for(const id of selected||[])if(!available.has(id))this.allOptions.push({value:id,label:optionLabel(id),unavailable:true});
      this.search.oninput=()=>this.searchChanged();
      this.add.onchange=()=>this.update();this.draw();
    }catch{if(active!==this||version!==this.version)return;this.list.textContent='Could not load values. Close and try again.'}
  }
  result(){return this.queried?(this.add.checked?new Set([...this.draft,...this.queried]):new Set(this.queried)):new Set(this.draft)}
  draw(){
    const query=this.search.value.trim();this.visible=this.allOptions.filter(o=>!query||searchMatches(o.label,query));
    this.list.replaceChildren();const allRow=element('label','value-filter-option value-filter-all');this.all=element('input');this.all.type='checkbox';
    allRow.append(this.all,document.createTextNode(query?'(Select All Search Results)':'(Select All)'));this.list.append(allRow);
    const set=this.queried||this.draft;
    this.all.onchange=()=>{for(const o of this.visible){if(o.unavailable)continue;if(this.all.checked)set.add(o.value);else set.delete(o.value)}this.draw()};
    for(const option of this.visible){
      const label=element('label','value-filter-option'),input=element('input');input.type='checkbox';input.value=option.value;input.checked=set.has(option.value);
      if(option.unavailable){label.classList.add('is-unavailable');label.title='No matching records with the other filters. Uncheck to remove this selection.'}
      input.onchange=()=>{if(input.checked)set.add(option.value);else set.delete(option.value);this.update()};
      label.append(input,element('span','',option.label));this.list.append(label);
    }
    if(!this.visible.length)this.list.append(element('p','value-filter-empty','No matches'));
    this.update();
  }
  update(){
    const set=this.queried||this.draft,visible=this.visible.filter(o=>!o.unavailable),n=visible.filter(o=>set.has(o.value)).length;
    this.all.checked=visible.length>0&&n===visible.length;this.all.indeterminate=n>0&&n<visible.length;this.all.disabled=!visible.length;
    this.ok.disabled=!this.result().size;
  }
}
class Group{
  constructor({container,rows,onChange,dimensions:defs=dimensions}){
    this.source=rows;this.onChange=onChange;this.states=new Map();this.state={};this.defs=defs;this.histories=new Map();this.previewing=false;
    this.container=container;container.classList.add('value-filters');
    this.controls=defs.map(({key,label})=>new Dropdown({container,key,label,options:()=>catalog(this.source(),this.state,key),selection:()=>this.state[key]??null,apply:selected=>{this.state[key]=selected;this.changed()}}));
    this.clear=element('button','btn ghost value-filters-reset','×');this.clear.type='button';this.clear.title='Clear filters';this.clear.setAttribute('aria-label','Clear filters');this.clear.hidden=true;
    this.clear.onclick=()=>{this.state={};this.states.set(this.scope,this.state);this.changed()};container.append(this.clear);
    // Single-scope reports never call use(), so their first change needs this baseline to be undoable.
    this.history().last=this.snapshot();
  }
  use(scope){active?.close();this.scope=scope;if(!this.states.has(scope))this.states.set(scope,{});this.state=this.states.get(scope);this.previewing=false;this.history().last??=this.snapshot();this.refresh()}
  matches(row,exclude){return matches(row,this.state,exclude)}
  rows(){return this.source().filter(r=>this.matches(r))}
  get active(){return Object.values(this.state).some(s=>s!=null)}
  refresh(){this.controls.forEach(c=>c.refresh());this.clear.hidden=!this.active}
  changed(){this.record();this.refresh();this.onChange()}
  selection(){return Object.fromEntries(Object.entries(this.state).filter(([,s])=>s!=null).map(([k,s])=>[k,[...s]]))}
  /* Committed state per scope, a temporary preview over it, and per-scope undo history of committed changes. */
  committed(){return this.states.get(this.scope)||{}}
  snapshot(state=this.state){return JSON.stringify(Object.entries(state).filter(([,s])=>s!=null).map(([k,s])=>[k,[...s].sort()]).sort((a,b)=>a[0]<b[0]?-1:1))}
  restore(text){return Object.fromEntries(JSON.parse(text).map(([k,v])=>[k,new Set(v)]))}
  history(){let h=this.histories.get(this.scope);if(!h)this.histories.set(this.scope,h={undo:[],redo:[],last:undefined});return h}
  record(){
    if(this.previewing)return;
    const h=this.history(),now=this.snapshot();
    if(h.last!=null&&h.last!==now){h.undo.push(h.last);h.redo.length=0;if(h.undo.length>200)h.undo.shift()}
    h.last=now;
  }
  apply(state){this.previewing=false;this.state=state;this.states.set(this.scope,state);this.changed()}
  preview(state){this.previewing=true;this.state=state;this.refresh();this.onChange()}
  endPreview(){if(!this.previewing)return;this.previewing=false;this.state=this.committed();this.refresh();this.onChange()}
  step(from,to){
    this.endPreview();const h=this.history();if(!h[from].length)return false;
    h[to].push(h.last);h.last=h[from].pop();this.state=this.restore(h.last);this.states.set(this.scope,this.state);
    this.refresh();this.onChange();return true;
  }
  undo(){return this.step('undo','redo')}
  redo(){return this.step('redo','undo')}
}
if(typeof document!=='undefined'){
  document.addEventListener('pointerdown',e=>{if(active&&!active.popup?.contains(e.target)&&!active.button.contains(e.target))active.close()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&active){e.preventDefault();active.close(true)}});
  addEventListener('resize',()=>active?.close());
  document.addEventListener('scroll',e=>{if(active&&!active.popup?.contains(e.target))active.close()},true);
}
const api={Dropdown,Group,matches,catalog,searchMatches,mergeMovement,dimensions};
root.FSNEFilters=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
