/* Real onboarding. All requests stay relative to the app's mounted path. */
(() => {
  const selected = new Map();
  const slots = [
    ['config', 'Run configuration', '.xlsx,.xlsm'],
    ['secondary', 'Secondary sales', '.xlsb,.xlsx,.xlsm'],
    ['hierarchy', 'Product hierarchy', '.xlsx'],
    ['shelflife', 'Shelf-life report', '.csv'],
  ];
  const requiredSlots = slots.filter(([slot]) => slot !== 'config');
  const descriptions = {
    config: 'Fill the report date from a workbook.',
    secondary: 'Monthly sales through the report month.',
    hierarchy: 'Material codes, products and parent mapping.',
    shelflife: 'Stock, expiry dates and DB Landing value.',
  };
  const help = {
    config: 'Optional import: fills the report date from config_table. You can edit the date above.',
    secondary: 'Monthly sales history through the report month, including Month-Year, town, material, batch and sales volume. Example: Mode_Sec_Month on Month.xlsb',
    hierarchy: 'Excel product hierarchy with material code, description, volume and parent material code. Example: PH_FG.xlsx',
    shelflife: 'CSV stock and shelf-life report for the report month, including material, batch, town, expiry, stock and DB Landing value (the source of stock value in BDT). Example: Product Shelflife Report Multiple FSNE  (03-08-2026)  (03-09-2026).csv',
  };
  const formats = {config: ['XLSX', '#1E7A4C'], secondary: ['XLSB', '#1E7A4C'], hierarchy: ['XLSX', '#1E7A4C'], shelflife: ['CSV', '#3D5A99']};
  const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  let online = false, busy = false, runs = [], polling = false, reloadingReport = false, loadedRun = null, draft = null, invalidSlot = null, trackedRun = null;
  // The ticket stamp spells the month out so 08/09 can never be read the wrong way round.
  const stampText = value => value ? value.slice(8, 10) + ' ' + MONTHS[Number(value.slice(5, 7)) - 1] + ' ' + value.slice(0, 4) : 'DD MMM YYYY';
  const sizeText = bytes => bytes < 1024 ** 2 ? Math.max(1, Math.ceil(bytes / 1024)).toLocaleString('en-GB') + ' KB' : (bytes / 1024 ** 2).toLocaleString('en-GB', {maximumFractionDigits: 1}) + ' MB';
  const dateText = value => value ? new Date(value.slice(0,10) + 'T00:00:00Z').toLocaleDateString('en-GB', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}) : '—';
  const basePath = location.pathname.replace(/\/index\.html$/, '').replace(/\/?$/, '/');
  const apiUrl = path => location.origin + basePath + 'api/' + path;
  const error = message => {
    $('#onboardingError').textContent = message || '';
    $('#onboardingError').hidden = !message;
  };
  async function api(path, options = {}) {
    const response = await fetch(apiUrl(path), {cache: 'no-store', signal: AbortSignal.timeout(300000), ...options});
    const data = await response.json().catch(() => ({error: 'The run service returned an unreadable response'}));
    if (!response.ok) {
      const failure = new Error(data.error || 'The request failed');
      failure.previousUnsettled = response.status === 400 && failure.message.startsWith('Previous month ');
      throw failure;
    }
    return data;
  }
  const post = (path, body = {}) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  let pendingSettings = Promise.resolve();
  /** Median duration of completed runs in seconds, or null before the first completed run. */
  function medianSeconds() {
    const durations = runs.filter(r => r.status === 'ok' && r.seconds != null).map(r => r.seconds).sort((a, b) => a - b);
    if (!durations.length) return null;
    const mid = Math.floor(durations.length / 2);
    return durations.length % 2 ? durations[mid] : Math.round((durations[mid - 1] + durations[mid]) / 2);
  }
  function startMessage(missing, hasDate) {
    if (!online) return 'Open the live app to start a run.';
    if (busy) return 'Saving and checking your files…';
    if (invalidSlot === 'config') return 'Check the report date or configuration workbook.';
    if (invalidSlot) return 'Fix the ' + slots.find(s => s[0] === invalidSlot)[1].toLowerCase() + ' file.';
    if (!hasDate) return 'Add a report date to continue.';
    if (missing === 1) return 'Add the ' + requiredSlots.find(([slot]) => !selected.has(slot))[1].toLowerCase() + ' to continue.';
    if (missing) return 'Add ' + missing + ' more files to continue.';
    const median = medianSeconds();
    return median == null ? 'Ready to calculate and save your report.' : 'Ready. A run usually takes about ' + Math.max(1, Math.round(median / 60)) + ' min.';
  }
  function showStamp() {
    const value = $('#sDate').validity.valid ? $('#sDate').value : '';
    $('#dateStamp').textContent = stampText(value);
    $('#dateStamp').classList.toggle('empty', !value);
  }
  /** Ticket checklist entry for one required file: [row class, short note]. */
  function checklistEntry(slot) {
    if (invalidSlot === slot) return busy ? ['', 'Checking…'] : ['bad', 'Check file'];
    const file = selected.get(slot);
    return file ? ['ok', sizeText(file.size)] : ['', 'Missing'];
  }
  function showChecklist() {
    $('#fileChecklist').html = requiredSlots.map(([slot, label]) => {
      const [state, note] = checklistEntry(slot);
      return `<li class="${state}"><span class="ob-tick" aria-hidden="true"></span>${label}<em>${note}</em></li>`;
    }).join('');
  }
  function controls() {
    const missing = requiredSlots.filter(([slot]) => !selected.has(slot)).length;
    const hasDate = !!$('#sDate').value && $('#sDate').validity.valid;
    $('#runBtn').disabled = !online || busy || missing > 0 || !hasDate || !!invalidSlot;
    $('#dateHint').textContent = !hasDate ? 'The day stock was counted.' : 'Sets stock age and the sales cutoff.';
    $('#startReason').textContent = startMessage(missing, hasDate);
    $('#discardDraft').hidden = !draft;
    $('#discardDraft').disabled = busy;
    $('#sLabel').disabled = busy || !online;
    $('#sDate').disabled = busy || !online;
    $('#datePick').disabled = busy || !online;
    $('#datePick').textContent = hasDate ? 'Change' : 'Choose date';
    $('#ticketRun').textContent = draft ? draft.id + ' · Draft' : 'New run';
    $$('#ob-setup input[type="file"]').forEach(input => input.disabled = busy || !online);
    const count = requiredSlots.length - missing;
    $('#fileProg').textContent = count + ' of ' + requiredSlots.length + ' selected';
    $$('#ob-setup .ob-segs i').forEach((segment, i) => segment.classList.toggle('on', i < count));
    showStamp();
    showChecklist();
  }
  $('#runBtn').disabled = true;
  function fileGlyph(slot) {
    const [tag, color] = formats[slot];
    const lines = tag === 'CSV' ? 'M8 18h22M8 23h16M8 28h20' : 'M8 18h22M8 23h22M8 28h22M15.5 15v15M23 15v15';
    return `<svg viewBox="-4 0 46 50" aria-hidden="true"><path class="fg-page" d="M7 1.5h18.6L38.5 14.4V43A5.5 5.5 0 0 1 33 48.5H7A5.5 5.5 0 0 1 1.5 43V7A5.5 5.5 0 0 1 7 1.5Z"/><path class="fg-fold" d="M25.6 1.5V9a5.4 5.4 0 0 0 5.4 5.4h7.5"/><path class="fg-grid" d="${lines}"/><g class="fg-tag"><rect x="-3" y="31.5" width="${tag.length * 6 + 8}" height="12" rx="3" fill="${color}"/><text x="${tag.length * 3 + 1}" y="40.2" text-anchor="middle">${tag}</text></g></svg>`;
  }
  const fileRow = ([slot, label, ext]) => `
    <div class="file" data-slot="${slot}">
      <span class="st">${fileGlyph(slot)}</span>
      <div class="file-body">
        <div class="file-heading"><b>${label}</b><details class="file-info"><summary aria-label="${label} file requirements" title="File requirements">i</summary><div class="file-help">${esc(help[slot])}</div></details></div>
        <span class="file-description" id="${slot}-description">${descriptions[slot]}</span>
        <div class="file-meta" hidden><span class="file-name"></span><span class="file-upload-status"></span></div>
        <span class="file-ext">${ext.split(',').map(e => e.slice(1).toUpperCase()).join(' · ')}</span>
      </div>
      <div class="file-actions">
        <a class="btn sm ghost file-sample" href="${esc(SAMPLES[slot].href)}" download="${esc(SAMPLES[slot].name)}" aria-label="Download ${label.toLowerCase()} sample" title="Download sample · synthetic example"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5M5 19h14"/></svg></a>
        <label class="btn sm"><span class="file-choose">Choose file</span><input type="file" aria-label="${label}" aria-describedby="${slot}-description" accept="${ext}"></label>
      </div>
    </div>`;
  $('#files').html = requiredSlots.map(fileRow).join('');
  $('#configFile').html = fileRow(slots[0]);
  // Requirement notes and the ticket's option popovers close on an outside click or Escape.
  const popovers = '.file-info[open], .ob-pop[open]';
  document.addEventListener('click', event => {
    $$(popovers).forEach(pop => {if (!pop.contains(event.target)) pop.open = false;});
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') $$(popovers).forEach(pop => pop.open = false);
  });
  const openDatePicker = () => {try {$('#sDate').showPicker();} catch {/* Older browsers open the picker from the field itself. */}};
  $('#datePick').onclick = () => {$('#sDate').focus(); openDatePicker();};
  $('#sDate').addEventListener('click', openDatePicker);
  $('#sDate').addEventListener('input', showStamp);
  function showDraft(run, keepFields = false) {
    draft = run;
    selected.clear();
    for (const [slot, file] of Object.entries(run?.files || {})) selected.set(slot, file);
    $$('#ob-setup .file').forEach(el => {
      const file = selected.get(el.dataset.slot);
      el.classList.toggle('done', !!file && !file.generated);
      const metadata = el.querySelector('.file-meta');
      metadata.hidden = !file;
      el.querySelector('.file-name').textContent = file?.generated ? 'Using the date entered above.' : file?.name || '';
      el.querySelector('.file-upload-status').textContent = file && !file.generated ? sizeText(file.size) + ' · Uploaded' : '';
      metadata.tabIndex = file ? 0 : -1;
      el.querySelector('.file-choose').textContent = file && !file.generated ? 'Replace' : 'Choose file';
    });
    if (!keepFields) {
      $('#sDate').value = run?.report_date || '';
      $('#sLabel').value = run && run.label !== 'Untitled run' ? run.label : '';
    }
    controls();
  }
  async function persistSettings() {
    if (!draft) draft = await post('runs', {label: $('#sLabel').value});
    const report_date = $('#sDate').value;
    const label = $('#sLabel').value;
    if (report_date && (draft.report_date !== report_date || draft.label !== (label.trim() || 'Untitled run'))) {
      draft = await post('runs/' + draft.id + '/config', {report_date, label});
    }
    return draft;
  }
  function saveSettings() {
    controls();
    if (!online || busy || !$('#sDate').value || !$('#sDate').validity.valid) return;
    pendingSettings = pendingSettings.then(async () => {
      await persistSettings();
      if (invalidSlot === 'config') invalidSlot = null;
      showDraft(draft, true); error('');
      $('#appNotice').textContent = 'Run settings saved for ' + dateText(draft.report_date) + '.';
      await refresh();
    }).catch(e => {invalidSlot = 'config'; error(e.message); controls();});
  }
  $('#sDate').onchange = saveSettings;
  $('#sLabel').onchange = saveSettings;
  $$('#ob-setup .file').forEach(el => {
    const slot = el.dataset.slot;
    async function choose(file) {
      await pendingSettings;
      if (!file || busy || !online) return;
      const allowed = slots.find(s => s[0] === slot)[2].split(',');
      const suffix = '.' + file.name.split('.').at(-1).toLowerCase();
      if (!allowed.includes(suffix) || !file.size || file.size > 1024 ** 3) {
        invalidSlot = slot;
        error('Choose a nonempty ' + allowed.join(' / ') + ' file, up to 1 GB.');
        $('#appNotice').textContent = 'Upload rejected. Choose a valid file to continue.';
        controls();
        el.querySelector('input').value = '';
        return;
      }
      busy = true; invalidSlot = slot; error(''); controls();
      try {
        if (slot === 'config') {
          if (!draft) draft = await post('runs', {label: $('#sLabel').value});
        } else {
          await persistSettings();
        }
        $('#appNotice').textContent = 'Uploading and checking ' + slots.find(s => s[0] === slot)[1] + '…';
        const meta = await api('runs/' + draft.id + '/files/' + slot, {method:'PUT',headers:{'X-Filename':encodeURIComponent(file.name)},body:file});
        if (slot === 'config') $('#sDate').value = meta.report_date;
        invalidSlot = null; showDraft(meta, true);
        $('#appNotice').textContent = 'Draft ' + draft.id + ' saved. You can return to it later.';
        await refresh();
      } catch (e) {error(e.message); $('#appNotice').textContent = 'Upload rejected. Check the file and try again.';}
      finally {busy = false; el.querySelector('input').value = ''; controls();}
    }
    el.querySelector('input').onchange = e => choose(e.target.files[0]);
    el.ondragover = e => {e.preventDefault(); if (online && !busy) el.classList.add('over');};
    el.ondragleave = () => el.classList.remove('over');
    el.ondrop = e => {e.preventDefault(); el.classList.remove('over'); choose(e.dataTransfer.files[0]);};
  });
  const status = {draft: ['s-idle', 'Incomplete upload'], queued: ['s-idle', 'Queued'], running: ['s-run', 'Running'], ok: ['s-ok', 'Completed'], bad: ['s-bad', 'Failed'], cancelled: ['s-idle', 'Cancelled']};

  function resultsUrl(id) {
    return basePath + '?run=' + encodeURIComponent(id) + '#results';
  }
  function actions(r) {
    const settled = r.plan_state === 'settled';
    return '<div class="run-actions">' + (r.status === 'ok' ? `<a class="btn sm" data-open="${r.id}" href="${esc(resultsUrl(r.id))}">${esc(r.view_label)}</a><a class="btn sm ghost" href="${apiUrl('runs/' + r.id + '/report.xlsx')}" download>Action plan input file</a>` +
      (settled ? `<button class="btn sm" data-reopen="${r.id}">Reopen</button>` : r.out_of_date ? '' : `<button class="btn sm" data-plan="${r.id}">Upload action plan</button><button class="btn sm" data-settle="${r.id}">Settle month</button>`) : '') +
      `<button class="btn sm ghost" data-log="${r.id}">Log</button> ` +
      (['running', 'queued'].includes(r.status) ? `<button class="btn sm ghost" data-cancel="${r.id}">Cancel</button>` : `<button class="btn sm ghost" data-archive="${r.id}" data-value="${!r.archived}">${r.archived ? 'Restore' : 'Archive'}</button>`) +
      (['ok','bad','cancelled'].includes(r.status) && !settled ? ` <button class="btn sm ghost" data-delete="${r.id}">Delete</button>` : '') + '</div>';
  }
  function cycleDialog(title, content, label, action) {
    const dialog = $('#planDialog');
    $('#planTitle').textContent = title;
    $('#planBody').html = content;
    $('#planDialogError').hidden = true;
    $('#planConfirm').textContent = label;
    $('#planConfirm').disabled = false;
    $('#planCancel').disabled = false;
    $('#planCancel').onclick = () => dialog.close();
    dialog.oncancel = e => {if ($('#planCancel').disabled) e.preventDefault();};
    $('#planConfirm').onclick = async () => {
      $('#planConfirm').disabled = true;
      $('#planCancel').disabled = true;
      $('#planDialogError').hidden = true;
      try {await action();}
      catch (e) {
        $('#planDialogError').textContent = e.message;
        $('#planDialogError').hidden = false;
      } finally {
        $('#planConfirm').disabled = false;
        $('#planCancel').disabled = false;
      }
    };
    if (!dialog.open) dialog.showModal();
  }
  function reloadReport() {
    if (reloadingReport) return;
    reloadingReport = true;
    location.reload();
  }
  async function cycleDone(meta, message) {
    $('#planDialog').close();
    $('#appNotice').textContent = message + (meta.plan_state === 'settled' && !meta.settlement.commit.ok ? ' · settled, not committed' : '');
    if (loadedRun?.id === meta.id) reloadReport();
    else await refresh();
  }
  function uploadPlan(id) {
    cycleDialog('Upload action plan', '<p>Choose the file returned by sales, including its lm_ columns. Only action_plan and mitigation_status will be applied to this run’s batches.</p><input id="returnedPlan" type="file" accept=".xlsx" aria-label="Returned action plan">', 'Check file', async () => {
      const file = $('#returnedPlan').files[0];
      if (!file) throw new Error('Choose the returned Excel file.');
      const check = await api('runs/' + id + '/action-plan/check', {method:'PUT', headers:{'X-Filename':encodeURIComponent(file.name)}, body:file});
      cycleDialog('Check returned plan', `<p>${esc(file.name)}</p><dl class="plan-checks"><dt>Batches matched</dt><dd>${f(check.batches_matched)}</dd><dt>Blank action plan remarks</dt><dd>${f(check.blank_remarks)}</dd><dt>Blank statuses</dt><dd>${f(check.blank_statuses)}</dd><dt>Unrecognised statuses</dt><dd>${f(check.unrecognised_statuses)}</dd></dl>${check.unrecognised_values.length ? '<p class="plan-warning">Unrecognised values: ' + esc(check.unrecognised_values.join(', ')) + '</p>' : ''}<p>Blank remarks, blank statuses and unrecognised values are warnings. Accepting replaces the previous plan and rebuilds the view.</p>`, 'Accept action plan', async () => {
        const meta = await post('runs/' + id + '/action-plan/accept', {token:check.token});
        await cycleDone(meta, id + ' · Action plan view (not settled)');
      });
    });
  }
  async function settleMonth(id) {
    try {
      const check = await api('runs/' + id + '/settlement-check');
      cycleDialog('Settle month', `<p><b>${esc(check.month)}</b> · ${f(check.batches)} batches · ${f(check.without_status)} without a status.</p><p>This publishes the frozen snapshot to history and archives other runs for this month.</p>${check.without_plan ? '<p class="plan-warning"><b>No action plan has been uploaded.</b> The final file will contain carried-over statuses.</p><label class="plan-confirmation"><input id="settleWithoutPlan" type="checkbox">I confirm settlement without a returned plan, using carried-over statuses.</label>' : ''}`, check.without_plan ? 'Settle without a plan' : 'Confirm settlement', async () => {
        if (check.without_plan && !$('#settleWithoutPlan').checked) throw new Error('Confirm settlement without a returned plan.');
        const meta = await post('runs/' + id + '/settle', {confirm:true, snapshot:check.snapshot, confirm_without_plan:check.without_plan});
        await cycleDone(meta, id + ' · Final view');
      });
      if (check.without_plan) {
        $('#planConfirm').disabled = true;
        $('#settleWithoutPlan').onchange = e => $('#planConfirm').disabled = !e.target.checked;
      }
    } catch (e) {error(e.message);}
  }
  function reopenMonth(id) {
    cycleDialog('Reopen month', '<p>Remove this month’s settled history in a new local commit and return to the earlier view. Later runs that used this month will be marked out of date.</p>', 'Confirm reopen', async () => {
      const meta = await post('runs/' + id + '/reopen', {confirm:true});
      await cycleDone(meta, id + ' reopened' + (!meta.reopened.commit.ok ? ' · history removal not committed' : ''));
    });
  }
  function progress(r) {
    if (r.status === 'ok') return r.plan_state === 'settled' && !r.settlement?.commit?.ok ? 'settled, not committed' : r.view_label;
    if (r.status !== 'running') return r.step || status[r.status][1];
    const elapsed = r.started ? Math.max(0, Math.floor((Date.now() - Date.parse(r.started)) / 1000)) : 0;
    const median = medianSeconds();
    return (r.step || 'Running') + ' · ' + elapsed + ' s elapsed' + (median == null ? '' : ' · typical ' + median + ' s');
  }
  function summary(r) {
    const s = r.summary;
    if (!s || s.total_closing_stock_bdt == null) return '—';
    const money = n => n == null ? '—' : (n < 0 ? '−' : '') + Math.abs(n / 1e6).toLocaleString('en-GB', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + 'M BDT';
    const val = n => n == null ? '—' : Number(n).toLocaleString('en-GB');
    return `<details data-summary="${r.id}"><summary>View summary</summary><div>Stock: ${money(s.total_closing_stock_bdt)} · exposure: ${money(s.exposure_bdt)}<br>Previous report: ${esc(dateText(s.previous_report_date))}<br>Stock change: ${money(s.stock_change_bdt)} · exposure change: ${money(s.exposure_change_bdt)}<br>Materials missing hierarchy: ${val(s.materials_missing_hierarchy)} · batches missing a stock value: ${val(s.batches_missing_value)}<br>Sales months capped: ${val(s.winsorized_months)} · carried labels: ${val(s.mitigation_carried_batches)} · unlabelled batches: ${val(s.mitigation_unlabelled_batches)}</div></details>`;
  }
  const columns = [
    {k:'id',h:'Run',r:r=>r.id,cls:'mono'},
    {k:'label',h:'Label',r:r=>`${esc(r.label)}<div class="run-secondary">${esc(r.started_by || 'local')}${r.seconds == null ? '' : ' · ' + r.seconds + ' s'}</div>`,cls:'name'},
    {k:'report_date',h:'Report date',r:r=>dateText(r.report_date),cls:'dim'},
    {k:'rows',h:'Batches',num:1,r:r=>r.rows == null ? '—' : f(r.rows)},
    {k:'summary',h:'Summary',r:summary},
    {k:'status',h:'Status',r:r=>`<span class="dot ${status[r.status][0]}">${esc(progress(r))}</span>${r.out_of_date ? `<div class="plan-stale">Out of date · ${esc(r.out_of_date.month)} reopened</div>` : ''}${r.reopened && !r.reopened.commit.ok ? '<div class="plan-stale">History removal not committed</div>' : ''}${r.error ? `<div class="badc">${esc(r.error)}</div>` : ''}`},
    {k:'actions',h:'Results and actions',r:actions},
  ];
  function drawRuns() {
    const active = runs.filter(r => !r.archived), archived = runs.filter(r => r.archived);
    function renderList(target, rows, empty) {
      $(target).html = rows.length ? `<div class="runs-scroll"><table class="runs-table"><thead><tr>${columns.map(c=>`<th scope="col">${esc(c.h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${columns.map(c=>`<td>${c.r(r)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : `<p>${empty}</p>`;
    }
    const openSummaries = new Set($$('[data-summary][open]').map(el => el.dataset.summary));
    renderList('#runsT', active, 'No runs yet. Set a report date, upload three source files and choose Start run.');
    renderList('#archiveT', archived, 'Archived runs appear here. Only settled snapshots feed later history.');
    $$('[data-summary]').forEach(el => {el.open = openSummaries.has(el.dataset.summary);});
    $('#runsN').textContent = active.length || ''; $('#archN').textContent = archived.length || '';
    const running = runs.filter(r => ['queued', 'running'].includes(r.status)).length;
    $('#runCount').textContent = running ? running + ' running' : '';
    const newest = runs.find(r => r.status !== 'draft');
    document.title = running ? 'Running · FSNE' : newest?.status === 'ok' ? 'Done · FSNE' : 'FSNE';
    $$('[data-plan]').forEach(b => b.onclick = () => uploadPlan(b.dataset.plan));
    $$('[data-settle]').forEach(b => b.onclick = () => settleMonth(b.dataset.settle));
    $$('[data-reopen]').forEach(b => b.onclick = () => reopenMonth(b.dataset.reopen));
    $$('[data-archive]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try {await post('runs/' + b.dataset.archive + '/' + (b.dataset.value === 'true' ? 'archive' : 'restore')); await refresh();}
      catch (e) {error(e.message); b.disabled = false;}
    });
    $$('[data-cancel]').forEach(b => b.onclick = async () => {
      if (!confirm('Cancel this run?')) return;
      try {await post('runs/' + b.dataset.cancel + '/cancel'); await refresh();} catch (e) {error(e.message);}
    });
    $$('[data-delete]').forEach(b => b.onclick = async () => {
      if (!confirm('Delete this run and its files? Later runs may have used it as history.')) return;
      try {await post('runs/' + b.dataset.delete + '/delete'); await refresh();} catch (e) {error(e.message);}
    });
    $$('[data-log]').forEach(b => b.onclick = async () => {
      try {
        const response = await fetch(apiUrl('runs/' + b.dataset.log + '/log'), {cache:'no-store'});
        if (!response.ok) throw new Error('Could not load the run log');
        const raw = await response.text();
        const run = runs.find(r => r.id === b.dataset.log);
        const steps = raw.split('\n').filter(line => line.startsWith('=== Step'));
        $('#runLogSummary').textContent = [...steps, ...(run?.error ? ['Error: ' + run.error] : [])].join('\n') || 'No pipeline steps have run yet.';
        $('#runLog').textContent = raw || 'No technical details yet.';
        $('#runLogTitle').textContent = b.dataset.log + ' log';
        $('#runLogPanel').hidden = false;
        $('#runLogPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
      } catch (e) {error(e.message);}
    });
  }
  $('#closeRunLog').onclick = () => $('#runLogPanel').hidden = true;
  async function refresh() {
    if (reloadingReport) return;
    runs = await api('runs'); drawRuns();
    const current = runs.find(r => r.id === loadedRun?.id);
    const version = r => JSON.stringify([r?.plan_state, r?.plan_upload?.revision, r?.out_of_date, r?.settlement?.commit, r?.reopened]);
    if (current && version(current) !== version(loadedRun)) {reloadReport(); return;}
    const run = runs.find(r => r.id === trackedRun);
    if (run && !busy && !draft && ['ok', 'bad', 'cancelled'].includes(run.status)) {
      $('#appNotice').textContent = run.id + (run.status === 'ok' ? ' completed. Open results in Runs.' : run.status === 'bad' ? ' failed. See Runs for details.' : ' was cancelled.');
    }
  }
  async function poll() {
    if (polling || reloadingReport) return;
    polling = true;
    try {await refresh();}
    catch (e) {error('Could not refresh runs: ' + e.message);}
    finally {
      polling = false;
      if (!reloadingReport) setTimeout(poll, 2500);
    }
  }
  $('#discardDraft').onclick = async () => {
    await pendingSettings;
    if (!draft || busy || !confirm('Discard this draft and its uploaded files?')) return;
    busy = true; controls();
    try {await post('runs/' + draft.id + '/discard'); invalidSlot = null; showDraft(null); $('#sLabel').value = ''; error(''); $('#appNotice').textContent = 'Draft discarded. Upload files to start a new run.'; await refresh();}
    catch (e) {error(e.message);}
    finally {busy = false; controls();}
  };
  $('#runBtn').onclick = async () => {
    await pendingSettings;
    if (busy || !online || !draft || invalidSlot || !$('#sDate').value || requiredSlots.some(([slot]) => !selected.has(slot))) return;
    const previous = runs.find(r => r.status === 'ok' && r.report_date === $('#sDate').value);
    if (previous && !confirm('A completed run already exists for this report date (' + previous.id + '). Start another run? History changes only when you settle it.')) return;
    busy = true; error(''); controls();
    try {
      await persistSettings();
      const id = draft.id;
      try {
        await post('runs/' + id + '/start', {label: $('#sLabel').value});
      } catch (e) {
        if (!e.previousUnsettled) throw e;
        if (!confirm(e.message + '\n\nStart anyway? Last-month entry columns will be empty and no statuses will carry over.')) return;
        await post('runs/' + id + '/start', {label: $('#sLabel').value, override_unsettled:true});
      }
      trackedRun = id;
      showDraft(null); $('#sLabel').value = '';
      $('#appNotice').textContent = id + ' accepted. Follow its progress in Runs.';
      $$('#obTabs .tab')[1].click();
      await refresh();
    } catch (e) {error(e.message);}
    finally {busy = false; controls(); refresh().catch(() => {});}
  };
  async function init() {
    try {
      const context = await api('context' + location.search);
      loadedRun = context.run;
      $('#reportIdentity').textContent = loadedRun ? loadedRun.id + ' · ' + loadedRun.label : HAS_REPORT ? 'Sample' : 'No report yet';
      $('#repRun').textContent = HAS_REPORT ? STATUS.labels.at(-1) + ' snapshot' : '';
      $('.who b').textContent = context.user || 'local';
      online = true;
      $('#appNotice').textContent = loadedRun ? 'Upload your files to calculate a new run.' : HAS_REPORT ? 'Reports currently show the bundled sample. Upload your files to calculate a new run.' : 'No report yet. Set a report date and upload three source files to calculate your first run.';
      await refresh();
      showDraft(runs.find(r => r.status === 'draft' && !r.archived) || null);
      if (draft) $('#appNotice').textContent = 'Draft ' + draft.id + ' resumed. Continue uploading files or start the run.';
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
