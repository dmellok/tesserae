// The widget's page (templates/news_illustrated/index.html): stories drawn
// as the frame draws them, a lightbox to see one large, and the settings'
// own controls (image providers on and in order, the Recommended set-up,
// prompt resets, the caption's look followed live by the previews). The API
// keys aren't on this page (they are changed under Settings > Widgets): the
// server says which are set, in each row's data-has-key.
//
// The previews call client.js's render(), the frame's own code, into a box
// the frame's size, scaled down to fit: what is shown is what the frame gets
// (bar the e-paper dithering).

const PROVIDER_IDS = ["kie", "gemini", "pollinations", "huggingface"];

const dataEl = document.getElementById("ain-data");
const DATA = dataEl ? JSON.parse(dataEl.textContent || "{}") : {};
const form = document.querySelector("[data-ain-settings]");
const prefix = DATA.prefix || "";

let renderStory = null;

// ---------------------------------------------------------------------------
// The caption's look, from the form as it stands (unsaved changes included).

function currentLook() {
  const look = { ...(DATA.look || {}) };
  if (!form) return look;
  const bg = form.elements.namedItem("caption_bg");
  const fg = form.elements.namedItem("caption_fg");
  if (bg && bg.value) look.bg = bg.value;
  if (fg && fg.value) look.fg = fg.value;
  const parts = { tag: "show_tag", date: "show_date", count: "show_count", title: "show_headline", summary: "show_summary" };
  for (const [key, name] of Object.entries(parts)) {
    const box = form.elements.namedItem(name);
    if (box) look[key] = box.checked;
  }
  return look;
}

// ---------------------------------------------------------------------------
// Drawing a story

function sizeOf(w, h) {
  const longer = Math.max(w, h);
  if (longer <= 200) return "xs";
  if (longer <= 400) return "sm";
  if (longer <= 700) return "md";
  return "lg";
}

function prefixUrls(root) {
  if (!prefix) return;
  for (const el of root.querySelectorAll("[href], [src]")) {
    for (const attr of ["href", "src"]) {
      const v = el.getAttribute(attr);
      if (v && v.startsWith("/") && !v.startsWith("//") && !v.startsWith(prefix)) el.setAttribute(attr, prefix + v);
    }
  }
}

async function draw(host, story) {
  const frame = DATA.frame || { w: 1440, h: 2560 };
  host.style.width = `${frame.w}px`;
  host.style.height = `${frame.h}px`;
  const shadow = host.shadowRoot || host.attachShadow({ mode: "open" });
  const ctx = {
    cell: { w: frame.w, h: frame.h, size: sizeOf(frame.w, frame.h), plugin: "news_illustrated", plugin_id: "news_illustrated", options: {}, fragment: "full" },
    panel: { w: frame.w, h: frame.h, portrait: frame.h > frame.w },
    data: { ...story, look: currentLook() },
    locale: document.documentElement.lang || "en",
    strings: {},
    t: (key, fallback) => fallback ?? key,
    preview: true,
  };
  await renderStory(shadow, ctx);
  prefixUrls(shadow);
}

// Scale a frame-sized host to the width of the box it sits in.
function fit(host, box) {
  const frame = DATA.frame || { w: 1440, h: 2560 };
  const scale = Math.min(box.clientWidth / frame.w, box.clientHeight / frame.h);
  host.style.transform = `scale(${scale})`;
  host.style.left = `${Math.max(0, (box.clientWidth - frame.w * scale) / 2)}px`;
}

const hosts = [...document.querySelectorAll("[data-ain-host]")];

function drawAll() {
  for (const host of hosts) {
    const story = (DATA.stories || [])[Number(host.dataset.ainHost)];
    if (story) draw(host, story).catch((err) => console.warn("news_illustrated preview:", err));
  }
  if (lightbox) lightbox.redraw();
}

// ---------------------------------------------------------------------------
// Lightbox: one story at the frame's shape, as large as the window allows.

