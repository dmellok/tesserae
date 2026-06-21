// condition-picker.js
//
// Lightweight picker UI for the v0.48 condition primitive used by
// schedules + rotation steps. Reads JSON from a hidden textarea on
// init, renders editable rows above it using the host's existing
// form vocabulary (button.ghost, .dow-picker > .dow-chip, plain
// <select> / <input>), and writes JSON back on every change.
//
// The textarea stays as the canonical source so the existing form
// parser keeps working; the "Edit raw JSON" disclosure lets power
// users see / paste a hand-written array.
//
// HA entity list comes from GET /api/conditions/ha-entities and is
// cached for the page's lifetime; per-condition Test uses the
// existing POST /api/conditions/test endpoint.

const PREFIX = window.TESSERAE_URL_PREFIX || "";
const HA_ENTITIES_URL = `${PREFIX}/api/conditions/ha-entities`;
const TEST_URL = `${PREFIX}/api/conditions/test`;

const SOURCE_KINDS = [
  { value: "ha_entity",   label: "HA entity" },
  { value: "time_window", label: "Time window" },
  { value: "sun",         label: "Sun" },
];

const HA_OPS = [
  { value: "==", label: "is" },
  { value: "!=", label: "is not" },
  { value: ">",  label: "greater than" },
  { value: "<",  label: "less than" },
  { value: ">=", label: "at least" },
  { value: "<=", label: "at most" },
  { value: "in", label: "is one of" },
  { value: "present_within_seconds", label: "updated within (s)" },
];

const SUN_OPS = [
  { value: "before_sunrise", label: "before sunrise" },
  { value: "after_sunset",   label: "after sunset" },
  { value: "is_day",         label: "during the day" },
  { value: "is_night",       label: "during the night" },
];

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

let _haCachePromise = null;

function haEntities() {
  if (_haCachePromise == null) {
    _haCachePromise = fetch(HA_ENTITIES_URL, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : { entities: [] }))
      .then((d) => (Array.isArray(d.entities) ? d.entities : []))
      .catch(() => []);
  }
  return _haCachePromise;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k === "on") {
      for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
    } else if (v === true) {
      node.setAttribute(k, "");
    } else if (v != null && v !== false) {
      node.setAttribute(k, v);
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function parseJsonLoose(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return [];
  try {
    const parsed = JSON.parse(trimmed);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return null;
  }
}

function defaultCondition(kind) {
  if (kind === "ha_entity") {
    return { source_kind: "ha_entity", source_id: "", operator: "==", value: "" };
  }
  if (kind === "time_window") {
    return {
      source_kind: "time_window",
      source_id: "",
      operator: "in",
      value: { start_local: "06:00", end_local: "23:00", days_of_week: [] },
    };
  }
  if (kind === "sun") {
    return {
      source_kind: "sun",
      source_id: "",
      operator: "after_sunset",
      value: { offset_minutes: 0 },
    };
  }
  return null;
}

// Minimal JSON syntax highlighter for the raw-edit overlay. Splits the
// source into spans so a CSS theme can colour strings / numbers /
// keywords / punctuation independently. Returns a string of HTML.
function highlightJson(src) {
  // Escape HTML metacharacters first; quote characters are deliberately
  // not escaped because the regex below uses real quotes as token
  // boundaries. (The previous version matched ``&quot;`` against the
  // escaped output, but the escape map didn't include " so the regex
  // hit nothing and the overlay rendered as plain colourless text.)
  let html = String(src ?? "").replace(/[&<>]/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
  }[c]));
  html = html.replace(
    /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (match, key, str, kw, num) => {
      if (key) return `<span class="cp-jh-key">${match}</span>`;
      if (str) return `<span class="cp-jh-str">${match}</span>`;
      if (kw) return `<span class="cp-jh-kw">${match}</span>`;
      if (num) return `<span class="cp-jh-num">${match}</span>`;
      return match;
    },
  );
  return html + "\n"; // trailing newline so the overlay matches the textarea's scroll height
}

