// Dashboard editor v2: page shell. Shared helpers for the other step
// scripts (window.EV2): saving through the existing routes, the Saved
// indicator, the steps <-> preview events, and the UI state that survives
// the reload a structural change needs (layout preset, widget swap, display
// change). Plain script, no build step; loads before steps/widgets/catalog/
// look/lineup.js.
(function () {
  "use strict";
  const root = document.getElementById("ev2");
  if (!root) return;

  const cfg = root.dataset;
  const pageId = cfg.pageId;
  const isNew = cfg.new === "1";
  const statusEl = root.querySelector("[data-ev2-status]");

  // ---------------------------------------------------------------- status
  let inFlight = 0;
  let lastError = "";
  function setStatus(state, message) {
    if (!statusEl) return;
    statusEl.dataset.state = state;
    const icon =
      state === "saving"
        ? "ph ph-arrows-clockwise"
        : state === "error"
          ? "ph-fill ph-warning-circle"
          : "ph-bold ph-check";
    const label =
      state === "saving" ? "Saving…" : state === "error" ? message || "Couldn't save" : "Saved";
    statusEl.innerHTML = '<i class="' + icon + '" aria-hidden="true"></i><span></span>';
    statusEl.lastChild.textContent = label;
    statusEl.title = state === "error" ? lastError : "";
  }

  // ---------------------------------------------------------------- saving
  // Every write goes through one chain so saves land in the order they were
  // made (a cell option, then a layout preset that reads it, say).
  let chain = Promise.resolve();

  function toBody(data) {
    if (data instanceof FormData) return data;
    const fd = new FormData();
    Object.entries(data || {}).forEach(([k, v]) => {
      if (Array.isArray(v)) v.forEach((x) => fd.append(k, x));
      else if (v !== undefined && v !== null) fd.append(k, v);
    });
    return fd;
  }

  async function send(url, data, opts) {
    const o = opts || {};
    const headers = { "X-Requested-With": "fetch" };
    let body;
    if (o.json) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(data || {});
    } else {
      body = toBody(data);
    }
    const resp = await fetch(url, {
      method: "POST",
      body,
      headers,
      credentials: "same-origin",
      keepalive: Boolean(o.keepalive),
    });
    const out = await resp.json().catch(() => ({ ok: resp.ok }));
    if (!resp.ok || (out && out.ok === false)) {
      const err = new Error((out && out.message) || "HTTP " + resp.status);
      err.status = resp.status;
      err.body = out;
      throw err;
    }
    return out;
  }

  const jobs = new Set(); // one promise per queued save, settled with its outcome
  const failedForms = new Set(); // forms whose last save failed; they still hold the edit

  // Queue a POST. Resolves with the JSON body; rejects (and shows the error
  // in the Saved indicator) when the server refuses.
  function post(url, data, opts) {
    const o = opts || {};
    inFlight++;
    setStatus("saving");
    const p = chain.then(() => send(url, data, opts));
    chain = p.catch(() => {});
    const tracked = p.then(
      (out) => {
        inFlight--;
        if (!inFlight) setStatus(lastError ? "error" : "saved");
        return out;
      },
      (err) => {
        inFlight--;
        if (o.quiet && o.quiet(err)) {
          if (!inFlight) setStatus(lastError ? "error" : "saved");
          throw err;
        }
        lastError = String((err && err.message) || err);
        setStatus("error", "Couldn't save");
        console.error("[ev2] save failed:", url, err);
        throw err;
      }
    );
    // The real outcome of every queued save, for flush() to wait on.
    jobs.add(tracked);
    const done = () => jobs.delete(tracked);
    tracked.then(done, done);
    return tracked;
  }

  // Debounced autosave per form. ``delay`` 0 saves on the next tick.
  const timers = new Map();
  const formSaved = new Map();
  // Save a form now. A failure keeps the form on the retry list: its fields
  // still hold what the user typed, and the next flush sends them again.
  function postForm(form) {
    const p = post(form.action, new FormData(form));
    p.then(
      () => failedForms.delete(form),
      () => failedForms.add(form)
    );
    return p;
  }
  function saveForm(form, delay) {
    clearTimeout(timers.get(form));
    lastError = "";
    return new Promise((resolve, reject) => {
      timers.set(
        form,
        setTimeout(
          () => {
            timers.delete(form);
            postForm(form).then(
              (out) => {
                formSaved.set(form, Date.now());
                (form.__ev2after || []).forEach((fn) => fn(out));
                emit("ev2:saved", {});
                resolve(out);
              },
              reject
            );
          },
          delay || 0
        )
      );
    });
  }
  function afterSave(form, fn) {
    (form.__ev2after = form.__ev2after || []).push(fn);
  }

  // Send anything still waiting on a debounce (and retry forms whose last
  // save failed), then wait for the outcome of every queued save, and keep
  // going until nothing new was queued while we waited. Rejects when any of
  // them failed, so the caller (Replace, reload, Duplicate, Push, a layout
  // change) stops and the error state stays up.
  async function flush() {
    let failed = null;
    for (let round = 0; round < 50; round++) {
      const forms = new Set(timers.keys());
      if (round === 0) failedForms.forEach((f) => forms.add(f));
      if (forms.size && round === 0 && failedForms.size) lastError = "";
      forms.forEach((form) => {
        clearTimeout(timers.get(form));
        timers.delete(form);
        postForm(form).catch(() => {});
      });
      if (!jobs.size && !timers.size) break;
      const results = await Promise.allSettled([...jobs]);
      results.forEach((r) => {
        if (r.status === "rejected") failed = failed || r.reason;
      });
      if (!jobs.size && !timers.size) break;
    }
    if (failed) throw failed;
  }
  function pending() {
    return timers.size > 0 || inFlight > 0;
  }

  // Leaving with a debounce still pending: send it with keepalive so it
  // survives the navigation.
  function flushOnLeave() {
    const forms = new Set([...timers.keys(), ...failedForms]);
    timers.forEach((t) => clearTimeout(t));
    forms.forEach((form) => {
      send(form.action, new FormData(form), { keepalive: true }).catch(() => {});
    });
    timers.clear();
    failedForms.clear();
  }
  window.addEventListener("pagehide", flushOnLeave);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushOnLeave();
  });

  // ---------------------------------------------------------------- events
  function emit(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
  }
  function on(name, fn) {
    document.addEventListener(name, (ev) => fn(ev.detail || {}, ev));
  }

  // ------------------------------------------------- state across reloads
  const UI_KEY = "ev2-ui:" + pageId;
  let restored = null;
  try {
    restored = JSON.parse(sessionStorage.getItem(UI_KEY) || "null");
    sessionStorage.removeItem(UI_KEY);
  } catch (e) {
    restored = null;
  }
  const uiState = { open: undefined, cell: null };
  function stash(extra) {
    try {
      sessionStorage.setItem(
        UI_KEY,
        JSON.stringify(Object.assign({}, uiState, { y: window.scrollY }, extra || {}))
      );
    } catch (e) {
      /* private mode: the reload just starts fresh */
    }
  }
  // Persist what's open, then reload the page from the server (structural
  // changes re-render the steps and the preview).
  let reloading = false;
  async function reload(extra) {
    if (reloading) return true;
    // A save that failed leaves the edit in its form: stay on the page (the
    // error state is already showing) so the user can retry.
    try {
      await flush();
    } catch (e) {
      return false;
    }
    // Past this point nothing may queue a save the reload would drop.
    reloading = true;
    root.inert = true;
    stash(extra);
    location.reload();
    return true;
  }
  if (restored && typeof restored.y === "number") {
    const y = restored.y;
    window.addEventListener("load", () => window.scrollTo(0, y), { once: true });
  }

  // ---------------------------------------------------------------- helpers
  function debounceDelay(el) {
    if (!el) return 0;
    if (el.tagName === "TEXTAREA") return -1;
    if (el.tagName === "INPUT") {
      const t = (el.type || "text").toLowerCase();
      if (["text", "search", "email", "url", "password", "tel", "number", "time"].includes(t))
        return el.inputMode === "numeric" ? 600 : -1;
      if (t === "range" || t === "color") return 450;
    }
    return 250;
  }

  // Keep a range and its number box in step (data-ev2-pair="<key>").
  root.addEventListener("input", (ev) => {
    const el = ev.target;
    const key = el.dataset && el.dataset.ev2Pair;
    if (!key) return;
    const scope = el.closest("form") || root;
    scope.querySelectorAll('[data-ev2-pair="' + key + '"]').forEach((other) => {
      if (other === el) return;
      const v = Number(el.value);
      if (!Number.isFinite(v)) return;
      if (other.type === "range" && v > Number(other.max)) other.max = String(v);
      other.value = String(v);
    });
  });

  // Number boxes: Enter commits (blur) instead of submitting the form.
  root.addEventListener("keydown", (ev) => {
    const el = ev.target;
    if (ev.key !== "Enter" || el.tagName !== "INPUT") return;
    if (el.type === "checkbox" || el.type === "radio") return;
    if (!el.form && !el.closest("form")) return;
    ev.preventDefault();
    el.blur();
  });

  // ------------------------------------------------------------- name + icon
  const nameForm = root.querySelector("[data-ev2-name-form]");
  const nameInput = root.querySelector("[data-ev2-name]");
  const crumb = root.querySelector("[data-ev2-crumb]");
  let savedName = nameInput ? nameInput.value : "";
  // The name reads as a heading: the box is as wide as its text.
  let ruler = null;
  function fitName() {
    if (!nameInput) return;
    if (!ruler) {
      ruler = document.createElement("span");
      ruler.setAttribute("aria-hidden", "true");
      ruler.style.cssText = "position:absolute;visibility:hidden;white-space:pre;left:-9999px;top:0";
      document.body.append(ruler);
    }
    const cs = getComputedStyle(nameInput);
    ruler.style.font = cs.font;
    ruler.style.letterSpacing = cs.letterSpacing;
    ruler.textContent = nameInput.value || nameInput.placeholder;
    const pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth);
    let w = Math.ceil(ruler.getBoundingClientRect().width + pad + 2);
    nameInput.style.width = w + "px";
    // The field's own text shaping can run a little wider than the ruler.
    const over = nameInput.scrollWidth - nameInput.clientWidth;
    if (over > 0) nameInput.style.width = w + over + 4 + "px";
  }
  if (nameForm && nameInput) {
    fitName();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitName);
    window.addEventListener("resize", fitName);
    nameForm.addEventListener("submit", (ev) => ev.preventDefault());
    nameInput.addEventListener("input", fitName);
    nameInput.addEventListener("input", () => {
      if (crumb) crumb.textContent = nameInput.value.trim() || savedName;
    });
    nameInput.addEventListener("change", () => {
      const v = nameInput.value.trim();
      if (!v) {
        nameInput.value = savedName;
        fitName();
        if (crumb) crumb.textContent = savedName;
        return;
      }
      if (v === savedName) return;
      saveForm(nameForm).then(() => {
        savedName = v;
        document.title = document.title.replace(/^[^,]*/, v);
      });
    });
  }

  const iconForm = root.querySelector("[data-ev2-icon-form]");
  if (iconForm) {
    const hidden = iconForm.querySelector("[data-icon-value]");
    const trigger = iconForm.querySelector("[data-icon-trigger]");
    const paint = () => {
      const name = hidden.value;
      const cur = trigger.querySelector("[data-ev2-icon-current]");
      trigger.classList.toggle("set", Boolean(name));
      trigger.setAttribute("aria-label", "Dashboard icon" + (name ? ": " + name : ""));
      cur.innerHTML = name
        ? '<i class="ph ph-' + name.replace(/[^a-z0-9-]/gi, "") + '" aria-hidden="true"></i>'
        : '<i class="ph ph-plus" aria-hidden="true"></i><span>Icon</span>';
    };
    iconForm.addEventListener("submit", (ev) => ev.preventDefault());
    hidden.addEventListener("change", () => {
      // icon-picker.js repaints its own preview after dispatching; ours wins.
      setTimeout(paint, 0);
      saveForm(iconForm);
    });
  }

  // ------------------------------------------------------------ info tooltip
  const infoBtn = root.querySelector("[data-ev2-info]");
  const infoPop = infoBtn && document.getElementById(infoBtn.getAttribute("aria-controls"));
  function infoOpen(open) {
    if (!infoBtn || !infoPop) return;
    infoBtn.setAttribute("aria-expanded", open ? "true" : "false");
    infoPop.hidden = !open;
  }
  if (infoBtn && infoPop) {
    infoBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      infoOpen(infoPop.hidden);
      if (!infoPop.hidden) infoPop.querySelector("button")?.focus();
    });
    document.addEventListener("click", (ev) => {
      if (!infoPop.hidden && !infoPop.contains(ev.target)) infoOpen(false);
    });
  }

  // ----------------------------------------------------------- bottom bar
  // Duplicate and Push read the saved page on the server: wait for pending
  // autosaves first, and don't go ahead if one of them failed.
  document.querySelectorAll("form[data-ev2-after-save]").forEach((form) => {
    form.addEventListener("submit", async (ev) => {
      if (form.__ev2go) return;
      ev.preventDefault();
      const btn = ev.submitter;
      if (btn) btn.disabled = true;
      let ok = true;
      try {
        await flush();
      } catch (e) {
        ok = false;
      }
      if (btn) btn.disabled = false;
      if (!ok || lastError) return;
      form.__ev2go = true;
      form.submit();
    });
  });
  document.querySelectorAll("form[data-ev2-confirm-submit]").forEach((form) => {
    form.addEventListener("submit", (ev) => {
      if (!window.confirm(form.dataset.confirm)) ev.preventDefault();
    });
  });
  const discard = document.querySelector("form[data-ev2-discard]");
  if (discard) {
    discard.addEventListener("submit", (ev) => {
      if (!window.confirm("Delete this new dashboard? Nothing you set up here is kept.")) {
        ev.preventDefault();
        return;
      }
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
      failedForms.clear();
      try {
        sessionStorage.removeItem("ev2-step:" + pageId);
      } catch (e) {
        /* ignore */
      }
    });
  }
  document.querySelector("[data-ev2-finish]")?.addEventListener("click", async (ev) => {
    ev.preventDefault();
    if (ev.currentTarget.getAttribute("aria-disabled") === "true") return; // no display yet
    const href = ev.currentTarget.href;
    try {
      await flush();
    } catch (e) {
      return; // stay: the error state is showing and the edits are still in their forms
    }
    try {
      sessionStorage.removeItem("ev2-step:" + pageId);
    } catch (e) {
      /* ignore */
    }
    location.href = href;
  });

  // ---------------------------------------------------------------- Escape
  // One handler, highest layer first: the catalog, then edit mode, then the
  // info tooltip. Each owner registers a closer that returns true when it
  // handled the key.
  const escHandlers = [];
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || ev.defaultPrevented) return;
    if (infoPop && !infoPop.hidden) {
      infoOpen(false);
      infoBtn.focus();
      return;
    }
    for (const fn of escHandlers) {
      if (fn(ev)) {
        ev.preventDefault();
        return;
      }
    }
  });

  window.EV2 = {
    root,
    cfg,
    pageId,
    isNew,
    restored: restored || {},
    ui: uiState,
    post,
    saveForm,
    afterSave,
    flush,
    pending,
    reload,
    emit,
    on,
    setStatus,
    debounceDelay,
    onEscape: (fn, first) => (first ? escHandlers.unshift(fn) : escHandlers.push(fn)),
    // Run ``fn`` after every module script has registered (they load in order).
    ready: (fn) =>
      document.readyState === "loading"
        ? document.addEventListener("DOMContentLoaded", fn)
        : setTimeout(fn, 0),
  };

  // The schedule dialog saves the dashboard before it navigates (#280).
  window.tesseraeEditor = {
    isDirty: pending,
    saveAll: () =>
      flush().then(
        () => !lastError,
        () => false
      ),
  };
})();