let lightbox = null;

function openLightbox(index, opener) {
  const stories = DATA.stories || [];
  if (!stories.length || !renderStory) return;
  closeLightbox();
  const frame = DATA.frame || { w: 1440, h: 2560 };
  const overlay = document.createElement("div");
  overlay.className = "lightbox ain-lb";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.innerHTML = `
    <button type="button" class="lightbox-close" aria-label="Close"><i class="ph ph-x" aria-hidden="true"></i></button>
    <div class="ain-lb-stage"><span class="ain-host"></span></div>
    <div class="ain-lb-bar">
      <button type="button" class="ain-lb-nav" data-step="-1" aria-label="Previous story"><i class="ph ph-caret-left" aria-hidden="true"></i></button>
      <div class="ain-lb-text"></div>
      <button type="button" class="ain-lb-nav" data-step="1" aria-label="Next story"><i class="ph ph-caret-right" aria-hidden="true"></i></button>
    </div>`;
  const stage = overlay.querySelector(".ain-lb-stage");
  const host = overlay.querySelector(".ain-host");
  const text = overlay.querySelector(".ain-lb-text");
  for (const nav of overlay.querySelectorAll(".ain-lb-nav")) nav.hidden = stories.length < 2;

  function layout() {
    const room = overlay.getBoundingClientRect();
    const bar = overlay.querySelector(".ain-lb-bar").getBoundingClientRect().height;
    const maxW = room.width - 40;
    const maxH = room.height - 56 - 18 - 14 - bar;
    const scale = Math.max(0.05, Math.min(maxW / frame.w, maxH / frame.h));
    stage.style.width = `${Math.floor(frame.w * scale)}px`;
    stage.style.height = `${Math.floor(frame.h * scale)}px`;
    host.style.transform = `scale(${scale})`;
  }

  const state = {
    index,
    redraw() {
      const story = stories[state.index];
      overlay.setAttribute("aria-label", story.title || "Story");
      const link = /^https?:\/\//.test(story.url || "")
        ? `<a href="${encodeURI(story.url)}" target="_blank" rel="noopener noreferrer"></a>`
        : "<strong></strong>";
      text.innerHTML = `${link}<span></span>`;
      text.firstElementChild.textContent = story.title || "";
      text.lastElementChild.textContent = `${story.source || ""} · ${state.index + 1} of ${stories.length}`;
      draw(host, story).catch((err) => console.warn("news_illustrated lightbox:", err));
      layout();
    },
    step(by) {
      state.index = (state.index + by + stories.length) % stories.length;
      state.redraw();
    },
    close() {
      window.removeEventListener("resize", layout);
      overlay.remove();
      document.body.classList.remove("lightbox-open");
      if (opener && document.contains(opener)) opener.focus();
      lightbox = null;
    },
    overlay,
  };
  overlay.addEventListener("click", (ev) => {
    const nav = ev.target.closest(".ain-lb-nav");
    if (nav) state.step(Number(nav.dataset.step));
    else if (ev.target === overlay || ev.target.closest(".lightbox-close")) state.close();
  });
  overlay.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { ev.preventDefault(); state.close(); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); state.step(1); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); state.step(-1); }
    else if (ev.key === "Tab") {
      // Keep focus in the dialog.
      const stops = [...overlay.querySelectorAll("button:not([hidden]), a[href]")];
      const at = stops.indexOf(document.activeElement);
      if (ev.shiftKey && at <= 0) { ev.preventDefault(); stops[stops.length - 1].focus(); }
      else if (!ev.shiftKey && at === stops.length - 1) { ev.preventDefault(); stops[0].focus(); }
    }
  });
  document.body.appendChild(overlay);
  document.body.classList.add("lightbox-open");
  window.addEventListener("resize", layout);
  lightbox = state;
  state.redraw();
  overlay.querySelector(".lightbox-close").focus();
}

