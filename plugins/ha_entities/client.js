// ha_entities — six visual directions for a value list (E1–E6).
//
//   e1  Bauhaus Refined   — header chip + alt-tint rows + state icon
//   e2  Bauhaus Geometric — black gaps + state-colour icon blocks
//   e3  Swiss / Intl      — hairlines + tabular figures + state dot
//   e4  Data Meters       — inline meter bars for numeric values
//   e5  Editorial Ledger  — dotted-leader rows, serif numerals
//   e6  Glanceable Cards  — 3×2 bordered cards in state colour

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const STATE_BY_STATUS = {
  on: "ok",
  off: "idle",
  other: "info",
  missing: "idle",
};

const STATE_VAR = {
  heat: "var(--c-danger)",
  alert: "var(--c-danger)",
  cool: "var(--c-info)",
  info: "var(--c-info)",
  ok: "var(--c-ok)",
  warn: "var(--c-warn)",
  idle: "var(--c-text-soft)",
  off: "var(--c-text-soft)",
};
function stateColor(s) { return STATE_VAR[s] || "var(--c-text-soft)"; }
function stateOf(it) { return STATE_BY_STATUS[it.status] || "info"; }

// "23.4 °C" → ["23.4", "°C"]; "Locked" → ["Locked", ""].
function splitValue(label) {
  if (!label) return ["—", ""];
  const m = /^(-?\d+(?:\.\d+)?)\s*(.*)$/.exec(label);
  if (m) return [m[1], m[2].trim()];
  return [label, ""];
}

// Numeric value → meter fraction for E4. Falls back to a per-unit scale;
// non-numeric returns null (no bar drawn).
function meterFrac(label) {
  const [val, unit] = splitValue(label);
  const n = parseFloat(val);
  if (!Number.isFinite(n)) return null;
  const scale = {
    "%": 100, ppm: 1000, lx: 100, "°C": 35, "°F": 100, kg: 10,
    visits: 5, W: 500, kW: 5, V: 240, A: 16,
  }[unit] || 100;
  return Math.max(0.04, Math.min(1, n / scale));
}

