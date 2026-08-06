/* Deck editor (design-handoff rebuild): editable title + switch, segmented
 * advance, a filmstrip flip-order grid with a selected-page field panel, a right
 * rail (Deck settings + Available pages chips), and a sticky action bar.
 *
 * The no-JS <fieldset id="dxe-fallback"> (member / order / home / override /
 * dwell) stays the source of truth for per-page data; the deck-level fields
 * (name, enabled, advance, interval, device, cadence, timeout) are real inputs.
 * This script hides the fallback and renders the rich UI over the same inputs,
 * so the editor-save contract is unchanged.
 */
(function () {
  "use strict";

  const root = document.getElementById("deck-editor");
  if (!root) return;
  const data = JSON.parse(document.getElementById("dxe-data").textContent);
  const form = document.getElementById("deck-form");
  const fallback = document.getElementById("dxe-fallback");

  const el = (id) => document.getElementById(id);
  const rail = el("dxe-rail");
  const panel = el("dxe-panel");
  const avail = el("dxe-avail");
  const pagesField = el("dxe-pages-field");
  const titleInput = el("dxe-title");
  const enabledBox = form.querySelector('input[name="enabled"]');
  const statusWord = el("dxe-status-word");
  const deviceSel = el("dxe-device");
  const cadenceRange = el("dxe-cadence-range");
  const cadenceNum = el("dxe-cadence-num");
  const cadenceHelp = el("dxe-cadence-help");
  const returnSel = el("dxe-returnhome");
  const returnHelp = el("dxe-returnhome-help");
  const intervalWrap = el("dxe-interval-wrap");
  const advHint = el("dxe-adv-hint");
  const metaEl = el("dxe-meta");
  const statusEl = el("dxe-status");
  const availCount = el("dxe-avail-count");
  const availSub = el("dxe-avail-sub");

  const pagesById = {};
  (data.pages || []).forEach((p) => (pagesById[p.id] = p));

  const OVERRIDES = [
    ["", "deck default"],
    ["5", "every 5 min"],
    ["30", "every 30 min"],
    ["0", "never"],
  ];

  let selected = null;
  let dirty = false;
  const markDirty = () => {
    dirty = true;
    renderStatus();
  };

  // -- fallback accessors (source of truth for per-page data) ---------------
  const row = (id) => fallback.querySelector(`.dxe-fb-row[data-page="${CSS.escape(id)}"]`);
  const memberBox = (id) => row(id) && row(id).querySelector('input[name="member"]');
  const orderInput = (id) => row(id) && row(id).querySelector('input[type="number"][name^="order"]');
  const homeRadio = (id) => row(id) && row(id).querySelector('input[name="home"]');
  const overrideSel = (id) => row(id) && row(id).querySelector("select");
  const dwellInput = (id) => row(id) && row(id).querySelector('input[name^="dwell"]');

  const members = () =>
    (data.pages || []).map((p) => p.id).filter((id) => memberBox(id) && memberBox(id).checked);
  const orderedIds = () =>
    members().sort((a, b) => (+orderInput(a).value || 0) - (+orderInput(b).value || 0));
  const setOrder = (ids) => ids.forEach((id, i) => orderInput(id) && (orderInput(id).value = String(i + 1)));
  const homeId = () => {
    const checked = fallback.querySelector('input[name="home"]:checked');
    const ids = orderedIds();
    return checked && ids.includes(checked.value) ? checked.value : ids[0] || "";
  };
  const advanceMode = () => (form.querySelector('input[name="advance"]:checked') || {}).value || "manual";

  function addMember(id) {
    if (!memberBox(id)) return;
    memberBox(id).checked = true;
    orderInput(id).value = String(orderedIds().length + 1);
    selected = id;
    markDirty();
    render();
  }
  function removeMember(id) {
    if (memberBox(id)) memberBox(id).checked = false;
    const remaining = orderedIds();
    selected = remaining.length ? remaining[Math.max(0, remaining.indexOf(id) - 1)] || remaining[0] : null;
    setOrder(remaining);
    markDirty();
    render();
  }
  function movePage(id, delta) {
    const cur = orderedIds();
    const i = cur.indexOf(id);
    const j = i + delta;
    if (j < 0 || j >= cur.length) return;
    cur.splice(i, 1);
    cur.splice(j, 0, id);
    setOrder(cur);
    markDirty();
    render();
  }

  // -- filmstrip cards ------------------------------------------------------
  function card(id, pos) {
    const p = pagesById[id] || { name: id, thumb: "", kind: "" };
    const c = document.createElement("div");
    c.className = "dxe-pcard" + (selected === id ? " is-selected" : "");
    c.draggable = true;
    c.dataset.page = id;

    const thumb = document.createElement("div");
    thumb.className = "dxe-thumb";
    if (p.thumb) {
      const img = document.createElement("img");
      img.src = p.thumb;
      img.alt = "";
      img.loading = "lazy";
      img.addEventListener("error", () => {
        thumb.classList.add("is-ph");
        thumb.textContent = (p.kind || "page").toUpperCase();
      });
      thumb.appendChild(img);
    } else {
      thumb.classList.add("is-ph");
      thumb.textContent = (p.kind || "page").toUpperCase();
    }
    c.appendChild(thumb);

    const badge = document.createElement("span");
    badge.className = "dxe-badge";
    badge.textContent = pos;
    c.appendChild(badge);

    const grip = document.createElement("span");
    grip.className = "dxe-grip";
    grip.textContent = "⠿";
    c.appendChild(grip);

    const foot = document.createElement("div");
    foot.className = "dxe-pcard-foot";
    const name = document.createElement("span");
    name.className = "dxe-pcard-name";
    name.textContent = p.name;
    foot.appendChild(name);
    if (homeId() === id) {
      const star = document.createElement("span");
      star.className = "dxe-star";
      star.textContent = "★";
      star.title = "Home page";
      foot.appendChild(star);
    }
    c.appendChild(foot);

    c.addEventListener("click", () => {
      selected = id;
      render();
    });
    c.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", id);
      e.dataTransfer.effectAllowed = "move";
      selected = id;
      c.classList.add("is-dragging");
    });
    c.addEventListener("dragend", () => c.classList.remove("is-dragging"));
    c.addEventListener("dragover", (e) => {
      e.preventDefault();
      const dragId = e.dataTransfer.getData("text/plain") || window.__dxeDrag;
      if (!dragId || dragId === id) return;
      const ids = orderedIds().filter((x) => x !== dragId);
      ids.splice(ids.indexOf(id), 0, dragId);
      setOrder(ids);
      markDirty();
      render();
    });
    // some browsers don't expose getData during dragover; stash it.
    c.addEventListener("dragstart", () => (window.__dxeDrag = id));
    return c;
  }

  function addTile() {
    const t = document.createElement("button");
    t.type = "button";
    t.className = "dxe-add";
    t.innerHTML = '<span class="dxe-add-plus">+</span><span>add page</span>';
    t.disabled = !deviceSel.value || addable().length === 0;
    t.addEventListener("click", () => {
      const first = addable()[0];
      if (first) addMember(first.id);
    });
    return t;
  }

  // -- selected-page panel --------------------------------------------------
  function renderPanel() {
    panel.innerHTML = "";
    const ids = orderedIds();
    if (!selected || !ids.includes(selected)) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    const p = pagesById[selected] || { name: selected };
    const pos = ids.indexOf(selected) + 1;

    const head = document.createElement("div");
    head.className = "dxe-panel-head";
    head.innerHTML =
      `<span class="dxe-mono-label">PAGE ${pos}</span><span class="dxe-panel-name">${escape(p.name)}</span>`;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "dxe-remove";
    rm.textContent = "Remove from deck";
    rm.addEventListener("click", () => removeMember(selected));
    head.appendChild(rm);
    panel.appendChild(head);

    const grid = document.createElement("div");
    grid.className = "dxe-fieldgrid";

    // POSITION
    grid.appendChild(
      field("POSITION", () => {
        const wrap = document.createElement("div");
        wrap.className = "dxe-pos-btns";
        const mk = (label, delta) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "dxe-fbtn";
          b.textContent = label;
          b.disabled = delta < 0 ? pos <= 1 : pos >= ids.length;
          b.addEventListener("click", () => movePage(selected, delta));
          return b;
        };
        wrap.append(mk("←", -1), mk("→", 1));
        return wrap;
      })
    );
    // HOME
    grid.appendChild(
      field("HOME PAGE", () => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "dxe-fbtn dxe-home-btn";
        const isHome = homeId() === selected;
        b.textContent = isHome ? "★ is home" : "☆ make home";
        b.disabled = isHome;
        b.addEventListener("click", () => {
          if (homeRadio(selected)) homeRadio(selected).checked = true;
          markDirty();
          render();
        });
        return b;
      })
    );
    // REFRESH
    grid.appendChild(
      field("REFRESH", () => {
        const s = document.createElement("select");
        OVERRIDES.forEach(([v, label]) => {
          const o = document.createElement("option");
          o.value = v;
          o.textContent = label;
          if ((overrideSel(selected).value || "") === v) o.selected = true;
          s.appendChild(o);
        });
        s.addEventListener("change", () => {
          overrideSel(selected).value = s.value;
          markDirty();
        });
        return s;
      })
    );
    // DWELL
    grid.appendChild(
      field("DWELL", () => {
        const box = document.createElement("div");
        box.className = "dxe-dwell-box";
        const inp = document.createElement("input");
        inp.type = "number";
        inp.min = "1";
        inp.max = "10080";
        inp.placeholder = "deck interval";
        inp.className = "dxe-dwell-inp";
        inp.value = (dwellInput(selected) && dwellInput(selected).value) || "";
        inp.addEventListener("input", () => {
          if (dwellInput(selected)) dwellInput(selected).value = inp.value;
          markDirty();
        });
        const suf = document.createElement("span");
        suf.className = "dxe-dwell-suf";
        suf.textContent = "min";
        box.append(inp, suf);
        return box;
      })
    );
    panel.appendChild(grid);
  }

  function field(label, build) {
    const f = document.createElement("div");
    f.className = "dxe-field";
    const l = document.createElement("span");
    l.className = "dxe-mono-label";
    l.textContent = label;
    f.appendChild(l);
    f.appendChild(build());
    return f;
  }

  // -- available pages ------------------------------------------------------
  function addable() {
    const dev = deviceSel.value;
    const inDeck = new Set(members());
    if (!dev) return [];
    return (data.pages || []).filter(
      (p) => !inDeck.has(p.id) && ((p.devices || []).includes(dev) || (p.devices || []).length === 0)
    );
  }
  function renderAvailable() {
    avail.innerHTML = "";
    const list = addable();
    availCount.textContent = list.length ? `${list.length} available` : "none left";
    const dev = deviceSel.options[deviceSel.selectedIndex];
    const devName = dev ? dev.textContent : "this display";
    availSub.textContent = deviceSel.value
      ? `Bound to ${devName}, or unbound (adding one binds it).`
      : "Choose a display to see its dashboards.";
    list.forEach((p) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "dxe-chip";
      chip.innerHTML = `<span class="dxe-chip-plus">+</span>${escape(p.name)}`;
      chip.addEventListener("click", () => addMember(p.id));
      avail.appendChild(chip);
    });
  }

  // -- status / meta --------------------------------------------------------
  function renderStatus() {
    const ids = orderedIds();
    const chain = ids.map((id) => (pagesById[id] || {}).name || id).join(" → ");
    if (statusEl) statusEl.textContent = (dirty ? "Unsaved changes" : "Saved") + (chain ? " · " + chain : "");
    if (statusWord) statusWord.textContent = enabledBox.checked ? "Enabled" : "Disabled";
  }

  // -- main render ----------------------------------------------------------
  function render() {
    const ids = orderedIds();
    setOrder(ids);
    rail.innerHTML = "";
    ids.forEach((id, i) => rail.appendChild(card(id, i + 1)));
    rail.appendChild(addTile());
    pagesField.value = ids.join(",");

    if (metaEl) metaEl.textContent = `${ids.length} page${ids.length === 1 ? "" : "s"} · drag to reorder`;

    const mode = advanceMode();
    if (intervalWrap) intervalWrap.style.display = mode === "manual" ? "none" : "";
    const advMore = el("dxe-adv-more");
    if (advMore) advMore.style.display = mode === "manual" ? "none" : "";
    // Smart sync only means something when a timer drives the deck.
    const smartWrap = el("dxe-smartsync-wrap");
    if (smartWrap) smartWrap.style.display = mode === "manual" ? "none" : "";
    // Return-home is the inverse: it only means something when the deck waits
    // for a person. Under a timer the deck reclaims its own panel at the next
    // boundary, and a timeout shorter than the interval parks it on home in
    // between, which reads as "it stopped cycling". Disabled as well as hidden
    // so a stale value can't be submitted from a control nobody can see.
    const returnWrap = el("dxe-returnhome-wrap");
    if (returnWrap) returnWrap.style.display = mode === "manual" ? "" : "none";
    if (returnSel) returnSel.disabled = mode !== "manual";
    if (advHint) {
      advHint.textContent =
        mode === "manual"
          ? "a tap on the display flips to the next page"
          : mode === "timer"
            ? "pages auto-cycle, taps do nothing"
            : "auto-cycles, and a tap flips early";
    }
    if (returnHelp) {
      const hn = (pagesById[homeId()] || {}).name;
      returnHelp.textContent = hn ? `Snaps back to ★ ${hn}.` : "Snaps back to the home page.";
    }
    renderPanel();
    renderAvailable();
    renderStatus();
  }

  function escape(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // -- wiring ---------------------------------------------------------------
  fallback.classList.add("js-hidden");
  if (titleInput) titleInput.addEventListener("input", markDirty);
  if (enabledBox) enabledBox.addEventListener("change", () => { markDirty(); renderStatus(); });
  form.querySelectorAll('input[name="advance"]').forEach((r) => r.addEventListener("change", () => { markDirty(); render(); }));
  [el("dxe-interval"), deviceSel, returnSel].forEach(
    (c) => c && c.addEventListener("change", () => { markDirty(); render(); })
  );
  // Background-refresh slider <-> number box, kept in sync; 0 = manual only.
  function syncCadence(from) {
    if (!cadenceRange || !cadenceNum) return;
    let v = parseInt(from.value, 10);
    if (isNaN(v)) v = 0;
    v = Math.max(0, Math.min(1440, v));
    cadenceNum.value = String(v);
    cadenceRange.value = String(Math.min(v, +cadenceRange.max));
    if (cadenceHelp) {
      cadenceHelp.textContent =
        v === 0 ? "Manual only, pages re-render when pushed." : "How often pages re-render out of view.";
    }
  }
  if (cadenceRange) cadenceRange.addEventListener("input", () => { syncCadence(cadenceRange); markDirty(); });
  if (cadenceNum) cadenceNum.addEventListener("input", () => { syncCadence(cadenceNum); markDirty(); });
  if (cadenceNum) syncCadence(cadenceNum);
  // Discard = leave without saving; every bottom action lands on Lineups.
  const discard = el("dxe-discard");
  if (discard) {
    discard.addEventListener("click", () => {
      window.location.assign(discard.dataset.lineupsUrl || "/decks");
    });
  }
  form.addEventListener("submit", () => (pagesField.value = orderedIds().join(",")));

  // The wizard's "Create and open the editor" escape lands here with
  // ?open=conditions; the fold is server-rendered open, bring it into view.
  if (new URLSearchParams(window.location.search).get("open") === "conditions") {
    const cond = el("dxe-conditions");
    if (cond) {
      cond.open = true;
      cond.scrollIntoView({ block: "center" });
    }
  }

  render();
})();