function closeLightbox() {
  if (lightbox) lightbox.close();
}

// ---------------------------------------------------------------------------
// Image providers: ticked ones are tried, in the chips' order. The hidden
// provider_order input is what is saved: "kie, gemini", or "none".

function setupProviders() {
  if (!form) return;
  const input = form.querySelector("[data-ain-order-input]");
  const list = form.querySelector("[data-ain-chips]");
  const status = form.querySelector("[data-ain-order-status]");
  const rows = Object.fromEntries([...form.querySelectorAll("[data-ain-prov]")].map((r) => [r.dataset.ainProv, r]));
  const boxes = Object.fromEntries(PROVIDER_IDS.map((id) => [id, form.querySelector(`[data-ain-enable="${id}"]`)]));
  const rec = DATA.recommended || {};
  if (!input || !list) return;
  const initial = input.value;

  function parse(raw) {
    const names = String(raw || "").toLowerCase().split(/[\s,>]+/).filter((n) => PROVIDER_IDS.includes(n));
    if (!names.length) return /^\s*none\s*$/i.test(String(raw || "")) ? [] : [...PROVIDER_IDS];
    return [...new Set(names)];
  }
  let order = parse(input.value);

  const nameOf = (id) => (rows[id] ? rows[id].dataset.name : id);
  const modelOf = (id) => form.elements.namedItem(`${id}_model`);
  const hasKey = (id) => keySet(rows[id]);

  function save(announce) {
    const value = order.length ? order.join(", ") : "none";
    if (input.value !== value) {
      input.value = value;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
    if (announce && status) status.textContent = announce;
    paint();
  }

  function paint() {
    // Chips, in order.
    list.replaceChildren();
    order.forEach((id, i) => {
      const li = document.createElement("li");
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = `ain-chip${hasKey(id) ? "" : " no-key"}`;
      chip.dataset.id = id;
      chip.setAttribute("aria-label", `${nameOf(id)}, tried ${ordinal(i + 1)}${hasKey(id) ? "" : ", needs a key"}. Arrow keys move it.`);
      chip.setAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight Home End");
      chip.innerHTML = `<i class="ph ph-dots-six-vertical" aria-hidden="true"></i><span class="ain-chip-n" aria-hidden="true">${i + 1}</span><span></span>`;
      chip.lastElementChild.textContent = nameOf(id);
      li.appendChild(chip);
      list.appendChild(li);
    });
    if (!order.length) {
      const li = document.createElement("li");
      li.className = "ain-chips-empty";
      li.textContent = "None: tick a provider below. Without one, stories are text only.";
      list.appendChild(li);
    }
    // Rows: ticked, status, order of the rows follows the chips.
    const ready = order.filter(hasKey);
    for (const id of PROVIDER_IDS) {
      const row = rows[id];
      if (!row) continue;
      const on = order.includes(id);
      if (boxes[id]) boxes[id].checked = on;
      row.classList.toggle("is-off", !on);
      const chip = row.querySelector("[data-ain-status]");
      if (!on) setChip(chip, "Off", "");
      else if (!hasKey(id)) setChip(chip, "Needs a key", "tg--warn");
      else if (ready[0] === id) setChip(chip, "In use, tried first", "tg--ok");
      else setChip(chip, `In use, tried ${ordinal(ready.indexOf(id) + 1)}`, "tg--ok");
    }
    // Recommended or Custom.
    const isRec = JSON.stringify(order) === JSON.stringify(rec.order || [])
      && Object.entries(rec.models || {}).every(([id, m]) => !modelOf(id) || modelOf(id).value.trim() === m);
    for (const b of form.querySelectorAll("[data-ain-setup]")) {
      b.setAttribute("aria-pressed", String((b.dataset.ainSetup === "recommended") === isRec));
    }
  }

  function move(id, to) {
    const from = order.indexOf(id);
    if (from < 0) return;
    to = Math.max(0, Math.min(order.length - 1, to));
    if (to === from) return;
    order.splice(from, 1);
    order.splice(to, 0, id);
    save(`${nameOf(id)} is now tried ${ordinal(to + 1)}.`);
    const chip = list.querySelector(`[data-id="${id}"]`);
    if (chip) chip.focus();
  }

  list.addEventListener("keydown", (ev) => {
    const chip = ev.target.closest(".ain-chip");
    if (!chip) return;
    const at = order.indexOf(chip.dataset.id);
    const to = { ArrowLeft: at - 1, ArrowUp: at - 1, ArrowRight: at + 1, ArrowDown: at + 1, Home: 0, End: order.length - 1 }[ev.key];
    if (to === undefined) return;
    ev.preventDefault();
    move(chip.dataset.id, to);
  });

  // Dragging with the pointer (mouse, pen and touch alike).
  let drag = null;
  list.addEventListener("pointerdown", (ev) => {
    const chip = ev.target.closest(".ain-chip");
    if (!chip || ev.button !== 0) return;
    drag = { id: chip.dataset.id, chip, moved: false, x: ev.clientX, y: ev.clientY };
    chip.setPointerCapture(ev.pointerId);
  });
  list.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    if (!drag.moved && Math.hypot(ev.clientX - drag.x, ev.clientY - drag.y) < 4) return;
    drag.moved = true;
    drag.chip.classList.add("is-dragging");
    // The chip under the pointer (by centre) is where it goes.
    const chips = [...list.querySelectorAll(".ain-chip")];
    let target = order.indexOf(drag.id);
    chips.forEach((c, i) => {
      const r = c.getBoundingClientRect();
      if (ev.clientY >= r.top - 4 && ev.clientY <= r.bottom + 4 && ev.clientX >= r.left && ev.clientX <= r.right) {
        target = i;
      }
    });
    if (target !== order.indexOf(drag.id)) {
      const id = drag.id;
      order.splice(order.indexOf(id), 1);
      order.splice(target, 0, id);
      save(`${nameOf(id)} is now tried ${ordinal(target + 1)}.`);
      const chip = list.querySelector(`[data-id="${id}"]`);
      chip.classList.add("is-dragging");
      chip.setPointerCapture(ev.pointerId);
      drag.chip = chip;
    }
  });
  const endDrag = () => {
    if (!drag) return;
    drag.chip.classList.remove("is-dragging");
    drag = null;
  };
  list.addEventListener("pointerup", endDrag);
  list.addEventListener("pointercancel", endDrag);
  list.addEventListener("lostpointercapture", (ev) => { if (drag && ev.target === drag.chip && !drag.moved) endDrag(); });

  for (const id of PROVIDER_IDS) {
    const box = boxes[id];
    if (box) {
      box.addEventListener("change", () => {
        if (box.checked && !order.includes(id)) order.push(id);
        if (!box.checked) order = order.filter((n) => n !== id);
        save(box.checked ? `${nameOf(id)} is on, tried ${ordinal(order.indexOf(id) + 1)}.` : `${nameOf(id)} is off.`);
      });
    }
    if (modelOf(id)) modelOf(id).addEventListener("input", paint);
  }

  for (const b of form.querySelectorAll("[data-ain-setup]")) {
    b.addEventListener("click", () => {
      if (b.dataset.ainSetup === "recommended") {
        order = [...(rec.order || PROVIDER_IDS)];
        for (const [id, m] of Object.entries(rec.models || {})) {
          const field = modelOf(id);
          if (field && field.value !== m) {
            field.value = m;
            field.dispatchEvent(new Event("input", { bubbles: true }));
          }
        }
        save("Recommended set-up: kie.ai first, then Gemini, Pollinations and HuggingFace.");
      } else {
        const first = Object.values(boxes).find(Boolean);
        if (first) first.focus();
      }
    });
  }

  form.addEventListener("reset", () => {
    setTimeout(() => { input.value = initial; order = parse(initial); paint(); }, 0);
  });
  paint();
}