// ===========================================================
// E1 — BAUHAUS REFINED
// ===========================================================
function renderE1(data, items) {
  const rows = items.map((it, i) => {
    const s = stateOf(it);
    const [val, unit] = splitValue(it.label);
    return `
      <div class="e1-row${i % 2 ? " e1-row--alt" : ""}" style="--chip:${stateColor(s)}">
        <div class="e1-block" aria-hidden="true">
          <i class="ph-bold ph-${escapeHtml(it.icon)}"></i>
        </div>
        <div class="e1-name">${escapeHtml(it.name.toUpperCase())}</div>
        <div class="e1-value">${escapeHtml(val)}${unit ? `<span class="e1-unit"> ${escapeHtml(unit)}</span>` : ""}</div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-e1">
      <header class="e1-header">
        <span class="e1-mark" aria-hidden="true"></span>
        <span class="e1-title">${escapeHtml((data.title || "ENTITIES").toUpperCase())}</span>
        <span class="e1-meta">${items.length} ITEMS</span>
      </header>
      <section class="e1-list">${rows}</section>
    </div>
  `;
}

// ===========================================================
// E2 — BAUHAUS GEOMETRIC
// ===========================================================
function renderE2(data, items) {
  const rows = items.map((it) => {
    const s = stateOf(it);
    return `
      <div class="e2-row" style="--chip:${stateColor(s)}">
        <span class="e2-block" aria-hidden="true">
          <i class="ph-bold ph-${escapeHtml(it.icon)}"></i>
        </span>
        <span class="e2-name">${escapeHtml(it.name.toUpperCase())}</span>
        <span class="e2-value">${escapeHtml(it.label || "—")}</span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-e2">
      <header class="e2-header">${escapeHtml((data.title || "ENTITIES").toUpperCase())}</header>
      <section class="e2-list">${rows}</section>
    </div>
  `;
}

// ===========================================================
// E3 — SWISS / INTERNATIONAL
// ===========================================================
function renderE3(data, items) {
  const rows = items.map((it) => {
    const s = stateOf(it);
    const [val, unit] = splitValue(it.label);
    return `
      <div class="e3-row">
        <span class="e3-row-left">
          <span class="e3-dot" style="background:${stateColor(s)}"></span>
          <span class="e3-name">${escapeHtml(it.name)}</span>
        </span>
        <span class="e3-value">${escapeHtml(val)}<span class="e3-unit">${unit ? " " + escapeHtml(unit) : ""}</span></span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-e3">
      <div class="e3-eyebrow">
        <span>${escapeHtml(data.title || "Entities")}</span><span>${items.length}</span>
      </div>
      <div class="e3-rule"></div>
      <section class="e3-list">${rows}</section>
    </div>
  `;
}

// ===========================================================
// E4 — DATA METERS
// ===========================================================
function renderE4(data, items) {
  const rows = items.map((it) => {
    const s = stateOf(it);
    const f = meterFrac(it.label);
    const [val, unit] = splitValue(it.label);
    return `
      <div class="e4-row">
        <i class="ph ph-${escapeHtml(it.icon)} e4-icon" style="color:${stateColor(s)}"></i>
        <span class="e4-name">${escapeHtml(it.name.toUpperCase())}</span>
        <div class="e4-meter">
          ${f != null ? `<div class="e4-meter-fill" style="width:${(f * 100).toFixed(1)}%; background:${stateColor(s)}"></div>` : ""}
        </div>
        <span class="e4-value">${escapeHtml(val)}<span class="e4-unit">${unit ? " " + escapeHtml(unit) : ""}</span></span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-e4">
      <header class="e4-header">${escapeHtml((data.title || "ENTITIES").toUpperCase())}</header>
      <section class="e4-list">${rows}</section>
    </div>
  `;
}

// ===========================================================
// E5 — EDITORIAL LEDGER
// ===========================================================
function renderE5(data, items) {
  const rows = items.map((it) => {
    const s = stateOf(it);
    const [val, unit] = splitValue(it.label);
    return `
      <div class="e5-row">
        <span class="e5-mark" style="background:${stateColor(s)}"></span>
        <span class="e5-name">${escapeHtml(it.name)}</span>
        <span class="e5-leader" aria-hidden="true"></span>
        <span class="e5-value">${escapeHtml(val)}<span class="e5-unit">${unit ? " " + escapeHtml(unit) : ""}</span></span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-e5">
      <header class="e5-header">
        <span class="e5-title">${escapeHtml(data.title || "Entities")}</span>
        <span class="e5-meta">${items.length} TRACKED</span>
      </header>
      <div class="e5-rules"><div class="e5-rule e5-rule--thick"></div><div class="e5-rule e5-rule--thin"></div></div>
      <section class="e5-list">${rows}</section>
    </div>
  `;
}

// ===========================================================
// E6 — GLANCEABLE CARDS
// ===========================================================
function renderE6(data, items) {
  const cards = items.map((it) => {
    const s = stateOf(it);
    const [val, unit] = splitValue(it.label);
    return `
      <article class="e6-card" style="--chip:${stateColor(s)}">
        <i class="ph-bold ph-${escapeHtml(it.icon)} e6-icon" aria-hidden="true"></i>
        <div class="e6-card-bottom">
          <div class="e6-value">${escapeHtml(val)}${unit ? `<span class="e6-unit"> ${escapeHtml(unit)}</span>` : ""}</div>
          <div class="e6-name">${escapeHtml(it.name.toUpperCase())}</div>
        </div>
      </article>
    `;
  }).join("");
  return `<div class="variant variant-e6">${cards}</div>`;
}

const VARIANTS = {
  e1: renderE1, e2: renderE2, e3: renderE3,
  e4: renderE4, e5: renderE5, e6: renderE6,
};

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/plugins/ha_entities/client.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">`;

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="root error">
        <i class="ph ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  if (data.empty || !(data.items && data.items.length)) {
    shadow.innerHTML = `${HEAD}
      <div class="root">
        <div class="he-stub">
          <i class="ph-duotone ph-squares-four" aria-hidden="true"></i>
          <div class="he-stub-primary">Pick entities</div>
          <div class="he-stub-secondary">List entity ids in the cell options.</div>
        </div>
      </div>`;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "e1";
  const renderer = VARIANTS[variant] || renderE1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, data.items)}
    </div>`;
}
