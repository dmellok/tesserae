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
  // Dashboards the user agreed to bind to the chosen display: id -> "move"
  // (this display only) or "also" (keep its other display). Per display, so a
  // consent given for one display never carries to another.
  let consent = {};
  let consentDevice = deviceSel.value;
  let lastDevice = deviceSel.value;
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

  // -- binding ----------------------------------------------------------------
  const deviceInfo = (id) => {
    const d = (data.devices || []).find((x) => x.id === id) || { id, name: id };
    return { id: d.id, name: d.name, width: d.w || 0, height: d.h || 0 };
  };
  // A member is stranded when it is bound, but not to the chosen display.
  const strandedFor = (id, dev) => {
    const p = pagesById[id];
    return Boolean(dev && p && !p.missing && (p.devices || []).length && !p.devices.includes(dev));
  };
  const strandedPage = (id) => ({
    id,
    name: pagesById[id].name,
    bound_to: pagesById[id].devices.map(deviceInfo),
  });
  const needsConsent = (ids, dev) => {
    if (consentDevice !== dev) {
      consent = {};
      consentDevice = dev;
    }
    return ids.filter((id) => strandedFor(id, dev) && !consent[id]);
  };
  // Ask once for these dashboards; true when the user agreed (consent is
  // recorded), false when they cancelled.
  async function confirmBind(ids, dev) {
    const need = needsConsent(ids, dev);
    if (!need.length) return true;
    const mode = await window.TesseraeBindPrompt.ask({
      display: deviceInfo(dev),
      pages: need.map(strandedPage),
    });
    if (!mode) return false;
    need.forEach((id) => (consent[id] = mode));
    return true;
  }
  async function addOtherDisplay(id) {
    if (await confirmBind([id], deviceSel.value)) addMember(id);
  }
  // Consent travels with the form: the server binds exactly these, atomically
  // with the save, and refuses the save for any stranded step not listed.
  function syncConsent() {
    form.querySelectorAll("input[data-bind-consent]").forEach((n) => n.remove());
    const dev = deviceSel.value;
    orderedIds().forEach((id) => {
      if (!strandedFor(id, dev) || !consent[id]) return;
      [["bind_pages", id], ...(consent[id] === "also" ? [["bind_also", id]] : [])].forEach(
        ([name, value]) => {
          const inp = document.createElement("input");
          inp.type = "hidden";
          inp.name = name;
          inp.value = value;
          inp.dataset.bindConsent = "1";
          form.appendChild(inp);
        }
      );
    });
  }

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
    c.className =
      "dxe-pcard" + (selected === id ? " is-selected" : "") + (p.missing ? " is-missing" : "");
    if (p.missing) c.title = "This dashboard was deleted; remove it from the lineup";
    c.draggable = true;
    c.dataset.page = id;

    const thumb = document.createElement("div");
    thumb.className = "dxe-thumb";
    const target = deviceSel.options[deviceSel.selectedIndex];
    thumb.dataset.gamut = (target && target.dataset.gamut) || "color";
    thumb.dataset.greyLevels = (target && target.dataset.greyLevels) || "0";
    // Keep the dashboard's own shape instead of cropping it into a landscape box.
    if (p.w > 0 && p.h > 0) {
      thumb.style.setProperty("--thumb-ar", p.w + " / " + p.h);
      thumb.style.setProperty("--thumb-r", (p.w / p.h).toFixed(4));
      if (p.h > p.w) thumb.classList.add("is-portrait");
    }
    if (p.missing) {
      thumb.classList.add("is-ph");
      thumb.textContent = "!";
    } else if (p.thumb) {
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
    if (p.missing) {
      const rawId = document.createElement("small");
      rawId.className = "dxe-missing-id";
      rawId.textContent = id;
      name.appendChild(rawId);
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "dxe-remove dxe-remove-missing";
      rm.textContent = "Remove";
      rm.addEventListener("click", (e) => {
        e.stopPropagation();
        removeMember(id);
      });
      foot.appendChild(rm);
    }
    if (strandedFor(id, deviceSel.value)) {
      c.classList.add("is-offdisplay");
      const tag = document.createElement("span");
      tag.className = "dxe-offdisplay";
      tag.textContent = "Not on this display";
      tag.title =
        "This dashboard is bound to another display. You will be asked to bind it when you save.";
      foot.appendChild(tag);
    }
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
          updateAdvHint();
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
      (p) =>
        !p.missing &&
        !inDeck.has(p.id) &&
        ((p.devices || []).includes(dev) || (p.devices || []).length === 0)
    );
  }
  // Dashboards bound only to other displays: not offered by default, but never
  // hidden. Adding one asks to bind it first.
  function elsewhere() {
    const dev = deviceSel.value;
    const inDeck = new Set(members());
    if (!dev) return [];
    return (data.pages || []).filter(
      (p) => !p.missing && !inDeck.has(p.id) && strandedFor(p.id, dev)
    );
  }
  function renderAvailable() {
    avail.innerHTML = "";
    const list = addable();
    const other = elsewhere();
    availCount.textContent = list.length ? `${list.length} available` : "none left";
    const dev = deviceSel.options[deviceSel.selectedIndex];
    const devName = dev ? dev.textContent : "this display";
    availSub.textContent = deviceSel.value
      ? `Bound to ${devName}, or unbound (adding one binds it).`
      : "Choose a display to see its dashboards.";
    list.forEach((p) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "tg tg--lg dxe-chip";
      chip.innerHTML = `<i class="ph-bold ph-plus dxe-chip-plus" aria-hidden="true"></i>${escape(p.name)}`;
      chip.addEventListener("click", () => addMember(p.id));
      avail.appendChild(chip);
    });
    if (!other.length) return;
    const head = document.createElement("p");
    head.className = "dxe-help dxe-avail-other";
    head.textContent = "On other displays (adding one asks to bind it here)";
    avail.appendChild(head);
    other.forEach((p) => {
      const names = p.devices.map((d) => deviceInfo(d).name).join(", ");
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "tg tg--lg dxe-chip dxe-chip--other";
      chip.title = `Bound to ${names}`;
      chip.innerHTML = `<i class="ph-bold ph-plus dxe-chip-plus" aria-hidden="true"></i>${escape(p.name)}<span class="dxe-chip-where">${escape(names)}</span>`;
      chip.addEventListener("click", () => addOtherDisplay(p.id));
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

  // A full loop is the SUM of per-page dwells (pages without one inherit the
  // default-dwell select). Shown in the advance hint so the editor states the
  // real loop period instead of leaving the fallback to be read as one (#266).
  function loopMinutes(ids) {
    const fallback = parseInt((el("dxe-interval") || {}).value, 10) || 30;
    return ids.reduce((total, id) => {
      const inp = dwellInput(id);
      const v = inp ? parseInt(inp.value, 10) : NaN;
      return total + (isNaN(v) || v <= 0 ? fallback : v);
    }, 0);
  }

  function updateAdvHint() {
    if (!advHint) return;
    const mode = advanceMode();
    const base =
      mode === "manual"
        ? "a tap on the display flips to the next page"
        : mode === "timer"
          ? "pages auto-cycle, taps do nothing"
          : "auto-cycles, and a tap flips early";
    const ids = orderedIds();
    advHint.textContent =
      mode === "manual" || !ids.length ? base : base + " · full loop " + loopMinutes(ids) + " min";
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
    updateAdvHint();
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
  [el("dxe-interval"), returnSel].forEach(
    (c) => c && c.addEventListener("change", () => { markDirty(); render(); })
  );
  // Switching display keeps the steps, so any bound elsewhere need a decision
  // now; cancelling puts the previous display back and changes nothing.
  deviceSel.addEventListener("change", async () => {
    const dev = deviceSel.value;
    if (!(await confirmBind(orderedIds(), dev))) {
      deviceSel.value = lastDevice;
      render();
      return;
    }
    lastDevice = dev;
    markDirty();
    render();
  });
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
        v === 0
          ? "Manual only, pages re-render when pushed."
          : "How often member dashboards re-render out of view; takes over from each dashboard's own \"Updates\" setting while it's in this lineup.";
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
  // Last gate before the save: steps that were already on the wrong display
  // when the lineup was opened get the same question.
  let submitting = false;
  form.addEventListener("submit", (e) => {
    pagesField.value = orderedIds().join(",");
    if (submitting) {
      syncConsent();
      return;
    }
    if (!needsConsent(orderedIds(), deviceSel.value).length) {
      syncConsent();
      return;
    }
    e.preventDefault();
    confirmBind(orderedIds(), deviceSel.value).then((ok) => {
      if (!ok) return;
      submitting = true;
      form.requestSubmit();
    });
  });

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