// Whether a usable key is saved for a provider's (or Groq's) row.
function keySet(holder) {
  return Boolean(holder && holder.dataset.hasKey === "1");
}

function setChip(chip, text, tone) {
  if (!chip) return;
  chip.textContent = text;
  chip.className = `tg tg--sm${tone ? ` ${tone}` : ""}`;
}

function ordinal(n) {
  return ["first", "second", "third", "fourth"][n - 1] || `${n}th`;
}

// ---------------------------------------------------------------------------
// Summaries: Groq or off.

function setupSummaries() {
  if (!form) return;
  const groq = form.querySelector("[data-ain-groq]");
  const chip = groq && groq.querySelector("[data-ain-status]");
  function paint() {
    const choice = form.querySelector('[name="summary_service"]:checked');
    const off = choice && choice.value === "off";
    form.classList.toggle("is-summary-off", Boolean(off));
    if (off) setChip(chip, "Off", "");
    else if (keySet(groq)) setChip(chip, "In use", "tg--ok");
    else setChip(chip, "Needs a key", "tg--warn");
  }
  form.addEventListener("change", (ev) => { if (ev.target.name === "summary_service") paint(); });
  form.addEventListener("reset", () => setTimeout(paint, 0));
  paint();
}

// ---------------------------------------------------------------------------
// Prompt resets, the colour codes, and the previews following the look.

