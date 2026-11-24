const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {matches,catalog,searchMatches,mergeMovement}=require('../ui/report_filters.js');
const rows=[
  {brand:'A',parent:'101',pdesc:'Alpha',town:'Dhaka'},
  {brand:'A',parent:'102',pdesc:'Beta',town:'Sylhet'},
  {brand:'B',parent:'201',pdesc:'Gamma',town:'Dhaka'},
  {brand:'B',parent:'201',pdesc:'Gamma',town:'Khulna'},
];
test('OR within a field, AND across fields, and exact identities',()=>{
  const selection={brand:new Set(['A','B']),town:new Set(['Dhaka']),parent:new Set(['101','201'])};
  assert.deepEqual(rows.filter(r=>matches(r,selection)),[rows[0],rows[2]]);
  assert.equal(matches(rows[0],{parent:new Set(['10'])}),false);
  assert.equal(matches(rows[0],{parent:new Set()}),false);
  assert.equal(matches(rows[0],{parent:null}),true);
});
test('each catalog observes the other fields and ignores its own selection',()=>{
  const selection={brand:new Set(['A']),town:new Set(['Dhaka'])};
  assert.deepEqual(catalog(rows,selection,'brand').map(o=>o.value),['A','B']);
  assert.deepEqual(catalog(rows,selection,'town').map(o=>o.value),['Dhaka','Sylhet']);
  assert.deepEqual(catalog(rows,selection,'parent'),[{value:'101',label:'Alpha · 101'}]);
  assert.deepEqual(catalog(rows,{brand:new Set(['Missing'])},'town'),[]);
  assert.deepEqual([...selection.brand],['A']);
  assert.equal(catalog([{brand:'',town:'X'}],{},'brand')[0].label,'(Blanks)');
});
test('search uses Excel text, wildcard and literal escaping rules',()=>{
  for(const [text,q] of [['Alpha · 101','ALPHA'],['Alpha · 101','101'],['Dhaka','D*a'],['Dhaka','D?aka'],['A*B','A~*B'],['A?B','A~?B'],['A[B','[']])assert.equal(searchMatches(text,q),true);
  assert.equal(searchMatches('Dhaka','Khulna'),false);
  assert.equal(searchMatches('AXB','A~*B'),false);
  assert.equal(searchMatches('AXB','A~?B'),false);
});
test('movement merges selected town-parent facts in both units preserving missing cells',()=>{
  const cell=(lm,o,n,rem)=>({lm,o,n,r:lm-o,rem,kl:{lm:lm/10,o:o/10,n:n/10,r:(lm-o)/10}});
  const data=[{brand:'A',parent:'101',pdesc:'Alpha',town:'Dhaka',m:[[cell(100,40,20,'Push')],null]},
    {brand:'A',parent:'101',pdesc:'Alpha',town:'Sylhet',m:[[cell(50,20,10,'Push, Rotate')],[cell(30,10,0,null)]]},
    {brand:'B',parent:'201',pdesc:'Gamma',town:'Dhaka',m:[[cell(80,60,0,null)],null]}];
  const filtered=data.filter(r=>matches(r,{town:new Set(['Dhaka'])}));
  const result=mergeMovement(filtered,2,1);
  assert.equal(result.total[0][0].lm,180);assert.equal(result.total[0][0].kl.lm,18);
  assert.equal(result.total[0][0].r,80);assert.equal(result.total[1],null);
  assert.equal(result.brands[0].p[0].m[0][0].n,20);
  assert.equal(mergeMovement(data,2,1).brands[0].m[0][0].rem,'Push, Rotate');
  assert.deepEqual(mergeMovement([],2,1),{total:[null,null],brands:[]});
});
test('town-level evolution and DOH scopes include every brand unless explicitly filtered',()=>{
  const template=fs.readFileSync('ui/template.html','utf8');
  const start=template.indexOf('function statusEvolutionFilters('),end=template.indexOf('function statusDohPairs(',start);
  const context=vm.createContext({STATUS:{groups:[]},statusSelectedGroups:()=>[],statusFilters:{selection:()=>({})}});
  vm.runInContext(template.slice(start,end),context);
  const scope=n=>JSON.parse(JSON.stringify(context.statusEvolutionFilters(n,'town')));
  assert.deepEqual(scope({id:'town',level:0,name:'Dhaka',town:'Dhaka',brand:'A'}),{town:['Dhaka']});
  assert.deepEqual(scope({id:'brand',level:1,name:'A',town:'Dhaka',brand:'A'}),{town:['Dhaka'],brand:['A']});
  assert.deepEqual(scope({id:'parent',level:2,name:'Alpha',town:'Dhaka',brand:'A',code:'101'}),{town:['Dhaka'],brand:['A'],parent:['101']});
  context.statusFilters.selection=()=>({brand:['A','B']});
  assert.deepEqual(scope({id:'town',level:0,name:'Dhaka',town:'Dhaka',brand:'A'}),{brand:['A','B'],town:['Dhaka']});
});
function browser(){
  class Element{
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.style={};this.attrs={};this.value='';this.checked=false;this.hidden=false;this.className='';this.classList={add:(c)=>{this.className+=' '+c}}}
    append(...nodes){this.children.push(...nodes);for(const node of nodes)if(typeof node==='object')node.parent=this}
    replaceChildren(...nodes){this.children=[];this.append(...nodes)}
    setAttribute(k,v){this.attrs[k]=v}
    contains(target){return this===target||this.children.some(c=>c?.contains?.(target))}
    focus(){this.focused=true}
    click(){if(!this.disabled)return this.onclick?.()}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this)}
    getBoundingClientRect(){return {left:200,bottom:100,width:180}}
    querySelector(){return this.children.flatMap(c=>[c,...(c?.children||[])]).find(c=>c.tagName==='INPUT')}
    get offsetWidth(){return 300}
  }
  const events={},document={body:new Element(),createElement:t=>new Element(t),createTextNode:text=>({textContent:text}),addEventListener:(key,fn)=>{(events[key]??=[]).push(fn)}};
  const context=vm.createContext({document,console,innerWidth:1366,innerHeight:768,addEventListener(){}});
  vm.runInContext(fs.readFileSync('ui/report_filters.js','utf8'),context);
  return {api:context.FSNEFilters,Element,document,events};
}
test('dropdown drafts changes, disables empty OK, exposes mixed Select All and cancels',async()=>{
  const {api,Element}=browser();let selected=null,calls=0;
  const control=new api.Dropdown({container:new Element(),key:'brand',label:'Brand',options:()=>[{value:'A',label:'A'},{value:'B',label:'B'}],selection:()=>selected,apply:s=>{selected=s;calls++}});
  await control.open();assert.equal(control.all.checked,true);
  control.all.checked=false;control.all.onchange();assert.equal(control.ok.disabled,true);assert.equal(calls,0);
  control.draft.add('A');control.draw();assert.equal(control.all.indeterminate,true);assert.equal(control.ok.disabled,false);
  control.popup.onkeydown({key:'Escape',preventDefault(){},stopPropagation(){}});assert.equal(selected,null);assert.equal(calls,0);
  await control.open();control.draft.delete('B');control.draw();control.ok.click();assert.deepEqual([...selected],['A']);assert.equal(calls,1);
  await control.open();control.draft.clear();control.close();assert.deepEqual([...selected],['A']);
});
test('search selects matching results and supports adding a second search selection',async()=>{
  const {api,Element}=browser();let selected=new Set(['A']);
  const control=new api.Dropdown({container:new Element(),key:'brand',label:'Brand',options:()=>[{value:'A',label:'Alpha'},{value:'B',label:'Beta'},{value:'C',label:'Gamma'}],selection:()=>selected,apply:s=>{selected=s}});
  await control.open();control.search.value='Beta';control.search.oninput();assert.deepEqual([...control.result()],['B']);
  control.add.checked=true;control.add.onchange();assert.deepEqual([...control.result()],['A','B']);control.ok.click();assert.deepEqual([...selected],['A','B']);
  await control.open();control.search.value='Missing';control.search.oninput();assert.equal(control.ok.disabled,true);assert.equal(control.all.disabled,true);
  control.close();assert.deepEqual([...selected],['A','B']);
});
test('group keeps report scopes independent and preserves incompatible explicit selections',async()=>{
  const {api,Element}=browser();const group=new api.Group({container:new Element(),rows:()=>rows,onChange(){}});
  group.use('rescue');group.state.brand=new Set(['A']);group.state.parent=new Set(['201']);
  assert.equal(group.rows().length,0);await group.controls[1].open();
  assert.equal(group.controls[1].allOptions.find(o=>o.value==='201').unavailable,true);group.controls[1].close();
  group.use('rebalance');assert.equal(group.rows().length,4);
  group.use('rescue');assert.deepEqual([...group.state.parent],['201']);assert.equal(group.rows().length,0);
  group.state.brand=new Set(['Missing']);await group.controls[1].open();
  assert.equal(group.controls[1].all.disabled,true);assert.equal(group.controls[1].ok.disabled,false);
  group.controls[1].ok.click();assert.deepEqual([...group.state.parent],['201']);assert.equal(group.rows().length,0);
});
process.on('beforeExit',()=>console.log('filter checks passed'));
