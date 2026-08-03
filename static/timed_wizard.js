// "Help me choose" wizard (#167). One question per screen; ends by
// navigating to /decks with wz_* params so the server preseeds the matching
// existing form. Owns no submission paths.
(function () {
  const dialog = document.getElementById('timed-wizard');
  const opener = document.querySelector('[data-open-timed-wizard]');
  if (!dialog || !opener || typeof dialog.showModal !== 'function') return;

  const FLOWS = {
    daily: ['intent', 'page', 'time', 'name'],
    interval: ['intent', 'page', 'minutes', 'name'],
    cycle: ['intent', 'pages', 'dwell', 'name'],
    manual: ['intent', 'manual'],
  };
  let flow = ['intent'];
  let index = 0;

  const steps = dialog.querySelectorAll('[data-wizard-step]');
  const footer = dialog.querySelector('[data-wizard-footer]');
  const backBtn = dialog.querySelector('[data-wizard-back]');
  const forwardBtn = dialog.querySelector('[data-wizard-forward]');
  const editorLink = dialog.querySelector('[data-wizard-editor-link]');
  const progress = dialog.querySelector('[data-wizard-progress]');
  const intentNext = dialog.querySelector('[data-wizard-next]');

  const stepValid = (name) => {
    if (name === 'page') {
      const sel = dialog.querySelector('[data-wizard-page]');
      return Boolean(sel && sel.value);
    }
    if (name === 'pages') {
      return dialog.querySelectorAll('[data-wizard-cycle-page]:checked').length >= 1;
    }
    return true;
  };

  const render = () => {
    const name = flow[index];
    steps.forEach((el) => { el.hidden = el.dataset.wizardStep !== name; });
    const last = index === flow.length - 1;
    footer.hidden = name === 'intent';
    editorLink.hidden = name !== 'manual';
    forwardBtn.hidden = name === 'manual';
    forwardBtn.querySelector('span').textContent = last ? 'Prefill the form' : 'Continue';
    forwardBtn.disabled = !stepValid(name);
    progress.textContent = 'Step ' + (index + 1) + ' of ' + flow.length;
    const focusable = dialog.querySelector(
      `[data-wizard-step="${name}"] input, [data-wizard-step="${name}"] select`
    );
    (focusable || forwardBtn).focus();
  };

  opener.addEventListener('click', () => {
    flow = ['intent'];
    index = 0;
    render();
    dialog.showModal();
  });
  dialog.querySelector('[data-wizard-close]').addEventListener('click', () => dialog.close());

  dialog.querySelectorAll('input[name="wz-intent"]').forEach((r) =>
    r.addEventListener('change', () => {
      intentNext.disabled = false;
      flow = FLOWS[r.value];
    })
  );
  intentNext.addEventListener('click', () => { index = 1; render(); });
  backBtn.addEventListener('click', () => { index = Math.max(0, index - 1); render(); });

  // Re-validate the current step as its inputs change.
  dialog.addEventListener('change', () => { forwardBtn.disabled = !stepValid(flow[index]); });

  const finish = () => {
    const kind = flow === FLOWS.daily ? 'daily'
      : flow === FLOWS.interval ? 'interval' : 'cycle';
    const params = new URLSearchParams();
    const name = dialog.querySelector('[data-wizard-name]').value.trim();
    if (name) params.set('wz_name', name);
    if (kind === 'cycle') {
      const picked = Array.from(
        dialog.querySelectorAll('[data-wizard-cycle-page]:checked')
      ).map((c) => c.value);
      params.set('wz_pages', picked.join(','));
      params.set('wz_dwell', dialog.querySelector('[data-wizard-dwell]').value || '15');
      window.location.assign('/decks?' + params.toString() + '#rotation-form-card');
      return;
    }
    params.set('prefill_page', dialog.querySelector('[data-wizard-page]').value);
    params.set('wz_type', kind);
    if (kind === 'daily') {
      params.set('wz_time', dialog.querySelector('[data-wizard-time]').value || '07:00');
    } else {
      params.set('wz_interval', dialog.querySelector('[data-wizard-minutes]').value || '15');
    }
    window.location.assign('/decks?' + params.toString() + '#timed');
  };

  forwardBtn.addEventListener('click', () => {
    if (!stepValid(flow[index])) return;
    if (index === flow.length - 1) {
      finish();
      return;
    }
    index += 1;
    render();
  });
})();
