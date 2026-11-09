/* Real onboarding. All requests stay relative to the app's mounted path. */
(() => {
  const selected = new Map();
  const slots = [
    ['config', 'Run configuration', '.xlsx,.xlsm'],
    ['landing', 'Landing rates', '.xlsx,.xlsm'],
    ['secondary', 'Secondary sales, month on month', '.xlsb,.xlsx,.xlsm'],
    ['hierarchy', 'Product hierarchy', '.xlsx'],
    ['shelflife', 'Shelf-life report', '.csv'],
  ];
  let online = false, busy = false, runs = [], polling = false, loadedRun = null;
  const basePath = location.pathname.replace(/\/index\.html$/, '').replace(/\/?$/, '/');
  const apiUrl = path => location.origin + basePath + 'api/' + path;
  const error = message => {
    $('#onboardingError').textContent = message || '';
    $('#onboardingError').hidden = !message;
  };
  async function api(path, options = {}) {
    const response = await fetch(apiUrl(path), {cache: 'no-store', signal: AbortSignal.timeout(300000), ...options});
    const data = await response.json().catch(() => ({error: 'The run service returned an unreadable response'}));
    if (!response.ok) throw new Error(data.error || 'The request failed');
    return data;
  }
  const post = (path, body = {}) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  function controls() {
    $('#runBtn').disabled = !online || busy || selected.size !== slots.length;
    $('#sLabel').disabled = busy || !online;
    $('#archivePrevious').disabled = busy || !online;
    $$('#files input').forEach(input => input.disabled = busy || !online);
    $('#fileProg').textContent = selected.size + ' of ' + slots.length + ' selected';
  }
  $('#runBtn').disabled = true;
  $('#sDate').value = '';
  $('#files').html = slots.map(([slot, label, ext]) => `<label class="file" data-slot="${slot}"><span class="st"></span><div><b>${label}</b><small>${ext.replaceAll(',', ' / ')}</small></div><span class="btn sm">Choose file</span><input type="file" aria-label="${label}" accept="${ext}"></label>`).join('');
  $$('#files .file').forEach(el => {
    const slot = el.dataset.slot;
    function choose(file) {
      if (!file || busy || !online) return;
      const allowed = slots.find(s => s[0] === slot)[2].split(',');
      const suffix = '.' + file.name.split('.').at(-1).toLowerCase();
      if (!allowed.includes(suffix) || !file.size || file.size > 1024 ** 3) {
        error('Choose a nonempty ' + allowed.join(' / ') + ' file, up to 1 GB.');
        el.querySelector('input').value = '';
        return;
      }
      selected.set(slot, file);
      el.classList.add('done');
      el.querySelector('small').textContent = file.name + ' · ' + Math.ceil(file.size / 1024).toLocaleString() + ' KB selected';
      el.querySelector('.btn').textContent = 'Replace';
      if (slot === 'config') $('#sDate').value = '';
      error(''); controls();
    }
    el.querySelector('input').onchange = e => choose(e.target.files[0]);
    el.ondragover = e => {e.preventDefault(); if (online && !busy) el.classList.add('over');};
    el.ondragleave = () => el.classList.remove('over');
    el.ondrop = e => {e.preventDefault(); el.classList.remove('over'); choose(e.dataTransfer.files[0]);};
  });
  const status = {draft: ['s-idle', 'Incomplete upload'], queued: ['s-idle', 'Queued'], running: ['s-run', 'Running'], ok: ['s-ok', 'Completed'], bad: ['s-bad', 'Failed']};
  const dateText = value => value ? new Date(value.slice(0,10) + 'T00:00:00Z').toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}) : '—';
  function resultsUrl(id) {
    return basePath + '?run=' + encodeURIComponent(id) + '#results';
  }
  function actions(r) {
    return (r.status === 'ok' ? `<a class="btn sm" data-open="${r.id}" href="${esc(resultsUrl(r.id))}">Open results</a> <a class="btn sm ghost" href="${apiUrl('runs/' + r.id + '/report.xlsx')}" download>Excel</a> ` : '') +
      `<button class="btn sm ghost" data-log="${r.id}">Log</button> ` +
      (!['running', 'queued'].includes(r.status) ? `<button class="btn sm ghost" data-archive="${r.id}" data-value="${!r.archived}">${r.archived ? 'Restore' : 'Archive'}</button>` : '');
  }
  const columns = [
    {k:'id',h:'Run',r:r=>r.id,cls:'mono'},
    {k:'label',h:'Label',r:r=>esc(r.label),cls:'name'},
    {k:'report_date',h:'Report date',r:r=>dateText(r.report_date),cls:'dim'},
    {k:'rows',h:'Batches',num:1,r:r=>r.rows == null ? '—' : f(r.rows)},
    {k:'seconds',h:'Time',r:r=>r.seconds == null ? '—' : r.seconds + ' s',cls:'dim'},
    {k:'status',h:'Status',r:r=>`<span class="dot ${status[r.status][0]}">${esc(r.step || status[r.status][1])}</span>${r.error ? `<div class="badc">${esc(r.error)}</div>` : ''}`},
    {k:'actions',h:'Results and actions',r:actions},
  ];
  function drawRuns() {
    const active = runs.filter(r => !r.archived), archived = runs.filter(r => r.archived);
    table({t:'#runsT',cols:columns,rows:active,emptyMsg:'No runs yet. Select all five source files and choose Start run.'});
    table({t:'#archiveT',cols:columns,rows:archived,emptyMsg:'Archived runs appear here. Their results remain available.'});
    $('#runsN').textContent = active.length || ''; $('#archN').textContent = archived.length || '';
    const running = runs.filter(r => ['queued', 'running'].includes(r.status)).length;
    $('#runCount').textContent = running ? running + ' running' : '';
    $$('[data-archive]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try {await post('runs/' + b.dataset.archive + '/' + (b.dataset.value === 'true' ? 'archive' : 'restore')); await refresh();}
      catch (e) {error(e.message); b.disabled = false;}
    });
    $$('[data-log]').forEach(b => b.onclick = async () => {
      try {
        const response = await fetch(apiUrl('runs/' + b.dataset.log + '/log'), {cache:'no-store'});
        if (!response.ok) throw new Error('Could not load the run log');
        $('#runLog').textContent = await response.text() || 'No pipeline steps have run yet.';
        $('#runLogTitle').textContent = b.dataset.log + ' log';
        $('#runLogPanel').hidden = false;
        $('#runLogPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
      } catch (e) {error(e.message);}
    });
  }
  $('#closeRunLog').onclick = () => $('#runLogPanel').hidden = true;
  async function refresh() {runs = await api('runs'); drawRuns();}
  async function poll() {
    if (polling) return;
    polling = true;
    try {await refresh();}
    catch (e) {error('Could not refresh runs: ' + e.message);}
    finally {polling = false; setTimeout(poll, 2500);}
  }
  $('#runBtn').onclick = async () => {
    if (busy || !online || selected.size !== slots.length) return;
    busy = true; error(''); controls();
    let acceptedId = null, draftId = null;
    try {
      const run = await post('runs', {label: $('#sLabel').value, archive_previous: $('#archivePrevious').getAttribute('aria-checked') === 'true'});
      draftId = run.id;
      for (let i = 0; i < slots.length; i++) {
        const [slot, label] = slots[i], file = selected.get(slot);
        $('#appNotice').textContent = `Uploading ${i + 1} of ${slots.length}: ${label}…`;
        const meta = await api('runs/' + run.id + '/files/' + slot, {method:'PUT',headers:{'X-Filename':encodeURIComponent(file.name)},body:file});
        if (meta.report_date) $('#sDate').value = meta.report_date;
      }
      await post('runs/' + run.id + '/start');
      acceptedId = run.id;
      selected.clear();
      $$('#files .file').forEach(el => {
        el.classList.remove('done'); el.querySelector('input').value = '';
        el.querySelector('small').textContent = 'Select a file for the next run'; el.querySelector('.btn').textContent = 'Choose file';
      });
      $('#appNotice').textContent = run.id + ' accepted. Follow its progress in Runs, then choose Open results.';
      $$('#obTabs .tab')[1].click();
      await refresh();
    } catch (e) {
      if (draftId && !acceptedId) await post('runs/' + draftId + '/discard').catch(() => {});
      error(e.message);
      $('#appNotice').textContent = acceptedId ? `${acceptedId} was accepted. Check Runs for its status.` : 'Run was not started. Your selected files are kept so you can correct the problem and retry.';
    }
    finally {busy = false; controls(); refresh().catch(() => {});}
  };
  async function init() {
    try {
      const context = await api('context' + location.search);
      loadedRun = context.run;
      online = true;
      $('#appNotice').textContent = loadedRun ? `Viewing ${loadedRun.id} · ${loadedRun.label}. Select new files to start another run.` : 'Reports currently show the bundled sample. Upload your files to calculate a new run.';
      await refresh();
      if (location.hash === '#results' && loadedRun) $('.item[data-r="overview"]').click();
      poll();
    } catch {
      online = false;
      $('#appNotice').textContent = 'This is a saved report. Open the live app to upload files and run calculations.';
      drawRuns();
    }
    controls();
  }
  init();
})();
