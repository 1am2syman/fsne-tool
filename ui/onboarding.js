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
    config: 'Fill the FSNE month from a workbook.',
    secondary: 'Monthly sales through the month before FSNE.',
    hierarchy: 'Material codes, products and parent mapping.',
    shelflife: 'Stock, expiry dates and DB Landing value.',
  };
  const help = {
    config: 'Optional import: uses the month of config_table.starting_date as the FSNE month.',
    secondary: 'Monthly sales history ending one month before FSNE, including Month-Year, town, material, batch and sales volume. The latest sales month sets the next FSNE month automatically. Example: Mode_Sec_Month on Month.xlsb',
    hierarchy: 'Excel product hierarchy with material code, description, volume and parent material code. Example: PH_FG.xlsx',
    shelflife: 'CSV stock and shelf-life report for the report month, including material, batch, town, expiry, stock and DB Landing value (the source of stock value in BDT). Example: Product Shelflife Report Multiple FSNE  (03-08-2026)  (03-09-2026).csv',
  };
  const formats = {config: ['XLSX', '#1E7A4C'], secondary: ['XLSB', '#1E7A4C'], hierarchy: ['XLSX', '#1E7A4C'], shelflife: ['CSV', '#3D5A99']};
  const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
  let online = false, busy = false, runs = [], polling = false, reloadingReport = false, loadedRun = null, draft = null, invalidSlot = null, trackedRun = null;
  // The ticket stamp spells the month out so 08/09 can never be read the wrong way round.
  const stampText = value => value ? value.slice(8, 10) + ' ' + MONTHS[Number(value.slice(5, 7)) - 1] + ' ' + value.slice(0, 4) : 'DD MMM YYYY';
  const monthStampText = value => value ? MONTHS[Number(value.slice(5, 7)) - 1] + ' ' + value.slice(0, 4) : 'MMM YYYY';
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
    if (invalidSlot === 'config') return 'Check the FSNE month or configuration workbook.';
    if (invalidSlot) return 'Fix the ' + slots.find(s => s[0] === invalidSlot)[1].toLowerCase() + ' file.';
    if (!hasDate) return 'Upload secondary sales to detect the FSNE month, or choose it.';
    if (missing === 1) return 'Add the ' + requiredSlots.find(([slot]) => !selected.has(slot))[1].toLowerCase() + ' to continue.';
    if (missing) return 'Add ' + missing + ' more files to continue.';
    const median = medianSeconds();
    return median == null ? 'Ready to calculate and save your report.' : 'Ready. A run usually takes about ' + Math.max(1, Math.round(median / 60)) + ' min.';
  }
  function showStamp() {
    const value = $('#sDate').validity.valid ? $('#sDate').value : '';
    $('#dateStamp').textContent = monthStampText(value);
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
    let dateHint = 'Detected from secondary sales + 1 month.';
    if (hasDate) dateHint = draft?.configuration_source === 'secondary' ? 'Detected from secondary sales · uses month-end.' : 'Calculations use this month’s last day.';
    $('#dateHint').textContent = dateHint;
    $('#startReason').textContent = startMessage(missing, hasDate);
    $('#discardDraft').hidden = !draft;
    $('#discardDraft').disabled = busy;
    $('#sLabel').disabled = busy || !online;
    $('#sDate').disabled = busy || !online;
    $('#datePick').disabled = busy || !online;
    $('#datePick').textContent = hasDate ? 'Change' : 'Choose month';
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
      el.querySelector('.file-name').textContent = file?.generated ? 'Using the FSNE month above.' : file?.name || '';
      el.querySelector('.file-upload-status').textContent = file && !file.generated ? sizeText(file.size) + ' · Uploaded' : '';
      metadata.tabIndex = file ? 0 : -1;
      el.querySelector('.file-choose').textContent = file && !file.generated ? 'Replace' : 'Choose file';
    });
    if (!keepFields) {
      $('#sDate').value = run?.report_date?.slice(0, 7) || '';
      $('#sLabel').value = run && run.label !== 'Untitled run' ? run.label : '';
    }
    controls();
  }
  async function persistSettings() {
    if (!draft) draft = await post('runs', {label: $('#sLabel').value});
    const fsne_month = $('#sDate').value;
    const label = $('#sLabel').value;
    if (fsne_month && (draft.report_date?.slice(0, 7) !== fsne_month || !draft.fsne_month || draft.label !== (label.trim() || 'Untitled run'))) {
      draft = await post('runs/' + draft.id + '/config', {fsne_month, label});
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
      $('#appNotice').textContent = 'FSNE month saved: ' + monthText(draft.report_date.slice(0, 7)) + '.';
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
        let meta = await api('runs/' + draft.id + '/files/' + slot, {method:'PUT',headers:{'X-Filename':encodeURIComponent(file.name)},body:file});
        if (slot === 'config') meta = await post('runs/' + draft.id + '/config', {fsne_month: meta.report_date.slice(0, 7), label: $('#sLabel').value});
        $('#sDate').value = meta.report_date?.slice(0, 7) || '';
        invalidSlot = null; showDraft(meta, true);
        $('#appNotice').textContent = meta.configuration_source === 'secondary' ? 'Draft ' + draft.id + ' saved. FSNE month detected: ' + monthText(meta.report_date.slice(0, 7)) + '.' : 'Draft ' + draft.id + ' saved. You can return to it later.';
        await refresh();
      } catch (e) {error(e.message); $('#appNotice').textContent = 'Upload rejected. Check the file and try again.';}
      finally {busy = false; el.querySelector('input').value = ''; controls();}
    }
    el.querySelector('input').onchange = e => choose(e.target.files[0]);
    el.ondragover = e => {e.preventDefault(); if (online && !busy) el.classList.add('over');};
    el.ondragleave = () => el.classList.remove('over');
    el.ondrop = e => {e.preventDefault(); el.classList.remove('over'); choose(e.dataTransfer.files[0]);};
  });
  /* ---- Runs and archive: each run renders as the stub of the ticket issued in Setup. ---- */
  const ICON = {
    open: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5"/></svg>',
    download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m-4.5-4.5L12 15l4.5-4.5M5 19h14"/></svg>',
    upload: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 19h14"/></svg>',
    settle: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
    reopen: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 7.6-1.7"/></svg>',
    log: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.5h7l4 4v13H7zM14 3.5v4h4M10 12h5m-5 4h5"/></svg>',
    archive: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="4.5" rx="1"/><path d="M5.5 9v10.5h13V9M10 13h4"/></svg>',
    trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 7h15M10 7V4.5h4V7M6.5 7l1 13h9l1-13"/></svg>',
    stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 2.8 19.5h18.4zM12 10v4.5M12 17v.2"/></svg>',
  };
  /** Local day of an ISO timestamp, with the year only when it is not the current one. */
  const shortDate = value => {
    const day = new Date(value);
    return day.toLocaleDateString('en-GB', {day: 'numeric', month: 'short', ...(day.getFullYear() === new Date().getFullYear() ? {} : {year: 'numeric'})});
  };
  const stamp = value => new Date(value).toLocaleString('en-GB', {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'});
  const monthText = month => new Date(month + '-01T00:00:00Z').toLocaleDateString('en-GB', {month: 'long', year: 'numeric', timeZone: 'UTC'});
  const duration = seconds => seconds < 60 ? seconds + ' s' : Math.floor(seconds / 60) + ' min' + (seconds % 60 ? ' ' + seconds % 60 + ' s' : '');
  const elapsed = iso => duration(Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000)));
  const plural = (n, one, many = one + 's') => f(n) + ' ' + (n === 1 ? one : many);
  const millions = n => Math.abs(n / 1e6).toLocaleString('en-GB', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + 'M';
  const sentence = text => text ? text[0].toUpperCase() + text.slice(1) : '';

  function resultsUrl(id) {
    return basePath + '?run=' + encodeURIComponent(id) + '#results';
  }
  /** Row and pane status for one run, from its stored lifecycle facts: [badge class, text]. */
  function runState(r) {
    switch (r.status) {
      case 'draft': return ['', 'Draft'];
      case 'queued': return ['', 'Queued'];
      case 'running': {
        const step = /^Step (\d+) of (\d+)/.exec(r.step || '');
        return ['is-run', step ? `Step ${step[1]} of ${step[2]}` : 'Running'];
      }
      case 'bad': return ['is-bad', 'Failed'];
      case 'cancelled': return ['', 'Cancelled'];
    }
    if (r.plan_state === 'settled') return r.settlement?.commit?.ok ? ['is-ok', 'Settled'] : ['is-warn', 'Not committed'];
    if (r.superseded_by) return ['', 'Replaced by ' + r.superseded_by];
    if (r.out_of_date) return ['is-warn', 'Out of date'];
    // Archived runs are set aside: describe them, do not nudge.
    if (r.archived) return ['', r.plan_upload ? 'Plan returned' : 'Pre-action plan'];
    return r.plan_upload ? ['is-next', 'Ready to settle'] : ['', 'Awaiting plan'];
  }
  /** A report month's headline from all of its runs, active and archived: [node, badge class, badge, note HTML].
   *  The archive only reports whether the month is in history; next steps belong to the Runs tab. */
  function monthState(month, monthRuns, tab) {
    const id = run => `<span class="run-id-text">${esc(run.id)}</span>`;
    if (!month) return ['todo', '', 'No report date', 'Drafts without a report date yet.'];
    const settled = monthRuns.find(r => r.plan_state === 'settled');
    if (settled) {
      return settled.settlement?.commit?.ok ? ['done', 'is-ok', 'Settled', `In history from ${id(settled)}.`]
        : ['warn', 'is-warn', 'Not committed', `Settled from ${id(settled)}, but the local history commit failed.`];
    }
    if (tab === 'archive') return ['todo', '', 'Not settled', 'Not in history yet.'];
    const current = monthRuns.filter(r => r.status === 'ok' && !r.out_of_date && !r.archived);
    const others = current.length > 1 ? ' Settling archives the month’s other runs.' : '';
    const planned = current.find(r => r.plan_upload);
    if (planned) return ['current', 'is-next', 'Ready to settle', `Settle ${id(planned)} to put this month in history.` + others];
    if (current.length) return ['current', '', 'Awaiting plan', `Send ${id(current[0])}’s input file to sales, then upload what they return.` + others];
    if (monthRuns.some(r => ['queued', 'running'].includes(r.status))) return ['todo', 'is-run', 'Calculating', 'Not in history yet.'];
    return ['todo', '', 'Not settled', 'No completed run for this month yet.'];
  }
  /** One step of the action plan cycle in the pane. `meta` holds escaped HTML lines. */
  function runStep(state, name, meta, actions = '') {
    const spoken = {done: 'done', current: 'next step', todo: 'not started', warn: 'needs attention', skipped: 'skipped', locked: 'locked'}[state];
    return `<li class="is-${state}"><span class="run-node" aria-hidden="true"></span><div><b>${name}</b>${state === 'current' ? '<em aria-hidden="true">Next</em>' : ''}<span class="sr-only">, ${spoken}</span>`
      + `${meta.filter(Boolean).map(line => `<p>${line}</p>`).join('')}${actions ? `<div class="run-step-actions">${actions}</div>` : ''}</div></li>`;
  }
  /** Calculated → action plan returned → month settled, with each action on the step it advances. */
  function runSteps(r) {
    const id = esc(r.id), plan = r.plan_upload, settlement = r.settlement || {};
    const settled = r.plan_state === 'settled', locked = !!r.out_of_date && !settled;
    const inputFile = `<a class="btn run-btn" href="${esc(apiUrl('runs/' + r.id + '/report.xlsx'))}" download>${ICON.download}Action plan input file</a>`;
    const upload = settled || locked ? '' : `<button type="button" class="btn run-btn" data-plan="${id}"${plan ? ' title="Uploading again replaces the earlier plan"' : ''}>${ICON.upload}${plan ? 'Replace plan' : 'Upload action plan'}</button>`;
    const calculated = runStep('done', 'Calculated', [esc(shortDate(r.started || r.created)) + (r.seconds == null ? '' : ' · took ' + duration(r.seconds))]);
    let returned;
    if (plan) returned = runStep('done', 'Action plan returned', [`${esc(shortDate(plan.time))} · ${esc(plan.uploader || 'local')}`, `<span class="run-file" title="${esc(plan.filename)}">${esc(plan.filename)}</span>`], inputFile + upload);
    else if (settled) returned = runStep('skipped', 'Action plan returned', ['Skipped: settled with carried-over statuses'], inputFile);
    else if (locked) returned = runStep('locked', 'Action plan returned', ['Locked while this run is out of date'], inputFile);
    else returned = runStep('current', 'Action plan returned', ['Send the input file to sales, then upload the file they return.'], inputFile + upload);
    let close;
    if (settled) {
      const committed = settlement.commit?.ok;
      close = runStep(committed ? 'done' : 'warn', 'Month settled', [
        `${esc(shortDate(settlement.time))} · history/${esc(settlement.history_file)}`,
        committed ? 'Committed locally' : `<span class="run-warn" title="${esc(settlement.commit?.error || '')}">Settled, not committed</span>`,
      ], `<button type="button" class="btn run-btn" data-reopen="${id}">${ICON.reopen}Reopen</button>`);
    } else if (locked) {
      close = runStep('locked', 'Month settled', ['Calculate a new run to settle this month.']);
    } else {
      const reopened = r.reopened ? `Reopened ${esc(shortDate(r.reopened.time))}` + (r.reopened.commit?.ok ? '' : ` · <span class="run-warn" title="${esc(r.reopened.commit?.error || '')}">history removal not committed</span>`) : '';
      close = runStep(plan ? 'current' : 'todo', 'Month settled', [reopened, plan ? 'Publishes this snapshot to history.' : 'Settling without a returned plan keeps carried-over statuses.'],
        `<button type="button" class="btn run-btn" data-settle="${id}">${ICON.settle}Settle month</button>`);
    }
    return `<ol class="run-steps" aria-label="Action plan cycle for ${id}">${calculated}${returned}${close}</ol>`;
  }
  /** The figures a completed run produced. Older runs show only what they recorded. */
  function runFacts(r) {
    const s = r.summary || {}, checks = r.plan_upload?.checks;
    const missing = '<dd class="is-missing" title="Not recorded for this run">—</dd>';
    const fact = (label, dd, help = '') => `<dt${help ? ` title="${esc(help)}"` : ''}>${label}</dt>${dd ?? missing}`;
    const change = n => {
      if (n == null || !s.previous_report_date) return '';
      return `<small class="${n > 0 ? 'up' : n < 0 ? 'down' : ''}">${n > 0 ? '+' : n < 0 ? '−' : '±'}${millions(n)}</small>`;
    };
    const money = (value, delta) => value == null ? null : `<dd>${millions(value)} BDT${change(delta)}</dd>`;
    let statuses = null;
    if (checks) statuses = `<dd>${f(checks.batches_matched - checks.blank_statuses)} filled<span>${f(checks.blank_statuses)} blank · ${f(checks.unrecognised_statuses)} unrecognised</span></dd>`;
    else if (s.mitigation_carried_batches != null) statuses = `<dd>${f(s.mitigation_carried_batches)} carried<span>${f(s.mitigation_unlabelled_batches)} new, unlabelled</span></dd>`;
    let gaps = null;
    if (s.materials_missing_hierarchy != null) {
      const found = [s.materials_missing_hierarchy ? plural(s.materials_missing_hierarchy, 'unmapped material') : '', s.batches_missing_value ? plural(s.batches_missing_value, 'unvalued batch', 'unvalued batches') : ''].filter(Boolean);
      gaps = `<dd class="is-text">${found.join(' · ') || 'None found'}${s.winsorized_months == null ? '' : `<span>${plural(s.winsorized_months, 'sales month')} capped</span>`}</dd>`;
    }
    const note = s.previous_report_date && (s.stock_change_bdt != null || s.exposure_change_bdt != null) ? `<p class="run-facts-note">Changes are against the ${esc(dateText(s.previous_report_date))} report.</p>` : '';
    return `<dl class="run-facts">${fact('Batches', r.rows == null ? null : `<dd>${f(r.rows)}</dd>`)}${fact('Closing stock', money(s.total_closing_stock_bdt, s.stock_change_bdt))}`
      + `${fact('Exposure', money(s.exposure_bdt, s.exposure_change_bdt))}${fact(checks ? 'Returned statuses' : 'Statuses', statuses)}`
      + `${fact('Data gaps', gaps, 'Materials missing from the product hierarchy, batches without a DB Landing value, and sales months capped as outliers')}</dl>${note}`;
  }
  function runProgress(r) {
    const step = /^Step (\d+) of (\d+): (.*)$/.exec(r.step || '');
    const [n, total, text] = step ? [Number(step[1]), Number(step[2]), step[3]] : [0, 5, r.step || runState(r)[1]];
    const done = r.status === 'running' ? n - 1 : 0, median = medianSeconds();
    const segments = Array.from({length: total}, (_, i) => `<i class="${i < done ? 'on' : i === done && step && r.status === 'running' ? 'now' : ''}"></i>`).join('');
    return `<div class="run-progress"><div class="run-progress-line">${step ? `<b>Step ${n} of ${total}</b>` : ''}<span>${esc(sentence(text))}</span>`
      + `${r.status === 'running' && r.started ? `<span class="run-elapsed"><span data-since="${esc(r.started)}">${elapsed(r.started)}</span> elapsed</span>` : ''}</div>`
      + `<div class="run-segs" role="progressbar" aria-label="Pipeline steps finished" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${Math.max(0, done)}">${segments}</div>`
      + `${median == null ? '' : `<p>A run usually takes about ${Math.max(1, Math.round(median / 60))} min.</p>`}</div>`;
  }
  function runDraft(r) {
    const have = requiredSlots.filter(([slot]) => r.files?.[slot]).length;
    return `<div class="run-progress"><div class="run-progress-line"><b>${have} of ${requiredSlots.length} source files</b><span>${r.report_date ? 'FSNE month set' : 'No FSNE month yet'}</span></div>`
      + `<div class="run-segs" aria-hidden="true">${requiredSlots.map(([slot]) => `<i class="${r.files?.[slot] ? 'on' : ''}"></i>`).join('')}</div></div>`;
  }
  function runNotices(r) {
    const notes = [];
    if (r.out_of_date && r.plan_state !== 'settled') notes.push(['warn', monthText(r.out_of_date.month) + ' was reopened after this run used it. Calculate a new run for current figures.']);
    if (r.history_override) notes.push(['info', 'Started without last month’s settled history: last-month columns are empty and no statuses carried over.']);
    return notes.map(([kind, text]) => `<p class="run-notice is-${kind}">${ICON.alert}<span>${esc(text)}</span></p>`).join('');
  }
  function runBody(r) {
    switch (r.status) {
      case 'ok': return runSteps(r) + runFacts(r);
      case 'bad': return `<div class="run-failure">${ICON.alert}<div><b>Run failed</b><p>${esc(r.error || 'The run stopped without an error message. Its log has the details.')}</p></div></div>`;
      case 'cancelled': return '<p class="run-quiet">Cancelled before it finished.</p>';
      case 'draft': return runDraft(r);
      default: return runProgress(r);
    }
  }
  /** The pane's one way into the run, then housekeeping. Each data-* hook appears once. */
  function runPaneActions(r) {
    const id = esc(r.id), live = ['running', 'queued'].includes(r.status);
    let lead = '';
    if (r.status === 'ok') lead = `<a class="btn run-open" data-open="${id}" href="${esc(resultsUrl(r.id))}">${esc(r.view_label)}${ICON.open}</a>`;
    else if (r.status === 'bad') lead = `<button type="button" class="btn run-open is-outline" data-log="${id}">${ICON.log}View log</button>`;
    else if (live) lead = `<button type="button" class="btn run-open is-outline is-stop" data-cancel="${id}"${r.cancel_requested ? ' disabled' : ''}>${ICON.stop}Cancel run</button>`;
    else if (r.status === 'draft' && !r.archived && draft?.id === r.id) lead = `<button type="button" class="btn run-open is-outline" data-resume>Continue in Setup${ICON.open}</button>`;
    // Housekeeping is icon-only beside the lead action so the pane fits a laptop screen without scrolling.
    const icon = (attrs, svg, label, cls = '') => `<button type="button" class="btn${cls}" ${attrs} aria-label="${label}" title="${label}">${svg}</button>`;
    const house = [
      r.status === 'bad' ? '' : icon(`data-log="${id}"`, ICON.log, 'Log'),
      live ? '' : icon(`data-archive="${id}" data-value="${!r.archived}"`, ICON.archive, r.archived ? 'Restore' : 'Archive'),
      ['ok', 'bad', 'cancelled'].includes(r.status) && r.plan_state !== 'settled' ? icon(`data-delete="${id}"`, ICON.trash, 'Delete', ' is-danger') : '',
    ].join('');
    return lead || house ? `<div class="run-lead">${lead}${house ? `<div class="run-house">${house}</div>` : ''}</div>` : '';
  }
  function runPane(r, paneId) {
    const [badgeClass, badge] = runState(r), actions = runPaneActions(r);
    const by = esc(r.started_by || 'local') + ' · ' + (r.status === 'draft' ? 'draft created ' + esc(stamp(r.created)) : 'started ' + esc(stamp(r.started || r.created)))
      + (r.archived ? ' · archived ' + esc(shortDate(r.archived)) : '');
    return `<section class="run-pane${r.archived ? ' is-archived' : ''}" id="${paneId}" aria-label="Selected run ${esc(r.id)}"><div class="run-pane-top">`
      + `<p class="run-pane-date${r.report_date ? '' : ' empty'}"><span class="sr-only">FSNE report date </span>${esc(stampText(r.report_date))}</p><p class="run-pane-eyebrow">${esc(r.id)}</p></div>`
      + `<h2>${esc(r.label)}</h2><p class="run-pane-by">${by}</p>${r.status === 'ok' ? '' : `<p style="margin-top:12px"><span class="run-badge ${badgeClass}">${esc(badge)}</span></p>`}`
      + `${actions}${runNotices(r)}${runBody(r)}</section>`;
  }
  function runRow(r, selectedId, paneId, fresh) {
    const [badgeClass, badge] = runState(r), s = r.summary || {};
    let figure = '';
    if (s.total_closing_stock_bdt != null) figure = `${millions(s.total_closing_stock_bdt)} BDT<small>closing stock</small>`;
    else if (r.rows != null) figure = `${f(r.rows)}<small>batches</small>`;
    const quiet = r.superseded_by || ['cancelled', 'draft'].includes(r.status);
    return `<button type="button" class="run-row${quiet ? ' is-quiet' : ''}${fresh ? ' is-new' : ''}" data-select="${esc(r.id)}" aria-current="${r.id === selectedId}" aria-controls="${paneId}">`
      + `<span><b>${esc(r.label)}</b><span class="run-row-by"><span>${esc(r.id)}</span> · ${esc(r.started_by || 'local')} · ${r.status === 'draft' ? 'draft' : 'started'} ${esc(shortDate(r.started || r.created))}</span></span>`
      + `<span class="run-badge ${badgeClass}">${esc(badge)}</span><span class="run-row-fig">${figure}</span></button>`;
  }
  /** Report months newest first, with undated drafts on top; runs keep their newest-first order. */
  function byMonth(list) {
    const months = new Map();
    for (const r of list) {
      const month = r.report_date?.slice(0, 7) || '';
      if (!months.has(month)) months.set(month, []);
      months.get(month).push(r);
    }
    return [...months].sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : b.localeCompare(a)));
  }
  function runLedger(list, tab, fresh) {
    const paneId = 'runPane-' + tab, selectedId = selectedRun[tab];
    const months = byMonth(list).map(([month, monthRuns]) => {
      const [node, badgeClass, badge, note] = monthState(month, runs.filter(r => (r.report_date?.slice(0, 7) || '') === month), tab);
      const title = month ? `${MONTHS[Number(month.slice(5, 7)) - 1][0]}${MONTHS[Number(month.slice(5, 7)) - 1].slice(1).toLowerCase()}<small>${month.slice(0, 4)}</small>` : '—<small>NO DATE</small>';
      return `<section class="run-month is-${node}" aria-label="${month ? esc(monthText(month)) : 'No report date'}"><div class="run-gutter is-${node}"><span class="run-node" aria-hidden="true"></span><h3>${title}</h3>`
        + `<span class="run-badge ${badgeClass}">${esc(badge)}</span><p>${note}</p></div>`
        + `<div class="run-rows">${monthRuns.map(r => runRow(r, selectedId, paneId, fresh.has(r.id))).join('')}</div></section>`;
    }).join('');
    const selected = list.find(r => r.id === selectedId);
    return `<div class="run-ledger"><div class="run-months">${months}</div>${selected ? runPane(selected, paneId) : ''}</div>`;
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
  /** Log drawer steps, read from the pipeline's own step markers. A failure is attached to the step it stopped at. */
  function logSteps(raw, run) {
    const steps = [...raw.matchAll(/^=== Step (\d+) of (\d+): (.*?) ===$/gm)];
    const failed = run?.status === 'bad', items = steps.map((m, i) => {
      const last = i === steps.length - 1;
      const state = !last || run?.status === 'ok' ? 'done' : failed ? 'bad' : run?.status === 'running' ? 'current' : 'skipped';
      return `<li class="is-${state}"><span class="run-node" aria-hidden="true"></span><div><b>Step ${m[1]} of ${m[2]}</b><p>${esc(sentence(m[3]))}</p>${last && failed && run.error ? `<p>${esc(run.error)}</p>` : ''}</div></li>`;
    });
    if (!steps.length && run?.error) items.push(`<li class="is-bad"><span class="run-node" aria-hidden="true"></span><div><b>Error</b><p>${esc(run.error)}</p></div></li>`);
    return items.join('') || '<li class="run-log-empty">No pipeline steps have run yet.</li>';
  }
  async function openLog(button) {
    const id = button.dataset.log;
    try {
      const response = await fetch(apiUrl('runs/' + id + '/log'), {cache:'no-store'});
      if (!response.ok) throw new Error('Could not load the run log');
      const raw = await response.text();
      const run = runs.find(r => r.id === id);
      $('#runLogRun').textContent = id + (run ? ' · ' + run.label : '');
      $('#runLogSummary').html = logSteps(raw, run);
      $('#runLog').textContent = raw || 'No technical details yet.';
      if (!$('#runLogPanel').open) $('#runLogPanel').showModal();
    } catch (e) {error(e.message);}
  }
  $('#closeRunLog').onclick = () => $('#runLogPanel').close();
  $('#runLogPanel').addEventListener('click', e => {if (e.target === e.currentTarget) e.currentTarget.close();});

  // Polling redraws only when a run or the selection changed, so focus and scroll survive.
  let drawnKey = '', drawnIds = null;
  const selectedRun = {runs: null, archive: null};
  const FOCUS_KEYS = ['select', 'open', 'plan', 'settle', 'reopen', 'log', 'archive', 'delete', 'cancel'];
  /** Selector that finds the focused run control again after a redraw, or null. */
  function focusTarget(el) {
    const list = el?.closest?.('#runsT, #archiveT');
    if (!list) return null;
    const name = FOCUS_KEYS.find(k => el.dataset[k] != null);
    return name ? `#${list.id} [data-${name}="${CSS.escape(el.dataset[name])}"]` : null;
  }
  /** Keep each tab's selection. A run that just appeared is selected so its progress is in view;
   *  otherwise start on a run in flight, then one waiting for its action plan or settlement. */
  function keepSelection(tab, list, fresh) {
    const arrived = list.find(r => fresh.has(r.id));
    if (arrived) {selectedRun[tab] = arrived.id; return;}
    if (list.some(r => r.id === selectedRun[tab])) return;
    const pending = list.find(r => ['running', 'queued'].includes(r.status))
      || list.find(r => r.status === 'ok' && r.plan_state !== 'settled' && !r.out_of_date && !r.superseded_by);
    selectedRun[tab] = (pending || list[0])?.id ?? null;
  }
  function drawRuns() {
    const active = runs.filter(r => !r.archived), archived = runs.filter(r => r.archived);
    const fresh = new Set(drawnIds ? runs.filter(r => !drawnIds.has(r.id)).map(r => r.id) : []);
    keepSelection('runs', active, fresh);
    keepSelection('archive', archived, fresh);
    const key = JSON.stringify([online, draft?.id ?? null, runs, selectedRun]);
    if (key === drawnKey) return;
    drawnKey = key;
    drawnIds = new Set(runs.map(r => r.id));
    const refocus = focusTarget(document.activeElement);
    const empty = (title, text, action = '') => `<div class="runs-empty"><span class="runs-empty-stub" aria-hidden="true"><b>DD</b><b>MMM</b><small>YYYY</small></span><div><b>${title}</b><p>${text}</p>${action}</div></div>`;
    $('#runsT').html = active.length ? runLedger(active, 'runs', fresh) : online
      ? empty('No runs yet', 'Set a report date, upload three source files and choose Start run.', `<button type="button" class="btn run-btn" data-goto-setup>Go to Setup${ICON.open}</button>`)
      : empty('Runs live in the app', 'Open the live app to see each run and move it through the month.');
    $('#archiveT').html = archived.length ? runLedger(archived, 'archive', fresh)
      : empty('Nothing archived', 'Archive a run from its panel to set it aside. It keeps its results.');
    if (refocus) $(refocus)?.focus({preventScroll: true});
    $('#runsN').textContent = active.length || ''; $('#archN').textContent = archived.length || '';
    const running = runs.filter(r => ['queued', 'running'].includes(r.status)).length;
    $('#runCount').textContent = running ? running + ' running' : '';
    const newest = runs.find(r => r.status !== 'draft');
    document.title = running ? 'Running · FSNE' : newest?.status === 'ok' ? 'Done · FSNE' : 'FSNE';
    $$('[data-select]').forEach(b => b.onclick = () => {
      const tab = b.closest('#archiveT') ? 'archive' : 'runs', id = b.dataset.select;
      selectedRun[tab] = id;
      drawRuns();
      $(`#${tab === 'runs' ? 'runsT' : 'archiveT'} [data-select="${CSS.escape(id)}"]`)?.focus({preventScroll: true});
    });
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
    $$('[data-log]').forEach(b => b.onclick = () => openLog(b));
    $$('[data-goto-setup], [data-resume]').forEach(b => b.onclick = () => $('#obTabs [data-t="setup"]').click());
  }
  // Up and down arrows move the selection through a ledger's runs.
  for (const list of ['#runsT', '#archiveT']) {
    $(list).addEventListener('keydown', e => {
      const row = e.target.closest('.run-row'), step = {ArrowDown: 1, ArrowUp: -1}[e.key];
      if (!row || !step) return;
      e.preventDefault();
      const rows = $$(list + ' .run-row');
      rows[Math.max(0, Math.min(rows.length - 1, rows.indexOf(row) + step))].click();
    });
  }
  // Running runs count their elapsed time between polls.
  setInterval(() => $$('[data-since]').forEach(el => {el.textContent = elapsed(el.dataset.since);}), 1000);
  const ledes = {setup: $('#v-onboarding>.head .lede').textContent, runs: 'Each report month, its runs, and whether it feeds history.', archive: 'Archived runs keep their results. Only settled months feed later history.'};
  $$('#obTabs .tab').forEach(tab => tab.addEventListener('click', () => {$('#v-onboarding>.head .lede').textContent = ledes[tab.dataset.t];}));
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
    const previous = runs.find(r => r.status === 'ok' && r.report_date?.slice(0, 7) === $('#sDate').value);
    if (previous && !confirm('A completed run already exists for this FSNE month (' + previous.id + '). Start another run? History changes only when you settle it.')) return;
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
      $('#appNotice').textContent = loadedRun ? 'Upload your files to calculate a new run.' : HAS_REPORT ? 'Reports currently show the bundled sample. Upload your files to calculate a new run.' : 'No report yet. Upload three source files; secondary sales detects the next FSNE month.';
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
