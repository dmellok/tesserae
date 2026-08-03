// "Help me choose" wizard (#167). One question per screen, ending in a
// review step whose Create button submits directly through the existing
// create endpoints (a hidden form mirroring the real forms' field names);
// Advanced options deep-links to the prefilled full form instead.
(function () {
  const dialog = document.getElementById('timed-wizard');
  const opener = document.querySelector('[data-open-timed-wizard]');
  if (!dialog || !opener || typeof dialog.showModal !== 'function') return;

  const FLOWS = {
    daily: ['intent', 'page', 'time', 'name', 'review'],
    interval: ['intent', 'page', 'minutes', 'name', 'review'],
    cycle: ['intent', 'pages', 'dwell', 'name', 'review'],
    manual: ['intent', 'manual'],
  };
  let kind = null;
  let flow = ['intent'];
  let index = 0;

  const steps = dialog.querySelectorAll('[data-wizard-step]');
  const footer = dialog.querySelector('[data-wizard-footer]');
  const forwardBtn = dialog.querySelector('[data-wizard-forward]');
  const editorLink = dialog.querySelector('[data-wizard-editor-link]');
  const progress = dialog.querySelector('[data-wizard-progress]');
  const intentNext = dialog.querySelector('[data-wizard-next]');
  const dwellRows = dialog.querySelector('[data-wizard-dwell-rows]');
  const review = dialog.querySelector('[data-wizard-review]');
  const advanced = dialog.querySelector('[data-wizard-advanced]');

  const pickedPages = () =>
    Array.from(dialog.querySelectorAll('[data-wizard-cycle-page]:checked')).map((c) => ({
      id: c.value,
      label: c.parentElement.textContent.trim(),
    }));
  const dwellFor = (id) => {
    const input = dwellRows.querySelector(`input[data-page-id="${CSS.escape(id)}"]`);
    const v = parseInt(input && input.value, 10);
    return Number.isFinite(v) && v >= 1 ? Math.min(v, 1440) : 15;
  };
  const state = () => ({
    page: (dialog.querySelector('[data-wizard-page]') || {}).value || '',
    pageLabel: (() => {
      const sel = dialog.querySelector('[data-wizard-page]');
      return sel && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].text : '';
    })(),
    time: (dialog.querySelector('[data-wizard-time]') || {}).value || '07:00',
    minutes: (dialog.querySelector('[data-wizard-minutes]') || {}).value || '15',
    name: (dialog.querySelector('[data-wizard-name]') || {}).value.trim(),
    pages: pickedPages().map((p) => ({ ...p, dwell: dwellFor(p.id) })),
  });

  const stepValid = (name) => {
    if (name === 'page') return Boolean(state().page);
    if (name === 'pages') return pickedPages().length >= 1;
    if (name === 'name') return Boolean(state().name);
    return true;
  };

  const buildDwellRows = () => {
    const existing = {};
    dwellRows.querySelectorAll('input[data-page-id]').forEach((i) => {
      existing[i.dataset.pageId] = i.value;
    });
    dwellRows.textContent = '';
    pickedPages().forEach((p) => {
      const row = document.createElement('label');
      row.className = 'wizard-dwell-row';
      const span = document.createElement('span');
      span.textContent = p.label;
      const input = document.createElement('input');
      input.type = 'number';
      input.min = '1';
      input.max = '1440';
      input.value = existing[p.id] || '15';
      input.dataset.pageId = p.id;
      input.setAttribute('aria-label', `Minutes for ${p.label}`);
      const unit = document.createElement('span');
      unit.className = 'wizard-dwell-unit';
      unit.textContent = 'min';
      row.append(span, input, unit);
      dwellRows.append(row);
    });
  };

  const buildReview = () => {
    const s = state();
    const rows = [['Name', s.name || '(unnamed)']];
    if (kind === 'daily') {
      rows.push(['Creates', 'A timed send'], ['Dashboard', s.pageLabel], ['Fires', `daily at ${s.time}`]);
    } else if (kind === 'interval') {
      rows.push(['Creates', 'A timed send'], ['Dashboard', s.pageLabel], ['Fires', `every ${s.minutes} minutes`]);
    } else {
      rows.push(['Creates', 'A timer cycle'], ['Cycle', s.pages.map((p) => `${p.label} (${p.dwell} min)`).join(' → ')]);
    }
    review.textContent = '';
    rows.forEach(([dt, dd]) => {
      const t = document.createElement('dt');
      t.textContent = dt;
      const d = document.createElement('dd');
      d.textContent = dd;
      review.append(t, d);
    });
    // Advanced options: the prefilled full form for timed sends. Cycles
    // have no separate form anymore; fine-tuning happens in the deck
    // editor after Create, so the link hides on that path.
    advanced.hidden = kind === 'cycle';
    if (kind !== 'cycle') {
      const params = new URLSearchParams();
      if (s.name) params.set('wz_name', s.name);
      params.set('prefill_page', s.page);
      params.set('wz_type', kind);
      if (kind === 'daily') params.set('wz_time', s.time);
      else params.set('wz_interval', s.minutes);
      advanced.href = '/decks?' + params.toString() + '#timed';
    }
  };

  const render = () => {
    const name = flow[index];
    if (name === 'dwell') buildDwellRows();
    if (name === 'review') buildReview();
    steps.forEach((el) => { el.hidden = el.dataset.wizardStep !== name; });
    footer.hidden = name === 'intent';
    editorLink.hidden = name !== 'manual';
    forwardBtn.hidden = name === 'manual';
    forwardBtn.querySelector('span').textContent = name === 'review' ? 'Create' : 'Continue';
    forwardBtn.disabled = !stepValid(name);
    progress.textContent = 'Step ' + (index + 1) + ' of ' + flow.length;
    const focusable = dialog.querySelector(
      `[data-wizard-step="${name}"] input, [data-wizard-step="${name}"] select`
    );
    (focusable || forwardBtn).focus();
  };

  opener.addEventListener('click', () => {
    kind = null;
    flow = ['intent'];
    index = 0;
    render();
    dialog.showModal();
  });
  dialog.querySelector('[data-wizard-close]').addEventListener('click', () => dialog.close());

  dialog.querySelectorAll('input[name="wz-intent"]').forEach((r) =>
    r.addEventListener('change', () => {
      intentNext.disabled = false;
      kind = r.value;
      flow = FLOWS[kind];
    })
  );
  intentNext.addEventListener('click', () => { index = 1; render(); });
  dialog.querySelector('[data-wizard-back]').addEventListener('click', () => {
    index = Math.max(0, index - 1);
    render();
  });
  dialog.addEventListener('input', () => { forwardBtn.disabled = !stepValid(flow[index]); });

  const submitCreate = () => {
    const s = state();
    const form = document.createElement('form');
    form.method = 'post';
    form.hidden = true;
    const add = (name, value) => {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = value;
      form.append(input);
    };
    add('name', s.name);
    add('enabled', 'on');
    if (kind === 'cycle') {
      form.action = '/rotations/new';
      add('anchor', '00:00');
      s.pages.forEach((p) => {
        add('step_page_ids[]', p.id);
        add('step_dwell_minutes[]', String(p.dwell));
        add('step_conditions_json[]', '');
      });
    } else {
      form.action = '/schedules/new';
      add('page_id', s.page);
      add('type', kind);
      if (kind === 'daily') add('fires_at', s.time);
      else add('interval_minutes', s.minutes);
    }
    document.body.append(form);
    form.submit();
  };

  forwardBtn.addEventListener('click', () => {
    if (!stepValid(flow[index])) return;
    if (flow[index] === 'review') {
      submitCreate();
      return;
    }
    index += 1;
    render();
  });
})();
