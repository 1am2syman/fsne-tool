const {test}=require('node:test');
const assert=require('node:assert/strict');
const Q=require('../ui/quick_filter.js');
const {Group,matches}=require('../ui/report_filters.js');
const defs=[{key:'brand',label:'Brand'},{key:'parent',label:'Parent SKU'},{key:'town',label:'Town'}];
const rows=[
  {brand:'BPCNO-LP',parent:'101',pdesc:'BPCNO LP 100ML',town:'Sylhet'},
  {brand:'BPCNO-AC',parent:'102',pdesc:'BPCNO AC 50ML',town:'Bhairob'},
  {brand:'JFB_SHMP',parent:'201',pdesc:'JFB SHMP 180ML',town:'Sylhet'},
  {brand:'PN_SHMP',parent:'202',pdesc:'PN SHMP 340ML',town:'Narshingdi'},
  {brand:'SAFF ACTV',parent:'301',pdesc:'SAF ACTV+ 100G',town:'Rajshahi'},
];
const V=Q.vocabulary(defs,rows);
const st=s=>Object.fromEntries(Object.entries(s).map(([k,v])=>[k,[...v].sort()]));
test('commas are or within a field and spaced plus is and across fields',()=>{
  const r=Q.compile(V,'bpcno, shmp + sylhet');
  assert.deepEqual(st(r.state),{brand:['BPCNO-AC','BPCNO-LP','JFB_SHMP','PN_SHMP'],town:['Sylhet']});
  assert.deepEqual(r.hints,[]);
  assert.deepEqual(rows.filter(x=>matches(x,r.state)).map(x=>x.parent),['101','201']);
});
test('negation, typo tolerance and field prefixes',()=>{
  assert.deepEqual(st(Q.compile(V,'!sylhet').state),{town:['Bhairob','Narshingdi','Rajshahi']});
  const typo=Q.resolve(V,'narsingdi');assert.equal(typo.dim,'town');assert.equal(typo.typo,true);
  assert.equal(Q.resolve(V,'town:raj').dim,'town');
  assert.equal(Q.resolve(V,'sku:shmp').dim,'parent');
  assert.equal(Q.resolve(V,'-sylhet').neg,true);
  assert.equal(Q.resolve(V,'BPCNO-LP').neg,false);
});
test('operators need no spaces; quotes keep a literal plus or comma in a name',()=>{
  for(const q of ['bpcno+sylhet','bpcno +sylhet','bpcno+ sylhet',' bpcno , pn_shmp+sylhet'])
    assert.deepEqual(Object.keys(Q.compile(V,q).state).sort(),['brand','town'],q);
  assert.deepEqual(st(Q.compile(V,'bpcno,shmp+sylhet').state),st(Q.compile(V,'bpcno, shmp + sylhet').state));
  const r=Q.compile(V,'"SAF ACTV+ 100G"+rajshahi');
  assert.equal(r.groups.length,2);assert.deepEqual(st(r.state).parent,['301']);
  assert.equal(Q.scan('"a+b, c"+d').length,2);
  assert.deepEqual(st(Q.compile(V,'"BPCNO-LP"').state),{brand:['BPCNO-LP']});
  assert.equal(Q.resolve(V,'"Nowhere"').kind,'none');
});
test('impossible and mixed groups get hints with text fixes',()=>{
  const r=Q.compile(V,'"BPCNO-LP" + "JFB_SHMP"');
  assert.equal(r.hints[0].type,'onedim');assert.deepEqual(r.empty,['brand']);
  assert.equal(Q.mergeFix('"BPCNO-LP" + "JFB_SHMP"',0,1),'"BPCNO-LP", "JFB_SHMP"');
  const m=Q.compile(V,'"BPCNO-LP", sylhet');
  assert.equal(m.hints[0].type,'mixed');assert.deepEqual(m.state,{});
  assert.equal(Q.splitFix(V,'"BPCNO-LP", sylhet',0),'"BPCNO-LP" + sylhet');
});
test('Tab override changes the field a word means',()=>{
  const override=new Map([['shmp','parent']]);
  assert.equal(Q.resolve(V,'shmp').dim,'brand');assert.equal(Q.resolve(V,'shmp',override).dim,'parent');
});
test('serialized text round-trips and prefers short exclusions',()=>{
  const state={brand:new Set(['BPCNO-LP','PN_SHMP']),town:new Set(['Bhairob','Narshingdi','Rajshahi','Sylhet'].filter(t=>t!=='Rajshahi'))};
  const chips=Q.serialize(V,state);
  assert.equal(chips[1].neg,true);assert.equal(chips[1].text,'!"Rajshahi"');
  assert.deepEqual(st(Q.compile(V,Q.serializeText(V,state)).state),st(state));
  const parent={parent:new Set(['301'])};
  assert.deepEqual(st(Q.compile(V,Q.serializeText(V,parent)).state),{parent:['301']});
});
test('selection keeps, adds and excludes against the committed filter',()=>{
  const items=[{facts:{brand:'BPCNO-LP',town:'Sylhet'},primary:'town'},{facts:{brand:'PN_SHMP'},primary:'brand'}];
  assert.deepEqual(st(Q.keepState({town:new Set(['Bhairob'])},items)),{town:['Sylhet'],brand:['BPCNO-LP','PN_SHMP']});
  assert.deepEqual(st(Q.addState({brand:new Set(['SAFF ACTV'])},items)),{brand:['BPCNO-LP','PN_SHMP','SAFF ACTV']});
  assert.deepEqual(st(Q.excludeState(V,{},[items[1]])),{brand:['BPCNO-AC','BPCNO-LP','JFB_SHMP','SAFF ACTV']});
  assert.deepEqual(st(Q.excludeState(V,{brand:new Set(['PN_SHMP','JFB_SHMP'])},[items[1]])),{brand:['JFB_SHMP']});
});
test('group previews without recording and undoes committed changes per scope',()=>{
  const container={classList:{add(){}},append(){}};
  const group=Object.create(Group.prototype);let draws=0;
  Object.assign(group,{source:()=>rows,onChange:()=>draws++,states:new Map(),state:{},defs,histories:new Map(),previewing:false,controls:[],clear:{hidden:true},container});
  group.use('a');
  group.preview({brand:new Set(['PN_SHMP'])});assert.equal(group.rows().length,1);assert.equal(group.history().undo.length,0);
  group.endPreview();assert.equal(group.rows().length,5);
  group.apply({town:new Set(['Sylhet'])});group.apply({town:new Set(['Sylhet']),brand:new Set(['JFB_SHMP'])});
  assert.equal(group.rows().length,1);
  assert.equal(group.undo(),true);assert.equal(group.rows().length,2);
  assert.equal(group.undo(),true);assert.equal(group.rows().length,5);assert.equal(group.undo(),false);
  assert.equal(group.redo(),true);assert.equal(group.rows().length,2);
  group.use('b');assert.equal(group.undo(),false);group.use('a');assert.deepEqual(group.selection(),{town:['Sylhet']});
  assert.ok(draws>0);
});
test('values without rows do not block the short exclusion chip',()=>{
  const state={town:new Set(['Bhairob','Narshingdi','Rajshahi','Unlisted'])};
  const chip=Q.serialize(V,state)[0];
  assert.equal(chip.neg,true);assert.equal(chip.text,'!"Sylhet"');
  assert.deepEqual(rows.filter(r=>require('../ui/report_filters.js').matches(r,Q.compile(V,chip.text).state)).length,rows.filter(r=>require('../ui/report_filters.js').matches(r,state)).length);
});
test('a group without scopes can undo its first change',()=>{
  const fake=()=>({classList:{add(){},toggle(){},remove(){}},dataset:{},style:{},append(){},setAttribute(){},addEventListener(){}});
  global.document={createElement:fake};
  const container={classList:{add(){}},append(){}};
  try{
    const group=new Group({container,rows:()=>rows,onChange(){}});
    group.controls.forEach(c=>c.refresh=()=>{});
    group.apply({town:new Set(['Sylhet'])});
    assert.equal(group.undo(),true);assert.deepEqual(group.selection(),{});
  }finally{delete global.document}
});
