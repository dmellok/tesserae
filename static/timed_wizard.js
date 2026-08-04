// Display setup wizard (#167, redesigned): behaviour → details/pages →
// review → created. Timed modes create through the existing schedule /
// rotation endpoints via fetch (respond=json) so the dialog can stay on its
// created screen; deck mode creates the deck and hands off to the editor.
(function () {
  const dialog = document.getElementById('timed-wizard');
  const opener = document.querySelector('[data-open-timed-wizard]');
  if (!dialog || !opener || typeof dialog.showModal !== 'function') return;

  const $ = (sel) => dialog.querySelector(sel);
  const $$ = (sel) => Array.from(dialog.querySelectorAll(sel));

  let DASHBOARDS = [];
  try {
    DASHBOARDS = JSON.parse($('[data-wizard-dashboards]').textContent);
  } catch (err) {
    DASHBOARDS = [];
  }
  const nameOf = (id) => {
    const d = DASHBOARDS.find((x) => x.id === id);
    return d ? d.name : '';
  };

  const URLS = {
    schedule: dialog.dataset.createScheduleUrl,
    rotation: dialog.dataset.createRotationUrl,
    deck: dialog.dataset.createDeckUrl,
    editorTemplate: dialog.dataset.editorUrlTemplate,
    fullForm: dialog.dataset.fullFormUrl,
  };

  const initial = () => ({
    step: 0, // 0 behaviour · 1 details · 2 review · 3 created
    mode: null, // 'daily' | 'interval' | 'cycle' | 'deck'
    dash: DASHBOARDS.length ? DASHBOARDS[0].id : '',
    time: '07:00',
    interval: 15,
    picks: [], // ordered dashboard ids
    mins: {}, // { id: minutes }, default 5 on add, remembered on re-add
    query: '',
    name: '',
    pending: false,
    handed: false,
    error: '',
  });
  let state = initial();
  // Where "Done" / closing should land after a create, so the list behind
  // the dialog picks up the new card.
  let createdUrl = null;
  let editorUrl = null;

  const el = {
    progress: $('[data-wizard-progress]'),
    stepItems: $$('[data-step-item]'),
    views: $$('[data-wizard-view]'),
    choices: $$('[data-wizard-mode]'),
    configTitle: $('[data-wizard-config-title]'),
    configSub: $('[data-wizard-config-sub]'),
    blocks: $$('[data-wizard-block]'),
    dash: $('[data-wizard-dash]'),
    time: $('[data-wizard-time]'),
    interval: $('[data-wizard-interval]'),
    intervalHint: $('[data-wizard-interval-hint]'),
    presets: $$('[data-interval-preset]'),
    query: $('[data-wizard-query]'),
    results: $('[data-wizard-results]'),
    pickCount: $('[data-wizard-pick-count]'),
    multiHint: $('[data-wizard-multi-hint]'),
    durationRows: $('[data-wizard-duration-rows]'),
    durationsEmpty: $('[data-wizard-durations-empty]'),
    cycleHint: $('[data-wizard-cycle-hint]'),
    reviewSub: $('[data-wizard-review-sub]'),
    name: $('[data-wizard-name]'),
    summary: $('[data-wizard-summary]'),
    plain: $('[data-wizard-plain]'),
    escape: $('[data-wizard-escape]'),
    advanced: $('[data-wizard-advanced]'),
    doneTitle: $('[data-wizard-done-title]'),
    doneBody: $('[data-wizard-done-body]'),
    back: $('[data-wizard-back]'),
    again: $('[data-wizard-again]'),
    forward: $('[data-wizard-forward]'),
    forwardLabel: $('[data-wizard-forward] span'),
    error: $('[data-wizard-error]'),
  };

  // ----- derived values -------------------------------------------------
  const prettyTime = () => {
    const [h, m] = (state.time || '07:00').split(':').map(Number);
    const ap = h >= 12 ? 'PM' : 'AM';
    const hh = h % 12 === 0 ? 12 : h % 12;
    return hh + ':' + String(m).padStart(2, '0') + ' ' + ap;
  };

  const loopMinutes = () => state.picks.reduce((a, id) => a + Number(state.mins[id] || 5), 0);

  const autoName = () => {
    if (state.mode === 'daily') return nameOf(state.dash) + ' at ' + prettyTime();
    if (state.mode === 'interval') return nameOf(state.dash) + ' every ' + state.interval + ' min';
    if (state.mode === 'cycle') return state.picks.length + '-dashboard rotation';
    return state.picks.length + '-page deck';
  };

  const plain = () => {
    if (state.mode === 'daily') {
      return 'Every day at ' + prettyTime() + ', your display renders ' + nameOf(state.dash) +
        ' once and holds it until the next send.';
    }
    if (state.mode === 'interval') {
      return 'Your display re-renders ' + nameOf(state.dash) + ' every ' + state.interval +
        ' minutes, all day.';
    }
    if (state.mode === 'cycle') {
      return 'Your display steps through ' + state.picks.length +
        ' dashboards in order, then starts over; a full loop takes about ' +
        loopMinutes() + ' minutes.';
    }
    return 'Your display holds ' + state.picks.length + ' pages, pre-rendered on the device. ' +
      'A button press, tap or swipe moves between them instantly.';
  };

  const valid = () => {
    if (state.pending) return false;
    if (state.step === 0) return Boolean(state.mode);
    if (state.step === 1) {
      if (state.mode === 'daily') return Boolean(state.dash) && Boolean(state.time);
      if (state.mode === 'interval') return Boolean(state.dash) && Number(state.interval) >= 1;
      return state.picks.length >= 2;
    }
    return true;
  };

  const clampMinutes = (value) => Math.min(1440, Math.max(1, Number(value) || 1));

  // ----- step 2 dynamic lists --------------------------------------------
  const buildResults = () => {
    const q = state.query.trim().toLowerCase();
    const matches = DASHBOARDS.filter((d) => !q || d.name.toLowerCase().includes(q));
    el.results.textContent = '';
    if (!DASHBOARDS.length) {
      const empty = document.createElement('p');
      empty.className = 'wizard-list-empty';
      empty.textContent = 'No dashboards yet. Create some first, then come back.';
      el.results.append(empty);
      return;
    }
    if (!matches.length) {
      const empty = document.createElement('p');
      empty.className = 'wizard-list-empty';
      empty.textContent = 'No dashboards match “' + state.query.trim() + '”.';
      el.results.append(empty);
      return;
    }
    matches.forEach((d) => {
      const on = state.picks.includes(d.id);
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'wizard-page-row' + (on ? ' is-picked' : '');
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', on ? 'true' : 'false');
      const box = document.createElement('span');
      box.className = 'wizard-checkbox';
      box.setAttribute('aria-hidden', 'true');
      if (on) {
        const check = document.createElement('i');
        check.className = 'ph-bold ph-check';
        box.append(check);
      }
      const label = document.createElement('span');
      label.className = 'wizard-page-name';
      label.textContent = d.name;
      row.append(box, label);
      if (on) {
        const order = document.createElement('span');
        order.className = 'wizard-order-badge';
        order.textContent = '#' + (state.picks.indexOf(d.id) + 1);
        row.append(order);
      }
      row.addEventListener('click', () => togglePick(d.id));
      el.results.append(row);
    });
  };

  const buildDurationRows = () => {
    el.durationRows.textContent = '';
    state.picks.forEach((id, i) => {
      const row = document.createElement('div');
      row.className = 'wizard-duration-row';
      const chip = document.createElement('span');
      chip.className = 'wizard-position-chip';
      chip.textContent = String(i + 1);
      const label = document.createElement('span');
      label.className = 'wizard-duration-name';
      label.textContent = nameOf(id);
      const input = document.createElement('input');
      input.type = 'number';
      input.min = '1';
      input.max = '1440';
      input.value = String(state.mins[id] || 5);
      input.setAttribute('aria-label', 'Minutes for ' + nameOf(id));
      input.addEventListener('change', () => {
        state.mins[id] = clampMinutes(input.value);
        input.value = String(state.mins[id]);
        updateHints();
      });
      input.addEventListener('input', () => {
        const v = Number(input.value);
        if (Number.isFinite(v) && v >= 1) state.mins[id] = Math.min(1440, v);
        updateHints();
      });
      const unit = document.createElement('span');
      unit.className = 'wizard-duration-unit';
      unit.textContent = 'min';
      row.append(chip, label, input, unit);
      el.durationRows.append(row);
    });
    el.durationRows.hidden = state.picks.length === 0;
    el.durationsEmpty.hidden = state.picks.length !== 0;
  };

  const togglePick = (id) => {
    const on = state.picks.includes(id);
    if (on) {
      state.picks = state.picks.filter((x) => x !== id);
    } else {
      state.picks = state.picks.concat([id]);
      // Minutes are remembered if the dashboard is re-added this session.
      if (state.mins[id] == null) state.mins[id] = 5;
    }
    buildResults();
    buildDurationRows();
    updateHints();
    el.forward.disabled = !valid();
  };

  const updateHints = () => {
    el.intervalHint.textContent =
      'Renders about ' + Math.max(1, Math.round(1440 / Number(state.interval || 15))) +
      ' times a day.';
    el.presets.forEach((p) => {
      p.classList.toggle('is-selected', Number(state.interval) === Number(p.dataset.intervalPreset));
    });
    el.pickCount.textContent =
      state.picks.length === 0 ? 'None picked yet' : state.picks.length + ' picked';
    el.multiHint.textContent = state.mode === 'cycle'
      ? 'Pick two or more. Numbers show the order they will appear in.'
      : 'Pick two or more. Order sets the page numbers in the editor.';
    el.cycleHint.textContent = state.picks.length > 0
      ? 'A full loop takes about ' + loopMinutes() + ' minutes.'
      : 'Each dashboard can stay up for a different length of time.';
  };

  // ----- review ----------------------------------------------------------
  const buildSummary = () => {
    const rows = [];
    if (state.mode === 'daily') {
      rows.push(['Behaviour', 'One dashboard, once a day']);
      rows.push(['Dashboard', nameOf(state.dash)]);
      rows.push(['When', 'Every day at ' + prettyTime()]);
    } else if (state.mode === 'interval') {
      rows.push(['Behaviour', 'One dashboard, kept fresh']);
      rows.push(['Dashboard', nameOf(state.dash)]);
      rows.push(['When', 'Every ' + state.interval + ' minutes']);
    } else if (state.mode === 'cycle') {
      rows.push(['Behaviour', 'Timed rotation']);
      rows.push(['Order', state.picks
        .map((id, i) => (i + 1) + '. ' + nameOf(id) + ' · ' + (state.mins[id] || 5) + ' min')
        .join('\n')]);
    } else {
      rows.push(['Behaviour', 'Manual deck']);
      rows.push(['Pages', state.picks.map((id, i) => (i + 1) + '. ' + nameOf(id)).join('\n')]);
      rows.push(['Next', 'Wire the “go to page” actions in the deck editor']);
    }
    el.summary.textContent = '';
    rows.forEach(([label, value]) => {
      const row = document.createElement('div');
      row.className = 'wizard-summary-row';
      const dt = document.createElement('span');
      dt.className = 'wizard-summary-label';
      dt.textContent = label;
      const dd = document.createElement('span');
      dd.className = 'wizard-summary-value';
      dd.textContent = value;
      row.append(dt, dd);
      el.summary.append(row);
    });
    el.plain.textContent = plain();

    el.reviewSub.textContent = state.mode === 'deck'
      ? 'This creates the deck, then hands you to the editor to wire it up.'
      : 'One last look, then it goes live on the display.';
    el.name.placeholder = autoName();

    // Escape hatch: the prefilled full form exists for timed sends only;
    // cycles and decks are fine-tuned in the deck editor after Create.
    const hasFullForm = state.mode === 'daily' || state.mode === 'interval';
    el.escape.hidden = !hasFullForm;
    if (hasFullForm) {
      const params = new URLSearchParams();
      if (state.name.trim()) params.set('wz_name', state.name.trim());
      params.set('prefill_page', state.dash);
      params.set('wz_type', state.mode);
      if (state.mode === 'daily') params.set('wz_time', state.time);
      else params.set('wz_interval', String(state.interval));
      el.advanced.href = URLS.fullForm + '?' + params.toString() + '#timed';
    }
  };

  // ----- render ------------------------------------------------------------
  const STEP_LABELS = () => ['Behaviour', state.mode === 'deck' ? 'Pages' : 'Details', 'Review'];

  const primaryLabel = () => {
    if (state.pending) return 'Creating…';
    if (state.step === 2) return state.mode === 'deck' ? 'Create and open editor' : 'Create schedule';
    if (state.step === 3) {
      if (state.mode === 'deck') return state.handed ? 'Opening…' : 'Open the deck editor';
      return 'Done';
    }
    return 'Continue';
  };

  const render = (focusHeading) => {
    const labels = STEP_LABELS();
    el.progress.textContent = state.step === 3
      ? 'Created'
      : 'Step ' + (state.step + 1) + ' of 3 · ' + labels[state.step];

    el.stepItems.forEach((item, i) => {
      item.classList.toggle('is-complete', state.step > i);
      item.classList.toggle('is-current', state.step === i);
      item.querySelector('[data-step-label]').textContent = labels[i];
      if (state.step === i) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    });

    const viewName = ['behaviour', 'details', 'review', 'created'][state.step];
    el.views.forEach((v) => { v.hidden = v.dataset.wizardView !== viewName; });

    if (viewName === 'behaviour') {
      el.choices.forEach((c, i) => {
        const on = c.dataset.wizardMode === state.mode;
        c.classList.toggle('is-selected', on);
        c.setAttribute('aria-checked', on ? 'true' : 'false');
        c.tabIndex = on || (!state.mode && i === 0) ? 0 : -1;
      });
    }

    if (viewName === 'details') {
      const titles = {
        daily: ['Which dashboard, and when?', 'One send a day, at the time you pick.'],
        interval: ['Which dashboard, and how often?',
          'Shorter cadences use more battery on radio-only displays.'],
        cycle: ['Build the rotation',
          'Pick two or more. They show in this order and the loop repeats all day.'],
        deck: ['Choose the pages',
          'Pick the dashboards that belong to this deck. You will link them in the editor.'],
      }[state.mode] || ['', ''];
      el.configTitle.textContent = titles[0];
      el.configSub.textContent = titles[1];
      const show = {
        single: state.mode === 'daily' || state.mode === 'interval',
        time: state.mode === 'daily',
        interval: state.mode === 'interval',
        multi: state.mode === 'cycle' || state.mode === 'deck',
        durations: state.mode === 'cycle',
        deckinfo: state.mode === 'deck',
      };
      el.blocks.forEach((b) => { b.hidden = !show[b.dataset.wizardBlock]; });
      buildResults();
      buildDurationRows();
      updateHints();
    }

    if (viewName === 'review') buildSummary();

    if (viewName === 'created') {
      el.doneTitle.textContent = state.handed
        ? 'Opening the deck editor…'
        : (state.name.trim() || autoName()) + ' is live';
      el.doneBody.textContent = state.handed
        ? 'Your pages are saved. Add a “go to page” action to any tile to link them.'
        : plain();
    }

    el.back.hidden = !(state.step === 1 || state.step === 2);
    el.again.hidden = state.step !== 3;
    el.forwardLabel.textContent = primaryLabel();
    el.forward.disabled = !valid() || state.handed;
    el.error.textContent = state.error;
    el.error.hidden = !state.error;

    if (focusHeading) {
      const heading = dialog.querySelector(
        '[data-wizard-view="' + viewName + '"] .wizard-section-title'
      );
      if (heading) heading.focus({ preventScroll: true });
    }
  };

  // ----- create ------------------------------------------------------------
  const submit = () => {
    const body = new FormData();
    body.set('respond', 'json');
    body.set('name', state.name.trim() || autoName());
    body.set('enabled', 'on');
    let url;
    if (state.mode === 'cycle') {
      url = URLS.rotation;
      body.set('anchor', '00:00');
      state.picks.forEach((id) => {
        body.append('step_page_ids[]', id);
        body.append('step_dwell_minutes[]', String(state.mins[id] || 5));
        body.append('step_conditions_json[]', '');
      });
    } else if (state.mode === 'deck') {
      url = URLS.deck;
      body.set('graph_json', JSON.stringify(state.picks.map((id) => ({ page_id: id }))));
    } else {
      url = URLS.schedule;
      body.set('page_id', state.dash);
      body.set('type', state.mode);
      if (state.mode === 'daily') body.set('fires_at', state.time);
      else body.set('interval_minutes', String(state.interval));
    }

    state.pending = true;
    state.error = '';
    render(false);
    fetch(url, { method: 'POST', body, headers: { Accept: 'application/json' } })
      .then((resp) => resp.json().then((data) => ({ ok: resp.ok, data })))
      .then(({ ok, data }) => {
        state.pending = false;
        if (!ok || !data.ok) {
          state.error = (data && data.error) || 'Something went wrong. Please try again.';
          render(false);
          return;
        }
        createdUrl = data.url || null;
        if (state.mode === 'deck' && data.id) {
          editorUrl = URLS.editorTemplate.replace('__DECK_ID__', encodeURIComponent(data.id));
        }
        state.step = 3;
        render(true);
      })
      .catch(() => {
        state.pending = false;
        state.error = 'Could not reach the server. Please try again.';
        render(false);
      });
  };

  // The dialog's controls keep their DOM values across resets; put them
  // back in step with a fresh state object.
  const syncInputs = () => {
    if (el.dash && DASHBOARDS.length) el.dash.value = state.dash;
    el.time.value = state.time;
    el.interval.value = String(state.interval);
    el.query.value = state.query;
    el.name.value = state.name;
  };

  // ----- events ------------------------------------------------------------
  opener.addEventListener('click', () => {
    state = initial();
    createdUrl = null;
    editorUrl = null;
    syncInputs();
    render(false);
    dialog.showModal();
  });

  $('[data-wizard-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    // Something was created behind the dialog: land on the list with the
    // new card highlighted so the page isn't stale.
    if (createdUrl && !state.handed) window.location.assign(createdUrl);
  });

  el.choices.forEach((c) => {
    c.addEventListener('click', () => {
      state.mode = c.dataset.wizardMode;
      state.error = '';
      render(false);
    });
  });
  // Radiogroup arrow keys move focus and selection between behaviour cards.
  dialog.querySelector('.wizard-choices').addEventListener('keydown', (e) => {
    const keys = ['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft'];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    const current = el.choices.findIndex((c) => c === document.activeElement);
    const delta = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1;
    const next = ((current < 0 ? 0 : current) + delta + el.choices.length) % el.choices.length;
    state.mode = el.choices[next].dataset.wizardMode;
    render(false);
    el.choices[next].focus();
  });

  el.dash.addEventListener('change', () => { state.dash = el.dash.value; });
  el.time.addEventListener('input', () => {
    state.time = el.time.value;
    el.forward.disabled = !valid();
  });
  el.interval.addEventListener('input', () => {
    const v = Number(el.interval.value);
    if (Number.isFinite(v) && v >= 1) state.interval = Math.min(1440, v);
    updateHints();
    el.forward.disabled = !valid();
  });
  el.interval.addEventListener('change', () => {
    state.interval = clampMinutes(el.interval.value);
    el.interval.value = String(state.interval);
    updateHints();
    el.forward.disabled = !valid();
  });
  el.presets.forEach((p) => {
    p.addEventListener('click', () => {
      state.interval = Number(p.dataset.intervalPreset);
      el.interval.value = String(state.interval);
      updateHints();
      el.forward.disabled = !valid();
    });
  });
  el.query.addEventListener('input', () => {
    state.query = el.query.value;
    buildResults();
  });
  el.name.addEventListener('input', () => {
    state.name = el.name.value;
  });

  el.back.addEventListener('click', () => {
    state.step = Math.max(0, state.step - 1);
    state.error = '';
    render(true);
  });

  el.again.addEventListener('click', () => {
    // Keep createdUrl: closing later should still refresh the list behind.
    state = initial();
    editorUrl = null;
    syncInputs();
    render(true);
  });

  el.forward.addEventListener('click', () => {
    if (!valid()) return;
    if (state.step < 2) {
      state.step += 1;
      state.error = '';
      render(true);
      return;
    }
    if (state.step === 2) {
      submit();
      return;
    }
    // Created screen.
    if (state.mode === 'deck' && editorUrl) {
      state.handed = true;
      render(false);
      window.location.assign(editorUrl);
      return;
    }
    dialog.close(); // the close handler navigates to createdUrl
  });
})();
