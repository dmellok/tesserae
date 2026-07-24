/* Deck editor ("dense rail + inspector"): progressive enhancement over
 * the no-JS fallback fieldset. The fallback's inputs (member checkboxes,
 * order numbers, home radios, override selects, timeout range) remain
 * the single source of truth; the rail/inspector UI reads and writes
 * them, so submit semantics are identical with or without JS. */
(function () {
  "use strict";
  const root = document.getElementById("deck-editor");
  if (!root) return;
  const data = JSON.parse(document.getElementById("dxe-data").textContent);
  const byId = {};
  data.pages.forEach((p) => (byId[p.id] = p));

  const fallback = document.getElementById("dxe-fallback");
  const rail = document.getElementById("dxe-rail");
  const inspector = document.getElementById("dxe-inspector");
  const library = document.getElementById("dxe-library");
  const libPills = document.getElementById("dxe-lib-pills");
  const libSuggested = document.getElementById("dxe-lib-suggested");
  const behavior = document.getElementById("dxe-behavior");
  const graphBox = document.getElementById("dxe-graph");
  const pagesField = document.getElementById("dxe-pages-field");
  const wrapHint = document.getElementById("dxe-wrap-hint");
  const timeoutInput = document.getElementById("dxe-timeout");
  const nameInput = document.getElementById("dxe-name");
  const title = document.getElementById("dxe-title");
  const cadenceSel = document.getElementById("dxe-cadence");

  fallback.classList.add("js-hidden");
  let selected = null;

  /* ----- fallback input accessors (source of truth) ----- */
  const row = (id) => fallback.querySelector(`.dxe-fb-row[data-page="${CSS.escape(id)}"]`);
  const memberBox = (id) => row(id) && row(id).querySelector('input[name="member"]');
  const orderInput = (id) => row(id) && row(id).querySelector('input[type="number"]');
  const homeRadio = (id) => row(id) && row(id).querySelector('input[name="home"]');
  const overrideSel = (id) => row(id) && row(id).querySelector("select");

  function orderedIds() {
    return data.pages
      .filter((p) => memberBox(p.id) && memberBox(p.id).checked)
      .sort((a, b) => (+orderInput(a.id).value || 0) - (+orderInput(b.id).value || 0))
      .map((p) => p.id);
  }
  function setOrder(ids) {
    ids.forEach((id, i) => {
      if (orderInput(id)) orderInput(id).value = String(i + 1);
    });
    pagesField.value = ids.join(",");
  }
  function homeId() {
    const checked = fallback.querySelector('input[name="home"]:checked');
    const ids = orderedIds();
    if (checked && ids.includes(checked.value)) return checked.value;
    return ids[0] || "";
  }
  function setHome(id) {
    if (homeRadio(id)) homeRadio(id).checked = true;
    render();
  }

  /* ----- rail ----- */
  function cardEl(id, pos, n) {
    const p = byId[id];
    const el = document.createElement("div");
    el.className = "dxe-page-card" + (selected === id ? " is-selected" : "");
    el.setAttribute("role", "button");
    el.tabIndex = 0;
    const thumb = document.createElement("div");
    thumb.className = "dxe-thumb";
    const img = document.createElement("img");
    img.alt = "";
    img.src = p.thumb;
    img.addEventListener("error", () => {
      thumb.classList.add("is-missing");
      img.remove();
      thumb.textContent = "not rendered";
    });
    thumb.appendChild(img);
    const foot = document.createElement("div");
    foot.className = "dxe-page-foot";
    foot.innerHTML =
      `<span class="dxe-mono">${pos}</span>` +
      `<span class="dxe-page-name" title="${p.name}">${p.name}</span>` +
      (homeId() === id ? '<span class="dxe-home-star">★</span>' : "");
    el.append(thumb, foot);
    el.addEventListener("click", () => {
      selected = id;
      render();
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selected = id;
        render();
      }
    });
    return el;
  }

  function renderRail() {
    const ids = orderedIds();
    rail.textContent = "";
    ids.forEach((id, i) => rail.appendChild(cardEl(id, i + 1, ids.length)));
    const add = document.createElement("button");
    add.type = "button";
    add.className = "dxe-add-tile";
    add.textContent = "+ Add page";
    add.addEventListener("click", () => {
      library.hidden = !library.hidden;
      renderLibrary();
    });
    rail.appendChild(add);
    wrapHint.textContent = ids.length > 1 ? `↺ wraps ${ids.length} → 1` : "";
  }

  /* ----- library ----- */
  function appendPage(id) {
    if (!memberBox(id)) return;
    memberBox(id).checked = true;
    const ids = orderedIds().filter((x) => x !== id);
    ids.push(id);
    setOrder(ids);
    selected = id;
    render();
  }
  function renderLibrary() {
    const memberSet = new Set(orderedIds());
    libPills.textContent = "";
    data.pages
      .filter((p) => !memberSet.has(p.id))
      .forEach((p) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "dxe-lib-pill";
        b.innerHTML = `${p.name} <span class="dxe-mono">#${p.id}</span>`;
        b.addEventListener("click", () => appendPage(p.id));
        libPills.appendChild(b);
      });
    if (!libPills.childElementCount) {
      libPills.innerHTML = '<span class="dxe-hint">every page is already in the deck</span>';
    }
    libSuggested.textContent = "";
    const clusters = (data.suggestions || []).filter((sg) =>
      sg.page_ids.some((id) => !memberSet.has(id) && byId[id])
    );
    if (clusters.length) {
      const label = document.createElement("div");
      label.className = "dxe-eyebrow";
      label.textContent = "SUGGESTED";
      libSuggested.appendChild(label);
      clusters.forEach((sg) => {
        const names = sg.page_ids.map((id) => (byId[id] ? byId[id].name : id)).join(" · ");
        const b = document.createElement("button");
        b.type = "button";
        b.className = "dxe-lib-pill dxe-lib-cluster";
        b.innerHTML = `${sg.name} — ${names} <strong>add all</strong>`;
        b.addEventListener("click", () => {
          sg.page_ids.forEach((id) => {
            if (byId[id] && !orderedIds().includes(id)) appendPage(id);
          });
        });
        libSuggested.appendChild(b);
      });
    }
  }

  /* ----- inspector ----- */
  function renderInspector() {
    const ids = orderedIds();
    if (!selected || !ids.includes(selected)) selected = ids[0] || null;
    if (!selected) {
      inspector.hidden = true;
      return;
    }
    inspector.hidden = false;
    const id = selected;
    const p = byId[id];
    const isHome = homeId() === id;
    const pos = ids.indexOf(id) + 1;
    inspector.textContent = "";

    const head = document.createElement("div");
    head.className = "dxe-card-head";
    head.innerHTML =
      `<span class="dxe-eyebrow">PAGE</span>` +
      `<span class="dxe-insp-name">${p.name} <span class="dxe-mono">#${p.id}</span></span>`;
    inspector.appendChild(head);

    const homeRow = document.createElement("div");
    homeRow.className = "dxe-home-row" + (isHome ? " is-home" : "");
    homeRow.innerHTML =
      `<span class="dxe-radio${isHome ? " on" : ""}" aria-hidden="true"></span>` +
      `<span>★ Home page — the deck returns here and Push sends it to the panel first.</span>`;
    homeRow.addEventListener("click", () => setHome(id));
    inspector.appendChild(homeRow);

    if (isHome) {
      const slider = document.createElement("div");
      slider.className = "dxe-slider-row";
      const label = document.createElement("label");
      label.textContent = "Return here after";
      label.htmlFor = "dxe-timeout";
      const out = document.createElement("span");
      out.className = "dxe-mono dxe-timeout-read";
      slider.append(label, timeoutInput, out);
      const helper = document.createElement("p");
      helper.className = "dxe-hint";
      helper.textContent = "0 = never · counts from the last button press or tap";
      inspector.append(slider, helper);
      const paint = () => {
        const v = +timeoutInput.value;
        out.textContent = v ? `${v} min` : "never";
        updateBehavior();
      };
      timeoutInput.addEventListener("input", paint);
      paint();
    } else {
      // Keep the range submitted from the fallback area when not shown.
      fallback.querySelector(".dxe-fb-row:last-child").appendChild(timeoutInput);
    }

    const ovWrap = document.createElement("div");
    ovWrap.className = "dxe-set";
    const ovLabel = document.createElement("span");
    ovLabel.className = "dxe-eyebrow";
    ovLabel.textContent = "REFRESH OVERRIDE";
    ovWrap.appendChild(ovLabel);
    const mirror = overrideSel(id).cloneNode(true);
    mirror.value = overrideSel(id).value;
    mirror.removeAttribute("name");
    mirror.querySelector('option[value=""]').textContent = `deck default (${cadenceSel.options[cadenceSel.selectedIndex].text.replace("every ", "")})`;
    mirror.addEventListener("change", () => {
      overrideSel(id).value = mirror.value;
    });
    ovWrap.appendChild(mirror);
    inspector.appendChild(ovWrap);

    const foot = document.createElement("div");
    foot.className = "dxe-insp-foot";
    const posLbl = document.createElement("span");
    posLbl.className = "dxe-hint";
    posLbl.textContent = `Position ${pos} of ${ids.length}`;
    const left = document.createElement("button");
    left.type = "button";
    left.className = "ghost";
    left.textContent = "←";
    left.disabled = pos === 1;
    left.addEventListener("click", () => move(id, -1));
    const right = document.createElement("button");
    right.type = "button";
    right.className = "ghost";
    right.textContent = "→";
    right.disabled = pos === ids.length;
    right.addEventListener("click", () => move(id, 1));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost danger";
    remove.textContent = "Remove";
    remove.disabled = ids.length <= 1;
    remove.addEventListener("click", () => {
      memberBox(id).checked = false;
      const rest = orderedIds();
      setOrder(rest);
      if (homeId() === "" || !rest.includes(homeId())) setHome(rest[0]);
      selected = rest[0] || null;
      render();
    });
    foot.append(posLbl, left, right, remove);
    inspector.appendChild(foot);
  }

  function move(id, delta) {
    const ids = orderedIds();
    const i = ids.indexOf(id);
    const j = i + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    setOrder(ids);
    render();
  }

  /* ----- behavior line + graph preview ----- */
  function updateBehavior() {
    const ids = orderedIds();
    const home = homeId();
    const t = +timeoutInput.value;
    let line = "◀ ▶ buttons flip through this order and wrap around";
    if (data.touchBound) line += " · tap the left / right edge on touch panels";
    line += t
      ? ` · returns to ★ ${byId[home] ? byId[home].name : home} after ${t} min idle.`
      : " · never returns automatically.";
    behavior.textContent = ids.length > 1 ? line : "";
    const graph = {};
    ids.forEach((id, i) => {
      graph[id] = {
        prev: ids[(i - 1 + ids.length) % ids.length],
        next: ids[(i + 1) % ids.length],
        home: id === home ? true : undefined,
      };
    });
    graphBox.value = JSON.stringify(graph, null, 2);
  }

  function render() {
    setOrder(orderedIds());
    renderRail();
    renderInspector();
    if (!library.hidden) renderLibrary();
    updateBehavior();
  }

  nameInput.addEventListener("input", () => {
    title.textContent = nameInput.value || "New deck";
  });
  cadenceSel.addEventListener("change", render);
  document.getElementById("deck-form").addEventListener("submit", () => {
    pagesField.value = orderedIds().join(",");
  });

  render();
})();