function setupFields() {
  if (!form) return;
  for (const button of form.querySelectorAll("[data-ain-reset]")) {
    const area = document.getElementById(button.dataset.ainReset);
    if (!area) continue;
    const sync = () => { button.disabled = area.value.trim() === (area.dataset.default || "").trim(); };
    button.addEventListener("click", () => {
      area.value = area.dataset.default || "";
      area.dispatchEvent(new Event("input", { bubbles: true }));
      area.focus();
    });
    area.addEventListener("input", sync);
    form.addEventListener("reset", () => setTimeout(sync, 0));
    sync();
  }
  for (const input of form.querySelectorAll('input[type="color"][data-ain-look]')) {
    const code = input.parentElement.querySelector("[data-ain-hex]");
    const sync = () => { if (code) code.textContent = input.value; };
    input.addEventListener("input", sync);
    form.addEventListener("reset", () => setTimeout(sync, 0));
  }
  let pending = 0;
  const redraw = () => {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(drawAll);
  };
  form.addEventListener("input", (ev) => { if (ev.target.matches("[data-ain-look]")) redraw(); });
  form.addEventListener("change", (ev) => { if (ev.target.matches("[data-ain-look]")) redraw(); });
  form.addEventListener("reset", () => setTimeout(redraw, 0));
}

// ---------------------------------------------------------------------------

// The run log opens on its newest lines, at the bottom.
function setupLog() {
  for (const pre of document.querySelectorAll("pre[data-ain-log]")) {
    const details = pre.closest("details");
    if (details) details.addEventListener("toggle", () => { if (details.open) pre.scrollTop = pre.scrollHeight; });
  }
}

async function start() {
  setupLog();
  setupProviders();
  setupSummaries();
  setupFields();
  if (!hosts.length) return;
  try {
    renderStory = (await import(DATA.client)).default;
  } catch (err) {
    console.warn("news_illustrated: could not load the frame's renderer", err);
    return; // the plain pictures stay
  }
  const sized = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const host = entry.target.querySelector("[data-ain-host]");
      if (host) fit(host, entry.target);
    }
  });
  for (const host of hosts) {
    host.replaceChildren();
    sized.observe(host.parentElement);
    fit(host, host.parentElement);
  }
  drawAll();
  document.addEventListener("click", (ev) => {
    const shot = ev.target.closest("[data-ain-open]");
    if (shot) openLightbox(Number(shot.dataset.ainOpen), shot);
  });
}

start();
