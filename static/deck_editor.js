/* Deck editor (device-first filmstrip + reveal drawer).
 *
 * Progressive enhancement: the no-JS <fieldset id="dxe-fallback"> (member
 * checkboxes, order numbers, home radios, override selects, timeout range)
 * stays the single source of truth. This script hides it and drives the same
 * inputs from a filmstrip of page thumbnails plus a per-card settings drawer.
 * The chosen display (#dxe-device) filters the "add a dashboard" library to the
 * pages assigned to that device.
 */
(function () {
  "use strict";

  const root = document.getElementById("deck-editor");
  if (!root) return;
  const data = JSON.parse(document.getElementById("dxe-data").textContent);

  const form = document.getElementById("deck-form");
  const fallback = document.getElementById("dxe-fallback");
  const rail = document.getElementById("dxe-rail");
  const drawer = document.getElementById("dxe-drawer");
  const library = document.getElementById("dxe-library");
  const libPills = document.getElementById("dxe-lib-pills");
  const behavior = document.getElementById("dxe-behavior");
  const pagesField = document.getElementById("dxe-pages-field");
  const deviceSel = document.getElementById("dxe-device");
  const deviceHint = document.getElementById("dxe-device-hint");
  const nameInput = document.getElementById("dxe-name");
  const title = document.getElementById("dxe-title");
  const timeoutInput = document.getElementById("dxe-timeout");
  const timeoutMirror = document.getElementById("dxe-timeout-mirror");
  const advInterval = document.getElementById("dxe-adv-interval");
  const advHint = document.getElementById("dxe-adv-hint");
  const advRadios = () => Array.from(form.querySelectorAll('input[name="advance"]'));
  const advanceMode = () => (advRadios().find((r) => r.checked) || {}).value || "manual";

  const pagesById = {};
  (data.pages || []).forEach((p) => (pagesById[p.id] = p));

  const OVERRIDES = [
    ["", "deck default"],
    ["5", "every 5 min"],
    ["15", "every 15 min"],
    ["60", "every 60 min"],
    ["0", "only on Push"],
  ];

  let selected = null; // page id whose drawer is open

  // -- fallback accessors (the source of truth) -----------------------------
  const row = (id) => fallback.querySelector(`.dxe-fb-row[data-page="${CSS.escape(id)}"]`);
  const memberBox = (id) => row(id) && row(id).querySelector('input[name="member"]');
  const orderInput = (id) => row(id) && row(id).querySelector('input[type="number"]');
  const homeRadio = (id) => row(id) && row(id).querySelector('input[name="home"]');
  const overrideSel = (id) => row(id) && row(id).querySelector("select");
  const dwellInput = (id) => row(id) && row(id).querySelector('input[name^="dwell"]');

  function members() {
    return (data.pages || [])
      .map((p) => p.id)
      .filter((id) => memberBox(id) && memberBox(id).checked);
  }
  function orderedIds() {
    return members().sort((a, b) => (+orderInput(a).value || 0) - (+orderInput(b).value || 0));
  }
  function setOrder(ids) {
    ids.forEach((id, i) => {
      if (orderInput(id)) orderInput(id).value = String(i + 1);
    });
  }
  function homeId() {
    const checked = fallback.querySelector('input[name="home"]:checked');
    const ids = orderedIds();
    return checked && ids.includes(checked.value) ? checked.value : ids[0] || "";
  }
  function setHome(id) {
    if (homeRadio(id)) homeRadio(id).checked = true;
    render();
  }
  function addMember(id) {
    if (!memberBox(id)) return;
    memberBox(id).checked = true;
    if (orderInput(id)) orderInput(id).value = String(orderedIds().length + 1);
    selected = id;
    render();
  }
  function removeMember(id) {
    if (memberBox(id)) memberBox(id).checked = false;
    if (selected === id) selected = null;
    setOrder(orderedIds());
    render();
  }

  // -- filmstrip cards ------------------------------------------------------
  function card(id, pos) {
    const p = pagesById[id] || { name: id, thumb: "" };
    const el = document.createElement("div");
    el.className = "dxe-card2" + (selected === id ? " is-selected" : "");
    el.draggable = true;
    el.dataset.page = id;

    const thumb = document.createElement("div");
    thumb.className = "dxe-thumb";
    if (p.thumb) {
      const img = document.createElement("img");
      img.src = p.thumb;
      img.alt = "";
      img.loading = "lazy";
      img.addEventListener("error", () => {
        thumb.classList.add("is-missing");
        thumb.textContent = "not rendered";
      });
      thumb.appendChild(img);
    } else {
      thumb.classList.add("is-missing");
      thumb.textContent = "not rendered";
    }
    el.appendChild(thumb);

    const foot = document.createElement("div");
    foot.className = "dxe-page-foot";
    const badge = document.createElement("span");
    badge.className = "dxe-pos";
    badge.textContent = pos;
    const name = document.createElement("span");
    name.className = "dxe-page-name";
    name.textContent = p.name;
    foot.appendChild(badge);
    foot.appendChild(name);
    if (homeId() === id) {
      const star = document.createElement("span");
      star.className = "dxe-home-star";
      star.textContent = "★";
      star.title = "home page";
      foot.appendChild(star);
    }
    el.appendChild(foot);

    el.addEventListener("click", () => {
      selected = selected === id ? null : id;
      render();
    });

    // drag to reorder
    el.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", id);
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("is-dragging");
    });
    el.addEventListener("dragend", () => el.classList.remove("is-dragging"));
    el.addEventListener("dragover", (e) => e.preventDefault());
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      const dragId = e.dataTransfer.getData("text/plain");
      if (!dragId || dragId === id) return;
      const ids = orderedIds().filter((x) => x !== dragId);
      const at = ids.indexOf(id);
      ids.splice(at, 0, dragId);
      setOrder(ids);
      render();
    });
    return el;
  }

  function addTile() {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "dxe-add-tile";
    el.innerHTML = '<span class="dxe-add-plus">+</span><span>add page</span>';
    el.addEventListener("click", () => {
      library.hidden = false;
      library.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    el.disabled = !deviceSel.value;
    return el;
  }

  // -- reveal drawer --------------------------------------------------------
  function renderDrawer() {
    drawer.innerHTML = "";
    if (!selected || !memberBox(selected) || !memberBox(selected).checked) {
      drawer.hidden = true;
      return;
    }
    const p = pagesById[selected] || { name: selected };
    drawer.hidden = false;

    const head = document.createElement("div");
    head.className = "dxe-drawer-head";
    head.textContent = p.name;
    drawer.appendChild(head);

    const controls = document.createElement("div");
    controls.className = "dxe-drawer-controls";

    // make home
    const homeBtn = document.createElement("button");
    homeBtn.type = "button";
    homeBtn.className = "dxe-mini" + (homeId() === selected ? " is-on" : "");
    homeBtn.innerHTML = homeId() === selected ? "★ home page" : "☆ make home";
    homeBtn.addEventListener("click", () => setHome(selected));
    controls.appendChild(homeBtn);

    // refresh override
    const wrap = document.createElement("label");
    wrap.className = "dxe-mini-field";
    wrap.innerHTML = "<span>↻ refresh</span>";
    const sel = document.createElement("select");
    OVERRIDES.forEach(([v, label]) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = label;
      if ((overrideSel(selected).value || "") === v) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => {
      overrideSel(selected).value = sel.value;
    });
    wrap.appendChild(sel);
    controls.appendChild(wrap);

    // per-page dwell (only when the deck advances on a timer)
    if (advanceMode() !== "manual") {
      const dwrap = document.createElement("label");
      dwrap.className = "dxe-mini-field";
      dwrap.innerHTML = "<span>⏱ dwell</span>";
      const din = document.createElement("input");
      din.type = "number";
      din.min = "1";
      din.max = "10080";
      din.placeholder = "deck interval";
      din.className = "dxe-dwell-in";
      din.value = (dwellInput(selected) && dwellInput(selected).value) || "";
      din.addEventListener("input", () => {
        if (dwellInput(selected)) dwellInput(selected).value = din.value;
      });
      dwrap.appendChild(din);
      controls.appendChild(dwrap);
    }

    // remove
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "dxe-mini dxe-mini-danger";
    rm.innerHTML = "✕ remove";
    rm.addEventListener("click", () => removeMember(selected));
    controls.appendChild(rm);

    drawer.appendChild(controls);
  }

  // -- add-page library, filtered by the chosen display ---------------------
  function renderLibrary() {
    libPills.innerHTML = "";
    const dev = deviceSel.value;
    const inDeck = new Set(members());
    const assigned = (data.pages || []).filter(
      (p) => !inDeck.has(p.id) && dev && (p.devices || []).includes(dev)
    );
    const unassigned = (data.pages || []).filter(
      (p) => !inDeck.has(p.id) && (!p.devices || p.devices.length === 0)
    );

    if (!dev) {
      library.hidden = true;
      return;
    }

    const mkPill = (p, muted) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "dxe-lib-pill" + (muted ? " is-muted" : "");
      b.textContent = p.name;
      b.addEventListener("click", () => addMember(p.id));
      return b;
    };

    assigned.forEach((p) => libPills.appendChild(mkPill(p, false)));
    if (!assigned.length) {
      const empty = document.createElement("p");
      empty.className = "dxe-hint";
      empty.textContent =
        "No dashboards are assigned to this display. Bind pages to it under Dashboards, or add an unassigned one below.";
      libPills.appendChild(empty);
    }
    if (unassigned.length) {
      const sep = document.createElement("div");
      sep.className = "dxe-eyebrow dxe-lib-sep";
      sep.textContent = "NOT ASSIGNED TO A DISPLAY";
      libPills.appendChild(sep);
      unassigned.forEach((p) => libPills.appendChild(mkPill(p, true)));
    }
  }

  // -- main render ----------------------------------------------------------
  function render() {
    const ids = orderedIds();
    setOrder(ids);

    rail.innerHTML = "";
    ids.forEach((id, i) => rail.appendChild(card(id, i + 1)));
    rail.appendChild(addTile());

    pagesField.value = ids.join(",");
    renderDrawer();
    renderLibrary();

    // behaviour line
    if (!deviceSel.value) {
      behavior.textContent = "Choose a display above to start adding dashboards.";
    } else if (!ids.length) {
      behavior.textContent = "Add one or more dashboards from the library below.";
    } else if (ids.length === 1) {
      behavior.textContent = "A single-page deck just keeps this page warm on the display.";
    } else {
      behavior.textContent =
        ids.length +
        " pages, flipping " +
        ids.map((id) => (pagesById[id] || {}).name || id).join(" → ") +
        " ↺";
    }

    // advance mode
    const mode = advanceMode();
    if (advInterval) advInterval.style.display = mode === "manual" ? "none" : "";
    if (advHint) {
      advHint.textContent =
        mode === "manual"
          ? "moves on a tap / button / swipe"
          : mode === "timer"
            ? "auto-cycles the pages on a timer"
            : "auto-cycles on a timer, and also moves on a tap";
    }

    // timeout mirror
    const t = +(timeoutInput ? timeoutInput.value : 0) || 0;
    if (timeoutMirror) timeoutMirror.textContent = t === 0 ? "off" : t + " min";

    // title
    if (title) title.textContent = (nameInput.value || "").trim() || "New deck";
  }

  // -- wiring ---------------------------------------------------------------
  // Class-based hide: a bare [hidden] loses to `.dxe-fallback { display: flex }`.
  fallback.classList.add("js-hidden");
  deviceSel.addEventListener("change", () => {
    if (deviceHint) {
      deviceHint.textContent = deviceSel.value
        ? "The deck runs on this display, and only its dashboards are offered below."
        : "Choose a display to see its dashboards.";
    }
    render();
  });
  if (nameInput) nameInput.addEventListener("input", render);
  if (timeoutInput) timeoutInput.addEventListener("input", render);
  advRadios().forEach((r) => r.addEventListener("change", render));
  if (advInterval) advInterval.addEventListener("change", render);
  form.addEventListener("submit", () => {
    pagesField.value = orderedIds().join(",");
  });

  render();
})();