function init(picker) {
  const textarea = picker.querySelector("textarea");
  if (!textarea) return;
  // The textarea is the form field that the server reads. We don't
  // hide it inline because it gets moved into the .cp-json-editor
  // below; the disclosure's open/close state then controls its
  // visibility naturally.

  // Stable id per picker so the HA entity datalist can be reused per
  // ha_entity row without colliding across pickers on the same page
  // (the rotation editor has one picker per step).
  const pickerId = picker.id || `cp-${Math.random().toString(36).slice(2, 8)}`;
  picker.id = pickerId;
  const datalistId = `cp-entities-${pickerId}`;

  const rowsHost = el("div", { class: "cp-rows" });
  const status = el("div", { class: "cp-status", "aria-live": "polite" });
  const errBanner = el(
    "p",
    { class: "field-help cp-error", hidden: true },
    "Raw JSON below is invalid. Fix it to re-enable the row editor.",
  );

  const addBtn = el(
    "button",
    {
      type: "button",
      class: "ghost",
      on: {
        click: () =>
          editRows((rows) => [...rows, defaultCondition("ha_entity")]),
      },
    },
    el("i", { class: "ph ph-plus", "aria-hidden": "true" }),
    el("span", {}, " Add condition"),
  );
  const testBtn = el(
    "button",
    {
      type: "button",
      class: "ghost",
      on: { click: () => runTest() },
    },
    el("i", { class: "ph ph-check", "aria-hidden": "true" }),
    el("span", {}, " Test conditions"),
  );

  const actions = el("div", { class: "cp-actions" }, addBtn, testBtn, status);

  // Syntax-highlight overlay. The <pre> renders coloured JSON behind
  // a transparent-text textarea so the caret + selection still work
  // natively. Scroll position syncs on input so the highlight tracks
  // the cursor for long edits.
  const highlightLayer = el("pre", { class: "cp-jh", "aria-hidden": "true" });
  const jsonEditor = el(
    "div",
    { class: "cp-json-editor" },
    highlightLayer,
    textarea,
  );
  const advancedDetails = el(
    "details",
    { class: "cp-advanced" },
    el("summary", {}, "Edit raw JSON"),
    jsonEditor,
  );

  function refreshHighlight() {
    highlightLayer.innerHTML = highlightJson(textarea.value);
    // Keep the overlay scroll position in lockstep with the textarea
    // so the highlight matches the visible region.
    highlightLayer.scrollTop = textarea.scrollTop;
    highlightLayer.scrollLeft = textarea.scrollLeft;
  }
  textarea.addEventListener("scroll", refreshHighlight);

  picker.appendChild(errBanner);
  picker.appendChild(rowsHost);
  picker.appendChild(actions);
  picker.appendChild(advancedDetails);
  refreshHighlight();

  // One datalist per picker for HA entity autocomplete.
  const datalist = el("datalist", { id: datalistId });
  picker.appendChild(datalist);
  haEntities().then((entities) => {
    entities.forEach((e) => {
      datalist.appendChild(el("option", { value: e.value }, e.label));
    });
  });

  function readRows() {
    return parseJsonLoose(textarea.value);
  }

  function writeRows(rows) {
    textarea.value = JSON.stringify(rows, null, 2);
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
    refreshHighlight();
    render();
  }

  function editRows(transform) {
    const cur = readRows();
    if (cur == null) return;
    writeRows(transform(cur));
  }

  function syncFromTextarea() {
    refreshHighlight();
    const parsed = readRows();
    if (parsed == null) {
      errBanner.hidden = false;
      addBtn.disabled = true;
    } else {
      errBanner.hidden = true;
      addBtn.disabled = false;
    }
    render();
  }

  textarea.addEventListener("input", syncFromTextarea);

  async function runTest() {
    const rows = readRows();
    if (rows == null || rows.length === 0) {
      status.textContent = "Add a condition first.";
      status.dataset.tone = "warn";
      return;
    }
    status.textContent = "Testing…";
    delete status.dataset.tone;
    try {
      const resp = await fetch(TEST_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ conditions: rows }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        status.textContent = data.error || `Test failed (${resp.status}).`;
        status.dataset.tone = "fail";
        return;
      }
      const passed = data.results.filter((r) => r.passed).length;
      const total = data.results.length;
      status.dataset.tone = data.all_passed ? "ok" : "warn";
      status.textContent = data.all_passed
        ? `All ${total} pass.`
        : `${passed} of ${total} pass.`;
      data.results.forEach((r, idx) => {
        const row = rowsHost.querySelector(`[data-row-idx="${idx}"]`);
        if (!row) return;
        let badge = row.querySelector(".cp-row-test");
        if (!badge) {
          badge = el("span", { class: "cp-row-test" });
          row.appendChild(badge);
        }
        badge.dataset.tone = r.passed ? "ok" : "fail";
        badge.title = `${r.observed}${r.reason ? ` - ${r.reason}` : ""}`;
        badge.textContent = r.passed ? "✓" : "✗";
      });
    } catch (err) {
      status.textContent = `Test failed: ${err.message}`;
      status.dataset.tone = "fail";
    }
  }

  function render() {
    const rows = readRows();
    rowsHost.innerHTML = "";
    if (rows == null) return;
    if (rows.length === 0) {
      rowsHost.appendChild(
        el(
          "p",
          { class: "field-help" },
          "No conditions yet — the schedule fires every time it's due.",
        ),
      );
      return;
    }
    rows.forEach((row, idx) => rowsHost.appendChild(renderRow(row, idx)));
  }

  function renderRow(cond, idx) {
    const kindSelect = el(
      "select",
      {
        on: {
          change: (e) => {
            const fresh = defaultCondition(e.target.value);
            editRows((rows) =>
              rows.map((r, i) => (i === idx ? fresh : r)),
            );
          },
        },
      },
      ...SOURCE_KINDS.map((k) =>
        el(
          "option",
          { value: k.value, ...(k.value === cond.source_kind ? { selected: true } : {}) },
          k.label,
        ),
      ),
    );
    const removeBtn = el(
      "button",
      {
        type: "button",
        class: "ghost",
        "aria-label": "Remove condition",
        on: {
          click: () =>
            editRows((rows) => rows.filter((_, i) => i !== idx)),
        },
      },
      el("i", { class: "ph ph-x", "aria-hidden": "true" }),
    );
    const body = el("div", { class: "cp-row-body" });
    const row = el(
      "div",
      { class: "cp-row", "data-row-idx": String(idx) },
      kindSelect,
      body,
      removeBtn,
    );
    if (cond.source_kind === "ha_entity") renderHaBody(body, cond, idx);
    else if (cond.source_kind === "time_window") renderTimeBody(body, cond, idx);
    else if (cond.source_kind === "sun") renderSunBody(body, cond, idx);
    return row;
  }

  function renderHaBody(body, cond, idx) {
    const entityInput = el("input", {
      type: "text",
      list: datalistId,
      placeholder: "binary_sensor.front_door",
      value: cond.source_id || "",
      on: {
        change: (e) =>
          editRows((rows) =>
            rows.map((r, i) =>
              i === idx ? { ...r, source_id: e.target.value.trim() } : r,
            ),
          ),
      },
    });
    const opSelect = el(
      "select",
      {
        on: {
          change: (e) =>
            editRows((rows) =>
              rows.map((r, i) => {
                if (i !== idx) return r;
                const next = { ...r, operator: e.target.value };
                if (e.target.value === "in" && !Array.isArray(next.value)) {
                  next.value = [];
                } else if (
                  [">", "<", ">=", "<=", "present_within_seconds"].includes(e.target.value)
                ) {
                  next.value = typeof next.value === "number" ? next.value : 0;
                } else if (Array.isArray(next.value)) {
                  next.value = next.value.join(",");
                }
                return next;
              }),
            ),
        },
      },
      ...HA_OPS.map((op) =>
        el(
          "option",
          { value: op.value, ...(op.value === cond.operator ? { selected: true } : {}) },
          op.label,
        ),
      ),
    );
    const numericOp = [">", "<", ">=", "<=", "present_within_seconds"].includes(
      cond.operator,
    );
    const valueInput = el("input", {
      type: numericOp ? "number" : "text",
      placeholder: cond.operator === "in" ? "on, home, ..." : "on",
      value: Array.isArray(cond.value) ? cond.value.join(", ") : String(cond.value ?? ""),
      on: {
        change: (e) =>
          editRows((rows) =>
            rows.map((r, i) => {
              if (i !== idx) return r;
              let value = e.target.value;
              if (r.operator === "in") {
                value = e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean);
              } else if (
                [">", "<", ">=", "<=", "present_within_seconds"].includes(r.operator)
              ) {
                value = Number(e.target.value);
                if (!Number.isFinite(value)) value = 0;
              }
              return { ...r, value };
            }),
          ),
      },
    });
    body.appendChild(entityInput);
    body.appendChild(opSelect);
    body.appendChild(valueInput);
  }

  function renderTimeBody(body, cond, idx) {
    const v = cond.value || {};
    const updateField = (field, value) =>
      editRows((rows) =>
        rows.map((r, i) =>
          i === idx ? { ...r, value: { ...r.value, [field]: value } } : r,
        ),
      );
    const startInput = el("input", {
      type: "time",
      value: v.start_local || "06:00",
      on: { change: (e) => updateField("start_local", e.target.value) },
    });
    const endInput = el("input", {
      type: "time",
      value: v.end_local || "23:00",
      on: { change: (e) => updateField("end_local", e.target.value) },
    });
    // Reuse the form's day-picker pattern (.dow-picker > .dow-chip > input + span).
    const dowPicker = el(
      "div",
      { class: "dow-picker" },
      ...DAY_LABELS.map((label, dayIdx) => {
        const checkbox = el("input", {
          type: "checkbox",
          ...((v.days_of_week || []).includes(dayIdx) ? { checked: true } : {}),
          on: {
            change: () => {
              editRows((rows) =>
                rows.map((r, i) => {
                  if (i !== idx) return r;
                  const days = new Set(r.value?.days_of_week || []);
                  if (days.has(dayIdx)) days.delete(dayIdx);
                  else days.add(dayIdx);
                  return { ...r, value: { ...r.value, days_of_week: [...days].sort() } };
                }),
              );
            },
          },
        });
        return el("label", { class: "dow-chip" }, checkbox, el("span", {}, label));
      }),
    );
    body.appendChild(el("span", { class: "cp-label" }, "between"));
    body.appendChild(startInput);
    body.appendChild(el("span", { class: "cp-label" }, "and"));
    body.appendChild(endInput);
    body.appendChild(dowPicker);
  }

  function renderSunBody(body, cond, idx) {
    const v = cond.value || {};
    const opSelect = el(
      "select",
      {
        on: {
          change: (e) =>
            editRows((rows) =>
              rows.map((r, i) => (i === idx ? { ...r, operator: e.target.value } : r)),
            ),
        },
      },
      ...SUN_OPS.map((op) =>
        el(
          "option",
          { value: op.value, ...(op.value === cond.operator ? { selected: true } : {}) },
          op.label,
        ),
      ),
    );
    const offsetInput = el("input", {
      type: "number",
      step: "5",
      min: "-720",
      max: "720",
      value: v.offset_minutes ?? 0,
      on: {
        change: (e) => {
          let n = Number(e.target.value);
          if (!Number.isFinite(n)) n = 0;
          editRows((rows) =>
            rows.map((r, i) =>
              i === idx ? { ...r, value: { ...r.value, offset_minutes: n } } : r,
            ),
          );
        },
      },
    });
    body.appendChild(opSelect);
    body.appendChild(el("span", { class: "cp-label" }, "by"));
    body.appendChild(offsetInput);
    body.appendChild(el("span", { class: "cp-label" }, "minutes"));
  }

  render();
}

function bootstrap() {
  document
    .querySelectorAll("[data-condition-picker]")
    .forEach((picker) => init(picker));
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootstrap);
} else {
  bootstrap();
}

// Late-attached pickers (rotation editor adds steps dynamically).
const observer = new MutationObserver((mutations) => {
  for (const m of mutations) {
    m.addedNodes.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      if (node.matches?.("[data-condition-picker]")) init(node);
      node.querySelectorAll?.("[data-condition-picker]").forEach(init);
    });
  }
});
observer.observe(document.body, { childList: true, subtree: true });
