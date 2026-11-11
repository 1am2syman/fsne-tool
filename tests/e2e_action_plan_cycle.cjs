// Widescreen browser E2E. First run: uv run python -m tests.e2e_action_plan_cycle
// Uses a disposable server seeded with that actual pipeline baseline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'validation-artifacts/action-plan');
const {chromium} = require(require.resolve('playwright-core', {paths:[ROOT, path.join(ROOT, 'validation-artifacts/run-upload')]}));
const python = process.env.FSNE_PYTHON || path.join(ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

(async () => {
  const server = spawn(python, ['-W', 'ignore::FutureWarning', '-m', 'tests.e2e_action_plan_cycle', '--serve-browser'], {cwd:ROOT});
  let stderr = '', browser, page;
  const pendingRequests = new Set();
  server.stderr.on('data', data => stderr += data);
  try {
    const address = await new Promise((resolve,reject) => {
      const timer = setTimeout(() => reject(Error('Fixture server did not start: '+stderr)), 30000);
      let output = '';
      server.stdout.on('data', data => {
        output += data;
        if (output.includes('\n')) {clearTimeout(timer); resolve(JSON.parse(output.split('\n')[0]));}
      });
      server.once('exit', code => {clearTimeout(timer); reject(Error('Fixture server exited '+code+': '+stderr));});
    });
    browser = await chromium.launch({headless:true, executablePath:process.env.FSNE_CHROMIUM || '/usr/bin/chromium'});
    page = await browser.newPage({viewport:{width:1524,height:980},acceptDownloads:true});
    page.on('request', request => pendingRequests.add(request.url()));
    page.on('requestfinished', request => pendingRequests.delete(request.url()));
    page.on('requestfailed', request => pendingRequests.delete(request.url()));
    page.setDefaultTimeout(30000);
    page.setDefaultNavigationTimeout(120000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = 'http://127.0.0.1:' + address.port;
    const pythonCheck = (code, ...args) => execFileSync(python, ['-W', 'ignore::FutureWarning', '-c', code, ...args], {cwd:ROOT,encoding:'utf8',timeout:30000});
    const reconcileMovement = () => JSON.parse(pythonCheck('import json,sys; from pathlib import Path; from tests.e2e_action_plan_cycle import assert_current_movement; print(json.dumps(assert_current_movement(Path(sys.argv[1]))))', path.join(address.directory, 'runs/RUN-0001')));
    const screenshot = name => page.screenshot({path:path.join(OUT, name+'.png'), fullPage:true});
    let reportNavigations = 0;
    async function delayReportRefresh() {
      await page.route(base+'/?run=RUN-0001', async route => {
        reportNavigations++;
        await new Promise(resolve => setTimeout(resolve, 4000));
        await route.continue();
      });
    }
    const runRow = () => page.locator('#runsT tbody tr').filter({has:page.locator('[data-open="RUN-0001"]')});
    async function runs() {
      await page.waitForFunction(() => document.querySelector('#fileProg')?.textContent.includes('of 3 selected'), null, {timeout:120000});
      await page.locator('.item[data-view="onboarding"]').click();
      await page.locator('#obTabs [data-t="runs"]').click();
      await page.locator('[data-open="RUN-0001"]').waitFor();
    }
    async function snapshotState(state, label) {
      console.log('Checking browser state:', state);
      await runs();
      assert.equal(await page.locator('[data-open="RUN-0001"]').innerText(), label);
      assert.ok(await runRow().getByText('Action plan input file', {exact:true}).isVisible());
      assert.equal(await page.locator('[data-plan="RUN-0001"]').count(), state === 'settled' ? 0 : 1);
      assert.equal(await page.locator('[data-reopen="RUN-0001"]').count(), state === 'settled' ? 1 : 0);
      await screenshot('runs-'+state);
      const geometry = await page.evaluate(() => ({width:document.querySelector('main').clientWidth, scroll:document.querySelector('main').scrollWidth}));
      assert.equal(geometry.width, geometry.scroll, 'Runs page horizontal overflow');
      for (const tab of ['overview','batch','move','town','status','transfer','salespush']) {
        await page.locator('.item[data-r="'+tab+'"]').click();
        assert.ok(await page.locator('#planFlag').isVisible(), state+' / '+tab+' flag hidden');
        assert.ok((await page.locator('#planFlag').innerText()).startsWith(label));
        assert.equal(await page.locator('#planFlag').getAttribute('data-state'), state);
      }
      await page.locator('.item[data-r="overview"]').click();
      await screenshot('dashboard-'+state);
    }
    await page.goto(base+'/?run=RUN-0001');
    await page.waitForFunction(() => document.querySelector('#fileProg').textContent.includes('of 3 selected'));
    await page.evaluate(() => document.fonts.ready);
    await snapshotState('provisional', 'Pre-action plan view');
    const flag = await page.locator('#planFlag').innerText();
    assert.match(flag, /statuses carried over from the previous month \([\d,]+ batches\), [\d,]+ new batches unlabelled/);
    const counts = address.expected_counts;
    const formatCount = value => new Intl.NumberFormat('en-US').format(value);
    assert.ok(flag.includes('('+formatCount(counts.mitigation_carried_batches)+' batches), '+formatCount(counts.mitigation_unlabelled_batches)+' new batches unlabelled'), 'Legacy runs must recover accurate label counts from their saved snapshot');
    await runs();
    // Cancel both destructive confirmations; neither changes the accepted run.
    await page.locator('[data-settle="RUN-0001"]').click();
    await page.locator('#settleWithoutPlan').waitFor();
    assert.ok(await page.locator('#planConfirm').isDisabled());
    await screenshot('settle-without-plan-confirmation');
    if (process.argv.includes('--refresh-only')) {
      await delayReportRefresh();
      await page.locator('#settleWithoutPlan').check();
      await page.locator('#planConfirm').click();
      await page.waitForFunction(() => document.querySelector('#planFlag')?.dataset.state === 'settled', null, {timeout:30000});
      assert.equal(reportNavigations, 1, 'Polling cancelled and restarted the pending report navigation');
      console.log('REPORT_REFRESH_E2E_PASSED');
      return;
    }
    await page.locator('#planCancel').click();
    let response = await page.request.get(base+'/api/runs/RUN-0001');
    assert.equal((await response.json()).plan_state, 'provisional');
    // The upload check is visible and requires a second explicit acceptance.
    await page.locator('[data-plan="RUN-0001"]').click();
    await page.locator('#returnedPlan').setInputFiles(address.returned);
    await page.locator('#planConfirm').click();
    await page.getByRole('heading', {name:'Check returned plan'}).waitFor({timeout:120000});
    const checks = await page.locator('.plan-checks dd').allTextContents();
    assert.equal(checks[1], '1'); assert.equal(checks[2], '2'); assert.equal(checks[3], '1');
    await screenshot('upload-check');
    response = await page.request.get(base+'/api/runs/RUN-0001');
    assert.equal((await response.json()).plan_state, 'provisional');
    await delayReportRefresh();
    const acceptance = page.waitForResponse(response => response.url().endsWith('/action-plan/accept'), {timeout:300000});
    await page.locator('#planConfirm').click();
    const accepted = await acceptance;
    assert.equal(accepted.status(), 200, 'Action plan acceptance failed');
    console.log('Upload accepted; waiting for refreshed report');
    await page.waitForFunction(() => !document.querySelector('#planDialog').open, null, {timeout:300000});
    await page.waitForFunction(() => document.querySelector('#planFlag').dataset.state === 'plan_uploaded', null, {timeout:300000});
    assert.equal(reportNavigations, 1, 'Only one navigation should refresh the accepted report');
    await snapshotState('plan_uploaded', 'Action plan view (not settled)');
    const uploadedMovement = reconcileMovement();
    const persisted = JSON.parse(fs.readFileSync(path.join(address.directory, 'runs/RUN-0001/meta.json'), 'utf8'));
    assert.equal(persisted.plan_state, 'plan_uploaded');
    assert.equal('view_label' in persisted, false, 'Persist facts; derive labels when reading metadata');
    assert.equal('view_flag' in persisted, false);
    await runs();
    await page.locator('[data-settle="RUN-0001"]').click();
    await page.getByRole('heading', {name:'Settle month', exact:true}).waitFor();
    assert.match(await page.locator('#planBody').innerText(), /2026-08.*batches.*2 without a status/s);
    await screenshot('settle-confirmation');
    await page.locator('#planConfirm').click();
    await page.waitForFunction(() => document.querySelector('#planFlag').dataset.state === 'settled', null, {timeout:120000});
    await snapshotState('settled', 'Final view');
    await page.setViewportSize({width:1440,height:900});
    await runs();
    await screenshot('runs-settled-1440');
    await page.locator('[data-reopen="RUN-0001"]').click();
    await page.getByRole('heading', {name:'Reopen month'}).waitFor();
    await page.locator('#planCancel').click();
    response = await page.request.get(base+'/api/runs/RUN-0001');
    assert.equal((await response.json()).plan_state, 'settled');
    await page.locator('[data-reopen="RUN-0001"]').click();
    await page.locator('#planConfirm').click();
    await page.waitForFunction(() => document.querySelector('#planFlag').dataset.state === 'plan_uploaded', null, {timeout:120000});
    await runs();
    assert.equal(await page.locator('[data-open="RUN-0001"]').innerText(), 'Action plan view (not settled)');
    const blankFile = path.join(OUT, 'blank-plan.xlsx');
    pythonCheck('import sys,polars as pl; pl.read_excel(sys.argv[1],infer_schema_length=0).with_columns(pl.lit(None,dtype=pl.String).alias("mitigation_status")).write_excel(sys.argv[2])', address.returned, blankFile);
    const blankCheck = await page.request.put(base+'/api/runs/RUN-0001/action-plan/check', {data:fs.readFileSync(blankFile), headers:{'X-Filename':'Blank plan.xlsx'},timeout:120000});
    assert.equal(blankCheck.status(), 200, await blankCheck.text());
    const blankAccepted = await page.request.post(base+'/api/runs/RUN-0001/action-plan/accept', {data:{token:(await blankCheck.json()).token},timeout:300000});
    assert.equal(blankAccepted.status(), 200, await blankAccepted.text());
    const blankMovement = reconcileMovement();
    assert.equal(blankMovement.movement_stock_bdt, 0);
    assert.deepEqual(errors, [], 'Uncaught browser errors');
    fs.writeFileSync(path.join(OUT,'browser-results.json'), JSON.stringify({result:'passed', widths:[1524,1440], states:['provisional','plan_uploaded','settled'], dashboardTabs:7, uploadedMovement, blankMovement, errors}, null, 2)+'\n');
    for (const name of ['browser-failure.json','browser-failure.png']) fs.rmSync(path.join(OUT,name), {force:true});
    console.log('ACTION_PLAN_BROWSER_PASSED');
  } catch (error) {
    fs.writeFileSync(path.join(OUT, 'browser-failure.json'), JSON.stringify({error:String(error), pending:[...pendingRequests], stderr}, null, 2));
    if (page) await page.screenshot({path:path.join(OUT, 'browser-failure.png'), timeout:5000}).catch(() => {});
    throw error;
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => {if (server.exitCode !== null) resolve(); else server.once('exit', resolve);});
  }
})().catch(error => {console.error(error);process.exitCode=1;});
