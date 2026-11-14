// Dashboard editor v2: the step accordion, plus steps 1 (Display) and
// 2 (Layout). New dashboards (?new=1) unlock steps in order; editing keeps
// one step open at a time with the rest collapsed to a summary line.
(function () {
  "use strict";
  const EV2 = window.EV2;
  if (!EV2) return;
  const root = EV2.root;
  const cfg = EV2.cfg;

  const sections = [...root.querySelectorAll(".ev2-step[data-step]")];
  const KEYS = sections.map((s) => s.dataset.step);
  const WAIT = {
    layout: "Pick a display first.",
    widgets: "Choose a layout first.",
    // "optional" is in these steps' titles; the wait line doesn't repeat it.
    look: "Defaults look fine.",
    lineup: "Add it to a lineup that rotates it with others.",
  };
  const STEP_KEY = "ev2-step:" + EV2.pageId;

  // New mode: how far the walk has got (1..6; 6 = every step seen).
  let progress = 1;
  if (EV2.isNew) {
    try {
      progress = Math.max(1, Number(sessionStorage.getItem(STEP_KEY)) || 1);
    } catch (e) {
      progress = 1;
    }
  }
  const restored = EV2.restored || {};
  const hashStep = KEYS.includes(location.hash.slice(1)) ? location.hash.slice(1) : null;
  const initialStep = hashStep || (EV2.isNew ? KEYS[Math.min(progress, KEYS.length) - 1] : "widgets");
  let open = restored.open !== undefined ? restored.open : initialStep;
  if (EV2.isNew && hashStep) setProgress(KEYS.indexOf(hashStep) + 1);

  function stateOf(key) {
    const idx = KEYS.indexOf(key) + 1;
    if (key === open) return "open";
    if (!EV2.isNew) return "done";
    return idx <= progress ? "done" : "wait";
  }

  // A collapsed step shows its check only when there is nothing left to do
  // in it: every cell has a widget, the dashboard is in a lineup. Empty
  // cells are worth a look (amber number); no lineup is just optional.
  function completion(key) {
    if (key === "layout") return root.querySelector(".ev2-cl[data-n]") ? "yes" : "no";
    if (key === "widgets") {
      const rows = [...root.querySelectorAll(".ev2-cl[data-n]")];
      if (!rows.length) return "no";
      return rows.every((r) => r.dataset.widget) ? "yes" : "attn";
    }
    if (key === "lineup" || key === "schedules")
      return root.querySelector("[data-ev2-lrow]") ? "yes" : "no";
    return "yes";
  }

  function render() {
    sections.forEach((sec) => {
      const key = sec.dataset.step;
      const st = stateOf(key);
      sec.dataset.state = st;
      const done = completion(key);
      sec.dataset.complete = done === "yes" ? "yes" : "no";
      sec.toggleAttribute("data-attn", st === "done" && done === "attn");
      const head = sec.querySelector(".ev2-st-h");
      const body = sec.querySelector(".ev2-st-body");
      head.setAttribute("aria-expanded", st === "open" ? "true" : "false");
      // New mode: the step you are on can't be folded away, and later ones
      // wait their turn.
      head.disabled = st === "wait" || (EV2.isNew && st === "open");
      if (body) body.hidden = st !== "open";
      const wait = sec.querySelector("[data-ev2-wait]");
      if (wait) wait.textContent = st === "wait" ? WAIT[key] || "" : "";
    });
    EV2.ui.open = open;
    renderBar();
    syncPlaceholder();
  }

  // New dashboard that hasn't reached the layout step: the preview shows a
  // screen-sized placeholder instead of the panel-pixel render.
  function syncPlaceholder() {
    const on =
      EV2.isNew && progress < 2 && !root.querySelector(".ev2-cl[data-widget]:not([data-widget=''])");
    if (EV2.placeholder === on) return;
    EV2.placeholder = on;
    EV2.emit("ev2:placeholder", { on });
  }

  function renderBar() {
    if (!EV2.isNew) return;
    const st = document.querySelector("[data-ev2-bar-st]");
    if (!st) return;
    const step = progress;
    const lead = step > 5 ? "All steps done" : step === 5 ? "Last step" : `Step ${step} of 5`;
    const rows = [...root.querySelectorAll(".ev2-cl[data-n]")];
    const filled = rows.filter((r) => r.dataset.widget).length;
    // The long line is for wide screens; the short one keeps a status in the
    // bar at phone width.
    const long = document.createElement("span");
    long.className = "ev2-long";
    long.append(lead + " · changes save as you go");
    if (rows.length) {
      const b = document.createElement("b");
      b.textContent = `${filled} of ${rows.length} cell${rows.length === 1 ? "" : "s"} filled`;
      long.append(" · ", b);
    }
    const short = document.createElement("span");
    short.className = "ev2-short";
    short.textContent = lead;
    st.replaceChildren(long, short);
    // Done waits for a display, as in the mockup: picked in step 1 (a real
    // one) or confirmed with Next.
    const fin = document.querySelector("[data-ev2-finish]");
    if (fin) {
      const off = progress < 2 && cfg.usingTest === "1";
      fin.setAttribute("aria-disabled", off ? "true" : "false");
      fin.title = off ? "Pick a display first" : "Show the finished editor";
    }
  }

  function setProgress(n) {
    progress = Math.max(progress, n);
    try {
      sessionStorage.setItem(STEP_KEY, String(progress));
    } catch (e) {
      /* ignore */
    }
  }

  function openStep(key, opts) {
    const o = opts || {};
    if (!KEYS.includes(key) && key !== null) return;
    const was = open;
    if (key && EV2.isNew) setProgress(KEYS.indexOf(key) + 1);
    open = key;
    if (was === "layout" && key !== "layout") setDraw(false);
    if (was === "widgets" && key !== "widgets" && EV2.widgets) EV2.widgets.closeAll();
    render();
    if (o.scroll && key) {
      // "start" with the step's scroll-margin: its title lands below the
      // sticky app header instead of under it.
      document.getElementById("ev2-step-" + key)?.scrollIntoView({ block: o.block || "nearest", behavior: "smooth" });
    }
    if (o.focus && key) focusStep(key);
  }

  // Keyboard focus for a step you just moved to. A header that can't be
  // pressed (the open step in new mode) can't take focus, so the step's body
  // (a labelled region, tabindex -1) does.
  function focusStep(key) {
    const head = document.getElementById("ev2-h-" + key);
    if (head && !head.disabled) {
      head.focus({ preventScroll: true });
      return;
    }
    const body = document.getElementById("ev2-b-" + key);
    if (body && !body.hidden) {
      body.tabIndex = -1;
      body.focus({ preventScroll: true });
    }
  }

  root.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-ev2-toggle]");
    if (t && !t.disabled) {
      const key = t.dataset.ev2Toggle;
      openStep(open === key ? null : key);
      return;
    }
    const nx = ev.target.closest("[data-ev2-next]");
    if (nx) {
      const idx = KEYS.indexOf(nx.dataset.ev2Next);
      const next = KEYS[idx + 1] || null;
      setProgress(idx + 2);
      openStep(next, { scroll: true, block: "start" });
      if (next === "widgets" && EV2.widgets) EV2.widgets.openFirstEmpty();
      else if (next) focusStep(next);
    }
  });

  // ---------------------------------------------------------- 1 · Display
  function boundIds() {
    return [...root.querySelectorAll("[data-ev2-dev]")]
      .filter((c) => c.dataset.ev2Dev && c.getAttribute("aria-pressed") === "true")
      .map((c) => c.dataset.ev2Dev);
  }
  root.addEventListener("click", (ev) => {
    const card = ev.target.closest("[data-ev2-dev]");
    if (!card) return;
    const id = card.dataset.ev2Dev;
    let ids = boundIds();
    if (!id) {
      if (!ids.length) return; // already on the test display
      ids = [];
    } else {
      ids = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
    }
    // Paint the choice now; the reload brings the new preview shapes.
    root.querySelectorAll("[data-ev2-dev]").forEach((c) => {
      const on = c.dataset.ev2Dev ? ids.includes(c.dataset.ev2Dev) : !ids.length;
      c.classList.toggle("on", on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });
    EV2.post(cfg.updateUrl, { device_ids_present: "1", device_ids: ids })
      .then(() => EV2.reload())
      .catch(() => {});
  });

  function saveTestSize(w, h) {
    EV2.post(cfg.updateUrl, { test_display_w: w, test_display_h: h })
      .then(() => EV2.reload())
      .catch(() => {});
  }
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-ev2-size]");
    if (!b || b.getAttribute("aria-pressed") === "true") return;
    const [w, h] = b.dataset.ev2Size.split("x").map(Number);
    b.parentElement.querySelectorAll("[data-ev2-size]").forEach((x) => x.setAttribute("aria-pressed", x === b ? "true" : "false"));
    saveTestSize(w, h);
  });
  root.querySelectorAll("[data-ev2-custom-size]").forEach((inp) => {
    inp.addEventListener("change", () => {
      const w = parseInt(document.getElementById("ev2-tw").value, 10);
      const h = parseInt(document.getElementById("ev2-th").value, 10);
      const ok = (v) => Number.isInteger(v) && v >= 100 && v <= 4000;
      if (!ok(w) || !ok(h)) {
        EV2.setStatus("error", "Use 100 to 4000 px");
        return;
      }
      saveTestSize(w, h);
    });
  });

  // ----------------------------------------------------------- 2 · Layout
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-ev2-layout]");
    if (!b) return;
    const slug = b.dataset.ev2Layout;
    if (slug === "custom") return;
    const slots = Number(b.dataset.slots || 0);
    const losing = [...root.querySelectorAll(".ev2-cl[data-n]")]
      .slice(slots)
      .filter((li) => li.dataset.widget)
      .map((li) => li.dataset.item || li.dataset.widget);
    if (
      losing.length &&
      !window.confirm(
        `${b.dataset.name} has ${slots} cell${slots === 1 ? "" : "s"}, so ` +
          `${losing.length} widget${losing.length === 1 ? "" : "s"} won't fit and will be removed ` +
          `with their settings:\n\n${losing.join("\n")}\n\nDrag them into the cells you're keeping first if you want them.`
      )
    )
      return;
    root.querySelectorAll("[data-ev2-layout]").forEach((x) => {
      x.classList.toggle("on", x === b);
      x.setAttribute("aria-pressed", x === b ? "true" : "false");
    });
    EV2.flush()
      .then(() => EV2.post(cfg.layoutUrl, { layout: slug }))
      .then(() => {
        if (EV2.isNew) setProgress(2);
        EV2.reload();
      })
      .catch(() => {});
  });

  // Edit on preview (split / remove happen on the preview, preview.js).
  const drawBtn = root.querySelector("[data-ev2-draw]");
  const drawbar = root.querySelector("[data-ev2-drawbar]");
  let drawing = false;
  function setDraw(on) {
    if (!drawBtn || drawing === on) return;
    drawing = on;
    // Split and remove reload the page; the reload brings edit mode back.
    EV2.ui.draw = on;
    drawBtn.setAttribute("aria-pressed", on ? "true" : "false");
    drawBtn.classList.toggle("ev2-btn--primary", on);
    drawBtn.querySelector("span").textContent = on ? "Done drawing" : "Edit on preview";
    if (drawbar) drawbar.hidden = !on;
    EV2.emit("ev2:edit-mode", { on });
    if (on) EV2.emit("ev2:snap", snapState());
    // Stacked layout: the preview sits above the steps, out of sight from
    // this switch. Take the user to it.
    if (on && !restoring && stacked()) {
      document.getElementById("ev2-preview")?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }
  let restoring = false;
  // The preview is above the steps when the editor grid has one column
  // (a container query in editor_v2.css decides).
  function stacked() {
    const ed = root.querySelector(".ev2-ed");
    return Boolean(ed) && getComputedStyle(ed).gridTemplateColumns.trim().split(/\s+/).length < 2;
  }
  EV2.on("ev2:edit-done", () => {
    setDraw(false);
    drawBtn?.focus({ preventScroll: true });
  });
  drawBtn?.addEventListener("click", () => setDraw(!drawing));
  EV2.onEscape(() => {
    if (!drawing) return false;
    setDraw(false);
    drawBtn.focus();
    return true;
  });

  // Snap to grid: not in the contract's event list; published for the
  // preview as ``ev2:snap`` and on EV2.snap.
  const snap = root.querySelector("[data-ev2-snap]");
  const snapDims = root.querySelector("[data-ev2-snapdims]");
  function snapState() {
    const n = (sel, d) => {
      const v = parseInt(root.querySelector(sel)?.value, 10);
      return v >= 2 && v <= 48 ? v : d;
    };
    return { on: Boolean(snap && snap.checked), cols: n("[data-ev2-snap-cols]", 12), rows: n("[data-ev2-snap-rows]", 8) };
  }
  function publishSnap() {
    EV2.snap = snapState();
    if (snapDims) snapDims.hidden = !EV2.snap.on;
    EV2.emit("ev2:snap", EV2.snap);
  }
  snap?.addEventListener("change", publishSnap);
  root.querySelectorAll("[data-ev2-snap-cols], [data-ev2-snap-rows]").forEach((i) => i.addEventListener("change", publishSnap));
  EV2.snap = snapState();

  root.querySelector("[data-ev2-refit]")?.addEventListener("click", () => {
    const w = cfg.panelW;
    const h = cfg.panelH;
    if (
      !window.confirm(
        `Stretch every cell to fit the ${w}×${h} panel? Uneven stretching can soften lines that met exactly; you can drag them back on the preview.`
      )
    )
      return;
    EV2.flush()
      .then(() => EV2.post(cfg.refitUrl, {}))
      .then(() => EV2.reload())
      .catch(() => {});
  });

  const sbar = root.querySelector("[data-ev2-sbar]");
  sbar?.addEventListener("change", () => {
    EV2.flush()
      .then(() => EV2.post(cfg.statusBarUrl, {}))
      .then(() => EV2.reload())
      .catch(() => {
        sbar.checked = !sbar.checked;
      });
  });

  // The preview saved a resize, split or remove.
  function fracCells(cells) {
    const W = Number(cfg.panelW) || 1;
    const H = Number(cfg.panelH) || 1;
    return cells.map((c) => [c.x / W, c.y / H, c.w / W, c.h / H]);
  }
  function miniLayout(cells) {
    const gw = Number(cfg.groupW) || 1;
    const gh = Number(cfg.groupH) || 1;
    const k = Math.min(36 / gw, 22 / gh);
    const TW = Math.round(gw * k);
    const TH = Math.round(gh * k);
    const wrap = document.createElement("span");
    wrap.className = "ev2-mlay";
    wrap.style.cssText = `width:${TW}px;height:${TH}px`;
    fracCells(cells).forEach(([x, y, w, h]) => {
      const s = document.createElement("span");
      s.style.cssText = `left:${x * TW}px;top:${y * TH}px;width:${w * TW}px;height:${h * TH}px`;
      wrap.append(s);
    });
    return wrap;
  }
  EV2.on("ev2:layout-changed", (d) => {
    const cells = Array.isArray(d.cells) ? d.cells : [];
    const have = [...root.querySelectorAll(".ev2-cl[data-cell-id]")].map((li) => li.dataset.cellId).sort();
    const got = cells.map((c) => c.id).sort();
    // A split or remove changed the set of cells: re-render from the server.
    if (have.join() !== got.join()) {
      EV2.reload();
      return;
    }
    const barId = root.querySelector(".ev2-cl[data-bar]")?.dataset.cellId;
    const content = cells.filter((c) => c.id !== barId);
    const sum = root.querySelector('[data-ev2-sum="layout"]');
    if (sum) {
      const n = content.length;
      sum.textContent = `Your own layout · ${n} cell${n === 1 ? "" : "s"}` + (barId ? " · status bar" : "");
    }
    const vis = root.querySelector('[data-ev2-vis="layout"]');
    if (vis) vis.replaceChildren(miniLayout(content));
    root.querySelectorAll("[data-ev2-layout]").forEach((x) => {
      if (x.dataset.ev2Layout === "custom") return;
      x.classList.remove("on");
      x.setAttribute("aria-pressed", "false");
    });
    // Empty rows name their shape; keep that current.
    content.forEach((c) => {
      const li = root.querySelector(`.ev2-cl[data-cell-id="${CSS.escape(c.id)}"]`);
      if (!li) return;
      const r = c.w / Math.max(1, c.h);
      li.dataset.shape = r > 1.6 ? "wide" : r < 0.7 ? "tall" : "square-ish";
      const txt = li.querySelector("[data-ev2-shape-txt]");
      if (txt) txt.textContent = `Cell ${li.dataset.n} · ${li.dataset.shape}`;
    });
  });

  // Back from a split / remove reload: edit mode was on, so it still is.
  if (restored.draw && open === "layout" && drawBtn && !drawBtn.disabled) {
    restoring = true;
    setDraw(true);
    restoring = false;
  }

  EV2.steps = { open: openStep, current: () => open, render, renderBar, setProgress, keys: KEYS };
  render();
})();
