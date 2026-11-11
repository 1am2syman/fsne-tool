// Browser end-to-end regressions. Requires Chromium and playwright-core:
// npm install --prefix validation-artifacts/run-upload playwright-core
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'validation-artifacts/run-upload');
fs.mkdirSync(OUT, {recursive:true});
const {chromium} = require(require.resolve('playwright-core', {paths:[ROOT, OUT]}));
const group = process.argv[2] || 'all';
const python = process.env.FSNE_PYTHON || path.join(ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const markers = {date:'DATE_BROWSER_PASSED', actions:'ACTIONS_BROWSER_PASSED', cards:'CARDS_BROWSER_PASSED', uploads:'UPLOADS_BROWSER_PASSED'};
const selected = name => group === 'all' || group === name;

(async () => {
  const server = spawn(python, ['-W', 'ignore::FutureWarning', 'tests/serve_upload_regression.py'], {cwd:ROOT});
  let stderr = '';
  server.stderr.on('data', chunk => stderr += chunk);
  let browser;
  try {
    const address = await new Promise((resolve,reject) => {
      const timer = setTimeout(() => reject(Error('Test server did not start: '+stderr)),30000);
      let data='';
      server.stdout.on('data', chunk => {
        data+=chunk;
        if(data.includes('\n')) {clearTimeout(timer);resolve(JSON.parse(data.split('\n')[0]));}
      });
      server.once('exit', code => {clearTimeout(timer);reject(Error('Server exited '+code+': '+stderr));});
    });
    browser = await chromium.launch({headless:true, executablePath:process.env.FSNE_CHROMIUM || '/usr/bin/chromium'});
    const page = await browser.newPage({viewport:{width:1524,height:980},acceptDownloads:true});
    const resize = async size => {
      await page.setViewportSize(size);
      await page.waitForFunction(expected=>Math.abs(document.querySelector('aside').getBoundingClientRect().width-expected)<0.1,size.width<=700?56:232);
    };
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('dialog',d=>d.accept());
    const base='http://127.0.0.1:'+address.port;
    await page.goto(base);
    await page.waitForFunction(()=>document.querySelector('#fileProg').textContent==='0 of 3 selected');
    await page.evaluate(()=>document.fonts.ready);
    const shot = name => page.screenshot({path:path.join(OUT,name+'.png'),fullPage:true});
    const bounds = () => page.evaluate(()=>({
      cards:[...document.querySelectorAll('#ob-setup>.panel')].map(el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height}}),
      rows:[...document.querySelectorAll('#files>.file')].map(el=>el.getBoundingClientRect().height),
      pageWidth:document.querySelector('main').clientWidth,
      scrollWidth:document.querySelector('main').scrollWidth
    }));
    const equalCards = b => {
      assert.ok(Math.abs(b.cards[0].top-b.cards[1].top)<1,JSON.stringify(b));
      assert.ok(Math.abs(b.cards[0].bottom-b.cards[1].bottom)<1,JSON.stringify(b));
      assert.equal(b.scrollWidth,b.pageWidth,'Unexpected whole-page horizontal overflow');
    };
    const empty = await bounds();
    if(selected('date') || selected('cards') || selected('uploads')) await shot('desktop-empty');
    if(selected('cards')) equalCards(empty);
    if(selected('date')) {
      assert.equal(await page.locator('#runBtn').isDisabled(),true);
      assert.equal(await page.locator('#dateHint').textContent(),'The day stock was counted.');
      assert.equal(await page.locator('#dateStamp').textContent(),'DD MMM YYYY');
      assert.ok(await page.locator('#startReason').isVisible());
      assert.equal(await page.locator('#startReason').textContent(),'Add a report date to continue.');
    }
    if(selected('date') || selected('cards') || selected('uploads')) {
      await page.locator('#sDate').fill('2026-08-30');
      await page.locator('#sDate').blur();
      await page.waitForFunction(()=>!document.querySelector('#discardDraft').hidden);
      assert.equal(await page.locator('#dateStamp').textContent(),'30 AUG 2026');
      const draft=await bounds();
      assert.deepEqual(draft.rows,empty.rows,'Draft control changed upload row heights');
      assert.deepEqual(draft.cards.map(c=>c.height),empty.cards.map(c=>c.height),'Draft control changed card heights');
      for(const slot of ['secondary','hierarchy','shelflife']) {
        const data=await page.locator(`[data-slot=${slot}] .file-sample`).getAttribute('href');
        const ext=slot==='shelflife'?'.csv':'.xlsx';
        const name=(slot==='hierarchy'?'a-very-long-selected-filename-'.repeat(6):'selected-'+slot)+ext;
        const accepted=page.waitForResponse(r=>r.url().endsWith('/files/'+slot)&&r.request().method()==='PUT');
        await page.locator(`[data-slot=${slot}] input`).setInputFiles({name,mimeType:slot==='shelflife'?'text/csv':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(data.split(',')[1],'base64')});
        assert.equal((await accepted).status(),200);
        await page.waitForFunction(s=>document.querySelector(`[data-slot=${s}]`).classList.contains('done'),slot);
        await page.waitForFunction(()=>!document.querySelector('#sDate').disabled);
        const after=await bounds();
        assert.deepEqual(after.rows,empty.rows,'File selection grew an upload row');
        assert.deepEqual(after.cards.map(c=>c.height),empty.cards.map(c=>c.height),'File selection grew a card');
        assert.equal(await page.locator(`[data-slot=${slot}] .file-name`).textContent(),name);
      }
      await page.waitForFunction(()=>!document.querySelector('#runBtn').disabled);
      const filled=await bounds();
      if(selected('cards')) equalCards(filled);
      await shot('desktop-selected');
      if(selected('uploads')) {
        const sample=await page.locator('[data-slot=hierarchy] .file-sample').getAttribute('href');
        const accepted=page.waitForResponse(r=>r.url().endsWith('/files/hierarchy')&&r.request().method()==='PUT');
        await page.locator('[data-slot=hierarchy] input').setInputFiles({name:'short.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(sample.split(',')[1],'base64')});
        assert.equal((await accepted).status(),200);
        await page.waitForFunction(()=>document.querySelector('[data-slot=hierarchy] .file-meta').textContent.startsWith('short.xlsx'));
        await page.waitForFunction(()=>!document.querySelector('#sDate').disabled);
        assert.deepEqual((await bounds()).rows,empty.rows);
        await page.reload();
        await page.waitForFunction(()=>document.querySelector('#fileProg').textContent==='3 of 3 selected');
        assert.deepEqual((await bounds()).rows,empty.rows,'Reload changed row heights');
      }
      if(selected('cards')) {
        await page.locator('.config-import>summary').click();
        await page.locator('.config-about>summary').click();
        equalCards(await bounds());
        await shot('desktop-expanded');
        // The ticket's option popovers are exclusive and close on Escape.
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.ob-pop[open]').count(),0,'Option popover stayed open');
      }
      await resize({width:390,height:1100});
      const mobile=await bounds();
      assert.ok(mobile.cards[1].top>=mobile.cards[0].bottom,'Mobile cards must stack');
      assert.equal(mobile.scrollWidth,mobile.pageWidth,'Mobile page overflows');
      if(selected('uploads')) {
        const sample=await page.locator('[data-slot=shelflife] .file-sample').getAttribute('href');
        const accepted=page.waitForResponse(r=>r.url().endsWith('/files/shelflife')&&r.request().method()==='PUT');
        const filename='a-long-mobile-filename-'.repeat(8)+'.csv';
        await page.locator('[data-slot=shelflife] input').setInputFiles({name:filename,mimeType:'text/csv',buffer:Buffer.from(sample.split(',')[1],'base64')});
        assert.equal((await accepted).status(),200);
        await page.waitForFunction(name=>document.querySelector('[data-slot=shelflife] .file-name').textContent===name,filename);
        await page.waitForFunction(()=>!document.querySelector('#sDate').disabled);
        // Filenames wrap in full instead of truncating, so a very long name may grow its row.
        const fit=await page.locator('[data-slot=shelflife] .file-name').evaluate(el=>({w:el.clientWidth,sw:el.scrollWidth,h:el.clientHeight,sh:el.scrollHeight}));
        assert.ok(fit.w>=fit.sw&&fit.h>=fit.sh,'Mobile filename is clipped: '+JSON.stringify(fit));
        const replaced=await bounds();
        assert.equal(replaced.scrollWidth,replaced.pageWidth,'Mobile filename replacement overflows the page');
      }
      await shot('mobile-setup-top');
      await page.locator('main').evaluate(el=>el.scrollTop=el.scrollHeight);
      await shot('mobile-setup-bottom');
      await page.locator('main').evaluate(el=>el.scrollTop=0);
      if(selected('date')) {
        await resize({width:1524,height:980});
        const started=page.waitForResponse(r=>r.url().endsWith('/start')&&r.request().method()==='POST');
        await page.locator('#runBtn').click();
        assert.equal((await started).status(),200);
        await page.waitForFunction(()=>document.querySelector('#runsT').textContent.includes('Waiting for a free worker'));
        const response=await page.request.get(base+'/api/runs');
        const queued=(await response.json()).find(r=>r.status==='queued');
        assert.ok(queued);
        assert.equal((await page.request.post(base+'/api/runs/'+queued.id+'/cancel',{data:{}})).status(),200);
      }
    }
    if(selected('actions')) {
      async function checkActions(selector) {
        const groups=await page.locator(selector+' .run-actions').evaluateAll(els=>els.map(el=>{
          const r=el.getBoundingClientRect();
          const cell=el.closest('td');
          const row=cell.parentElement;
          return {width:r.width,buttons:[...el.children].map(b=>{const x=b.getBoundingClientRect();return {top:x.top,bottom:x.bottom,right:x.right,left:x.left}}),right:r.right,
            labelPosition:getComputedStyle(row.children[1]).position};
        }));
        assert.ok(groups.length);
        assert.ok(groups.some(g=>g.buttons.length===5),'Completed run controls missing');
        for(const g of groups){
          assert.equal(g.labelPosition,'static','Run labels must not stick over the scrolled actions');
          assert.ok(g.buttons.length>=2);
          assert.ok(g.buttons.every(b=>Math.abs(b.top-g.buttons[0].top)<1),'Actions wrapped');
          assert.ok(g.buttons.at(-1).right<=g.right+1,'Actions overflow their cell');
        }
      }
      for(const width of [1524,1280,390]) {
        await resize({width,height:980});
        await page.locator('[data-t=runs]').click();
        await checkActions('#runsT');
        await page.locator('#runsT .runs-scroll').evaluate(el=>el.scrollLeft=el.scrollWidth);
        await shot('runs-'+width);
        await page.locator('[data-t=archive]').click();
        await checkActions('#archiveT');
        await page.locator('#archiveT .runs-scroll').evaluate(el=>el.scrollLeft=el.scrollWidth);
        await shot('archive-'+width);
      }
      await resize({width:1524,height:980});
      await page.locator('[data-t=runs]').click();
      const saved=page.waitForEvent('download');
      await page.locator('#runsT a').filter({hasText:'Excel'}).click();
      const file=await saved;
      assert.equal(file.suggestedFilename(),'action_plan_to_tm_2026_11_10.xlsx');
      await file.saveAs(path.join(OUT,'browser-downloaded.xlsx'));
      const active = page.locator('#runsT tr').filter({has:page.locator('[data-open]')});
      await active.locator('[data-log]').click();
      await page.locator('#runLogPanel').waitFor({state:'visible'});
      await page.locator('#closeRunLog').click();
      await active.locator('[data-archive]').click();
      await page.waitForFunction(()=>!document.querySelector('#runsT [data-open]'));
      await page.locator('[data-t=archive]').click();
      await page.locator('#archiveT tr').filter({has:page.locator('[data-open]')}).first().locator('[data-archive]').click();
      await page.locator('[data-t=runs]').click();
      await page.waitForFunction(()=>!!document.querySelector('#runsT [data-open]'));
      await active.locator('[data-delete]').click();
      await page.waitForFunction(()=>!document.querySelector('#runsT [data-open]'));
    }
    assert.deepEqual(errors,[],'Browser errors');
    fs.writeFileSync(path.join(OUT,group+'-measurements.json'),JSON.stringify({empty,errors,group},null,2));
    for(const [name,marker] of Object.entries(markers)) if(selected(name)) console.log(marker);
  } finally {
    if(browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve=>{if(server.exitCode!==null)resolve();else server.once('exit',resolve);});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
