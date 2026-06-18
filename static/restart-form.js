// Restart-after-submit form handler.
//
// Any ``<form data-restart-form>`` on the page gets intercepted: the
// submit becomes a fetch, the restart modal opens, we wait for the
// server to go down and come back, then auto-reload the page. The
// underlying endpoint is responsible for calling ``Updater.restart()``
// after persisting whatever it needs to persist; this script doesn't
// know or care which endpoint, only that ``/healthz`` will flap.
//
// Form attributes:
//   data-restart-form              opt in
//   data-restart-label="..."       modal title (default: "Updating Tesserae")
//   data-restart-confirm="..."     optional confirm() prompt before submit
//
// The modal markup lives in templates/_restart_modal.html and is
// included from _base.html, so any page that has an opted-in form
// gets the UX automatically.
(function () {
  const STAGES = [
    { at: 0,    text: "Working…" },
    { at: 4000, text: "Almost there…" },
  ];
  const forms = document.querySelectorAll("[data-restart-form]");
  if (!forms.length) return;
  const modal = document.querySelector("[data-restart-modal]");
  const titleEl = document.querySelector("[data-restart-modal-title]");
  const stageEl = document.querySelector("[data-restart-modal-stage]");
  const errorEl = document.querySelector("[data-restart-modal-error]");
  if (!modal || !titleEl || !stageEl || !errorEl) return;

  function showModal(label) {
    titleEl.textContent = label;
    stageEl.textContent = STAGES[0].text;
    errorEl.hidden = true;
    errorEl.textContent = "";
    modal.hidden = false;
    document.body.style.overflow = "hidden";
  }
  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.hidden = false;
    stageEl.textContent = "";
  }
  function setStage(text) {
    stageEl.textContent = text;
  }

  function rotateStages(startedAt) {
    let idx = 0;
    const timer = setInterval(() => {
      const elapsed = Date.now() - startedAt;
      let nextIdx = idx;
      for (let i = idx; i < STAGES.length; i++) {
        if (elapsed >= STAGES[i].at) nextIdx = i;
      }
      if (nextIdx !== idx) {
        idx = nextIdx;
        setStage(STAGES[idx].text);
      }
    }, 750);
    return () => clearInterval(timer);
  }

  function poll(url, opts) {
    const timeoutMs = opts.timeoutMs || 90000;
    const intervalMs = opts.intervalMs || 500;
    const startedAt = Date.now();
    return new Promise((resolve, reject) => {
      function tick() {
        fetch(url, { method: "GET", cache: "no-store", credentials: "same-origin" })
          .then((r) => {
            if (r.ok) return resolve();
            throw new Error("not ok");
          })
          .catch(() => {
            if (Date.now() - startedAt > timeoutMs) {
              reject(new Error("timeout"));
              return;
            }
            setTimeout(tick, intervalMs);
          });
      }
      tick();
    });
  }

  function waitForServerDown(url, startedAt) {
    // Health endpoint returns 200 right up until os.execv fires. We
    // want to observe it FAIL once before we start polling for "back
    // up" - otherwise we'd auto-reload while the old process is still
    // alive (which would just show the old version).
    return new Promise((resolve) => {
      function tick() {
        fetch(url, { method: "GET", cache: "no-store", credentials: "same-origin" })
          .then((r) => {
            if (!r.ok) return resolve();
            if (Date.now() - startedAt > 15000) return resolve();
            setTimeout(tick, 250);
          })
          .catch(() => resolve());
      }
      tick();
    });
  }

  forms.forEach((form) => {
    form.addEventListener("submit", (ev) => {
      const confirmText = form.dataset.restartConfirm;
      if (confirmText && !confirm(confirmText)) {
        ev.preventDefault();
        return;
      }
      ev.preventDefault();
      const label = form.dataset.restartLabel || "Updating Tesserae";
      const startedAt = Date.now();
      showModal(label);
      const stopRotate = rotateStages(startedAt);

      const fd = new FormData(form);
      // ``fetch`` can reject the POST itself with "Failed to fetch" if
      // the server kills the connection before flushing the response.
      // That happens routinely in --dev (werkzeug reloader races the
      // ``Updater.restart`` timer) and occasionally in production when
      // delay_s is short. ``/healthz`` is the source of truth for what
      // the server is actually doing, so we squash POST-level errors
      // into a resolved chain and let the down-then-up poll decide
      // whether the server is restarting or genuinely broken.
      const submit = fetch(form.action, {
        method: "POST",
        body: fd,
        credentials: "same-origin",
        redirect: "follow",
      }).catch(() => null);
      submit
        .then(() => {
          stopRotate();
          setStage("Restarting…");
          return waitForServerDown("/healthz", Date.now());
        })
        .then(() => {
          setStage("Server is restarting. Waiting for it to come back…");
          return poll("/healthz", { timeoutMs: 120000, intervalMs: 500 });
        })
        .then(() => {
          setStage("Up. Reloading…");
          setTimeout(() => window.location.reload(), 400);
        })
        .catch((err) => {
          // Only reachable if poll() timed out waiting for /healthz to
          // come back, i.e. the server really didn't recover. The POST
          // failure path is squashed above so it can't trigger this.
          stopRotate();
          showError(
            "Couldn't confirm the server came back. " +
              "It may have restarted anyway, reload the page manually to check. " +
              "(" + (err && err.message ? err.message : "timed out") + ")"
          );
        });
    });
  });
})();
