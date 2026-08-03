// "Help me choose" wizard (#167). Collects intent + details in a native
// <dialog>, then navigates back to /decks with wz_* params; the server
// preseeds the matching existing form. No submission paths of its own.
(function () {
  const dialog = document.getElementById('timed-wizard');
  const opener = document.querySelector('[data-open-timed-wizard]');
  if (!dialog || !opener || typeof dialog.showModal !== 'function') return;

  const steps = dialog.querySelectorAll('[data-wizard-step]');
  const show = (name) => {
    steps.forEach((el) => { el.hidden = el.dataset.wizardStep !== name; });
    const focusable = dialog.querySelector(
      `[data-wizard-step="${name}"] input, [data-wizard-step="${name}"] select, ` +
      `[data-wizard-step="${name}"] a, [data-wizard-step="${name}"] button`
    );
    if (focusable) focusable.focus();
  };

  opener.addEventListener('click', () => {
    show('intent');
    dialog.showModal();
  });
  dialog.querySelector('[data-wizard-close]').addEventListener('click', () => dialog.close());

  const nextBtn = dialog.querySelector('[data-wizard-next]');
  const intents = dialog.querySelectorAll('input[name="wz-intent"]');
  intents.forEach((r) => r.addEventListener('change', () => { nextBtn.disabled = false; }));
  nextBtn.addEventListener('click', () => {
    const picked = dialog.querySelector('input[name="wz-intent"]:checked');
    if (picked) show(picked.value);
  });
  dialog.querySelectorAll('[data-wizard-back]').forEach((b) =>
    b.addEventListener('click', () => show('intent'))
  );

  const go = (params, anchor) => {
    window.location.assign('/decks?' + params.toString() + anchor);
  };
  dialog.querySelectorAll('[data-wizard-finish]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const step = btn.closest('[data-wizard-step]');
      const kind = btn.dataset.wizardFinish;
      const params = new URLSearchParams();
      if (kind === 'daily' || kind === 'interval') {
        const page = step.querySelector('[data-wizard-page]');
        if (!page || !page.value) return;
        params.set('prefill_page', page.value);
        params.set('wz_type', kind);
        if (kind === 'daily') {
          params.set('wz_time', step.querySelector('[data-wizard-time]').value || '07:00');
        } else {
          params.set('wz_interval', step.querySelector('[data-wizard-minutes]').value || '15');
        }
        go(params, '#timed');
      } else if (kind === 'cycle') {
        const picked = Array.from(
          step.querySelectorAll('[data-wizard-cycle-page]:checked')
        ).map((c) => c.value);
        if (!picked.length) return;
        params.set('wz_pages', picked.join(','));
        params.set('wz_dwell', step.querySelector('[data-wizard-dwell]').value || '15');
        go(params, '#rotation-form-card');
      }
    });
  });
})();
