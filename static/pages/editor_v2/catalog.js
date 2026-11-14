// Dashboard editor v2: the widget catalog, a centred modal that opens for an
// empty cell or on Replace. Search, a category rail and widget rows; picking
// one saves the cell's new widget and reopens that cell's settings.
(function () {
  "use strict";
  const EV2 = window.EV2;
  if (!EV2) return;

  const pop = document.querySelector("[data-ev2-pk]");
  const scrim = document.querySelector("[data-ev2-pk-scrim]");
  if (!pop || !scrim) return;
  const titleEl = pop.querySelector("[data-ev2-pk-title]");
  const keepBtn = pop.querySelector("[data-ev2-pk-keep]");
  const closeBtn = pop.querySelector("[data-ev2-pk-close]");
  const q = pop.querySelector("[data-ev2-pk-q]");
  const catsEl = pop.querySelector("[data-ev2-pk-cats]");
  const listEl = pop.querySelector("[data-ev2-pk-list]");

  // widget_catalog (contract) or the classic widget_picker shape.
  let raw = {};
  try {
    raw = JSON.parse(document.getElementById("ev2-catalog-data")?.textContent || "{}") || {};
  } catch (e) {
    raw = {};
  }
  const WIDGETS = (raw.widgets || []).map((w) => ({
    key: w.key || w.id,
    name: w.name || w.item || w.key || w.id,
    family: w.family || "",
    icon: w.icon || "ph-squares-four",
    desc: w.description || w.desc || "",
    cat: w.category || "utility",
  }));
  const CATS = (raw.categories || []).map((c) => ({ key: c.key || c.id, label: c.label }));
  const catLabel = (k) => (CATS.find((c) => c.key === k) || {}).label || "";
  const count = (k) => (k === "all" ? WIDGETS.length : WIDGETS.filter((w) => w.cat === k).length);

  const S = { cellId: null, current: "", cat: "all", query: "", returnTo: null };

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function icon(cls) {
    const i = el("i", "ph " + String(cls).replace(/[^a-z0-9 -]/gi, ""));
    i.setAttribute("aria-hidden", "true");
    return i;
  }

  function renderCats() {
    catsEl.replaceChildren();
    const searching = Boolean(S.query.trim());
    [{ key: "all", label: "All widgets" }, ...CATS].forEach((c) => {
      const n = count(c.key);
      if (!n && c.key !== "all") return;
      const b = el("button");
      b.type = "button";
      b.dataset.cat = c.key;
      b.setAttribute("aria-pressed", !searching && S.cat === c.key ? "true" : "false");
      b.append(el("span", "", c.label), el("small", "", String(n)));
      catsEl.append(b);
    });
  }

  function renderList() {
    const query = S.query.trim().toLowerCase();
    const shown = query
      ? WIDGETS.filter((w) => (w.family + " " + w.name + " " + w.desc + " " + w.key).toLowerCase().includes(query))
      : WIDGETS.filter((w) => S.cat === "all" || w.cat === S.cat);
    listEl.replaceChildren();
    if (!shown.length) {
      const li = el("li", "ev2-pk-empty");
      li.textContent = `No widget matches “${S.query.trim()}”. Try a shorter word.`;
      listEl.append(li);
      return;
    }
    shown.forEach((w) => {
      const li = el("li");
      const b = el("button", "ev2-pk-row" + (w.key === S.current ? " on" : ""));
      b.type = "button";
      b.dataset.widget = w.key;
      const txt = el("span");
      const name = el("b", "", w.name);
      if (w.family) {
        name.append(" ", el("span", "ev2-tag", w.family));
      }
      txt.append(name);
      if (w.desc) txt.append(el("small", "", w.desc));
      const fit = el("span", "fit", w.key === S.current ? "In this cell" : query ? catLabel(w.cat) : "");
      b.append(icon(w.icon), txt, fit);
      if (w.key === S.current) b.setAttribute("aria-current", "true");
      li.append(b);
      listEl.append(li);
    });
  }

  function isOpen() {
    return !pop.hidden;
  }

  function open(cellId, opts) {
    const o = opts || {};
    const li = EV2.widgets && EV2.widgets.rowFor(cellId);
    if (!li) return;
    if (EV2.steps) {
      if (EV2.isNew) EV2.steps.setProgress(3);
      if (EV2.steps.current() !== "widgets") EV2.steps.open("widgets");
    }
    S.cellId = cellId;
    S.current = li.dataset.widget || "";
    S.query = "";
    const cur = WIDGETS.find((w) => w.key === S.current);
    S.cat = o.replace && cur ? cur.cat : "all";
    S.returnTo = document.activeElement;
    titleEl.textContent = S.current
      ? `Replace ${li.dataset.item || cur?.name || S.current}`
      : `Choose a widget for cell ${li.dataset.n}`;
    keepBtn.hidden = !S.current;
    q.value = "";
    renderCats();
    renderList();
    scrim.hidden = false;
    pop.hidden = false;
    document.documentElement.style.overflow = "hidden";
    EV2.emit("ev2:cell-focus", { cellId });
    q.focus();
    // Scroll the chosen category / current widget into view.
    pop.querySelector(".ev2-pk-cats [aria-pressed=true]")?.scrollIntoView({ block: "nearest", inline: "nearest" });
    pop.querySelector(".ev2-pk-row.on")?.scrollIntoView({ block: "nearest" });
  }

  function close() {
    if (!isOpen()) return;
    pop.hidden = true;
    scrim.hidden = true;
    document.documentElement.style.overflow = "";
    const openId = EV2.widgets && EV2.widgets.openId();
    if (openId !== S.cellId) EV2.emit("ev2:cell-focus", { cellId: openId || null });
    const back = S.returnTo;
    S.cellId = null;
    if (back && document.contains(back)) back.focus({ preventScroll: true });
  }

  function pick(key) {
    const cellId = S.cellId;
    const li = EV2.widgets && EV2.widgets.rowFor(cellId);
    if (!li) return;
    if (key === S.current) {
      close();
      EV2.widgets.openRow(cellId);
      return;
    }
    const form = li.querySelector("[data-ev2-cell-form]");
    listEl.querySelectorAll("button").forEach((b) => (b.disabled = true));
    // Send the pending edit while the form still says the old widget, then
    // swap once: the server resets the options to the new widget's defaults.
    // (Changing the field first would post the old form's values twice and
    // wipe the new widget's default-on options.)
    const field = form.querySelector("[data-ev2-plugin]");
    const was = field.value;
    EV2.flush()
      .then(() => {
        field.value = key;
        return EV2.saveForm(form);
      })
      .then(() => EV2.reload({ open: "widgets", cell: cellId }))
      .then((reloaded) => {
        if (reloaded === false) throw new Error("not reloaded");
      })
      .catch(() => {
        field.value = was; // the swap didn't save: the form keeps the current widget
        listEl.querySelectorAll("button").forEach((b) => (b.disabled = false));
      });
  }

  catsEl.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-cat]");
    if (!b) return;
    S.cat = b.dataset.cat;
    S.query = "";
    q.value = "";
    renderCats();
    renderList();
    listEl.scrollTop = 0;
  });
  listEl.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-widget]");
    if (b && !b.disabled) pick(b.dataset.widget);
  });
  q.addEventListener("input", () => {
    S.query = q.value;
    renderCats();
    renderList();
  });
  // Down arrow from the search box goes to the first widget.
  q.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      listEl.querySelector("button")?.focus();
    }
  });
  listEl.addEventListener("keydown", (ev) => {
    if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
    const all = [...listEl.querySelectorAll("button")];
    const i = all.indexOf(document.activeElement);
    if (i < 0) return;
    ev.preventDefault();
    if (ev.key === "ArrowUp" && i === 0) q.focus();
    else all[Math.max(0, Math.min(all.length - 1, i + (ev.key === "ArrowDown" ? 1 : -1)))].focus();
  });
  keepBtn.addEventListener("click", close);
  closeBtn.addEventListener("click", close);
  scrim.addEventListener("click", close);

  // Keep Tab inside the dialog.
  pop.addEventListener("keydown", (ev) => {
    if (ev.key !== "Tab") return;
    const f = [...pop.querySelectorAll("button:not([disabled]), input, [tabindex]:not([tabindex='-1'])")].filter(
      (x) => x.offsetParent !== null
    );
    if (!f.length) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  });
  EV2.onEscape(() => {
    if (!isOpen()) return false;
    close();
    return true;
  }, true);

  EV2.catalog = { open, close, isOpen };
})();
