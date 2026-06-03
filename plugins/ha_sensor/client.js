// ha_sensor — six visual directions for sensor tile grids (S1–S6).
//
//   s1  Bauhaus Refined   — chip + accent rule + 3×2 tinted tiles
//   s2  Bauhaus Geometric — solid state-colour fills, heavy black grid
//   s3  Swiss / Intl      — hairlines + tabular figures + tiny dot
//   s4  Ring Gauges       — per-unit ring + centered icon
//   s5  Editorial         — serif numerals, double rule, hairline grid
//   s6  Glanceable        — bordered cards, alert fills solid

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const STATE_VAR = {
  alert: "var(--c-danger)",
  heat: "var(--c-danger)",
  cool: "var(--c-info)",
  info: "var(--c-info)",
  ok: "var(--c-ok)",
  warn: "var(--c-warn)",
  idle: "var(--c-text-soft)",
  off: "var(--c-text-soft)",
};
function stateColor(s) { return STATE_VAR[s] || "var(--c-info)"; }

// Server doesn't emit a state — derive from value + unit so the colour
// system carries some signal. Battery low / CO₂ high / unavailable etc
// nudge to warn / alert; everything else is informational.
function inferState(it) {
  if (it.unavailable) return "idle";
  const n = parseFloat(it.value);
  const u = (it.unit || "").toLowerCase();
  const name = (it.name || "").toLowerCase();
  if (Number.isFinite(n)) {
    if (u === "ppm" && n > 1000) return "alert";
    if (u === "ppm" && n > 700) return "warn";
    if (u === "%" && name.includes("battery") && n < 20) return "alert";
    if (u === "%" && name.includes("battery") && n < 40) return "warn";
    if (u === "lx" && n < 50 && name.includes("illumin")) return "alert";
  } else {
    const v = String(it.value || "").toLowerCase();
    if (v === "on" || v === "open" || v === "unlocked" || v === "detected") return "ok";
    if (v === "off" || v === "closed" || v === "locked" || v === "dark") return "idle";
  }
  return "info";
}

const SCALE = {
  lx: 100, "°C": 35, "°F": 100, "%": 100, ppm: 1000,
  kg: 10, W: 500, kW: 5, V: 240, A: 16, visits: 5,
};
function fracOf(it) {
  const n = parseFloat(it.value);
  if (!Number.isFinite(n)) return null;
  const s = SCALE[it.unit] || 100;
  return Math.max(0.04, Math.min(1, n / s));
}

// ===========================================================
// S1 — BAUHAUS REFINED
// ===========================================================
function renderS1(data, items) {
  const tiles = items.map((it, i) => {
    const s = inferState(it);
    return `
      <div class="s1-tile${i % 3 < 2 ? " s1-tile--rsep" : ""}${i < items.length - 3 ? " s1-tile--bsep" : ""}"
           style="background:color-mix(in oklab, ${stateColor(s)} 16%, transparent)">
        <i class="ph-bold ph-${escapeHtml(it.icon)} s1-icon" style="color:${stateColor(s)}"></i>
        <div class="s1-tile-bottom">
          <div class="s1-value">${escapeHtml(it.value || "—")}<span class="s1-unit">${it.unit ? " " + escapeHtml(it.unit) : ""}</span></div>
          <div class="s1-name">${escapeHtml(it.name.toUpperCase())}</div>
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-s1">
      <header class="s1-header">
        <span class="s1-mark" aria-hidden="true"></span>
        <span class="s1-title">${escapeHtml((data.title || "SENSORS").toUpperCase())}</span>
        <i class="ph ph-gauge s1-header-icon" aria-hidden="true"></i>
      </header>
      <section class="s1-grid">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// S2 — BAUHAUS GEOMETRIC
// ===========================================================
function renderS2(data, items) {
  const tiles = items.map((it) => {
    const s = inferState(it);
    const solid = s !== "idle" && s !== "info";
    return `
      <div class="s2-tile${solid ? " s2-tile--solid" : ""}" style="--chip:${stateColor(s)}">
        <div class="s2-tile-head">
          <span class="s2-name">${escapeHtml(it.name.toUpperCase())}</span>
          <i class="ph-bold ph-${escapeHtml(it.icon)} s2-icon"></i>
        </div>
        <div class="s2-value">${escapeHtml(it.value || "—")}<span class="s2-unit">${it.unit ? escapeHtml(it.unit) : ""}</span></div>
      </div>
    `;
  }).join("");
  return `<div class="variant variant-s2">${tiles}</div>`;
}

// ===========================================================
// S3 — SWISS / INTERNATIONAL
// ===========================================================
function renderS3(data, items) {
  const tiles = items.map((it, i) => {
    const s = inferState(it);
    return `
      <div class="s3-tile${i % 3 < 2 ? " s3-tile--rsep" : ""}">
        <div class="s3-tile-head">
          <span class="s3-dot" style="background:${stateColor(s)}"></span>
          <span>${escapeHtml(it.name.toUpperCase())}</span>
        </div>
        <div class="s3-value">
          <span class="s3-num">${escapeHtml(it.value || "—")}</span>
          <span class="s3-unit">${it.unit ? escapeHtml(it.unit) : ""}</span>
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-s3">
      <div class="s3-eyebrow">
        <span>${escapeHtml(data.title || "Sensors")}</span><span>${items.length}</span>
      </div>
      <div class="s3-rule"></div>
      <section class="s3-grid">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// S4 — RING GAUGES
// ===========================================================
function ringSvg(f, color, size = 56) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const cx = size / 2;
  const fill = (f * c).toFixed(2);
  return `
    <svg viewBox="0 0 ${size} ${size}" class="s4-ring">
      <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="color-mix(in srgb, var(--c-text) 14%, transparent)" stroke-width="6"/>
      <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${fill} ${c.toFixed(2)}" transform="rotate(-90 ${cx} ${cx})"/>
    </svg>
  `;
}
function renderS4(data, items) {
  const tiles = items.map((it) => {
    const s = inferState(it);
    const colour = stateColor(s);
    const f = fracOf(it);
    const ring = f != null
      ? ringSvg(f, colour, 56)
      : `<span class="s4-ring-empty" style="border-color:${colour}"></span>`;
    return `
      <div class="s4-tile">
        <div class="s4-ring-wrap">
          ${ring}
          <i class="ph ph-${escapeHtml(it.icon)} s4-ring-icon" style="color:${colour}"></i>
        </div>
        <div class="s4-meta">
          <div class="s4-value">${escapeHtml(it.value || "—")}<span class="s4-unit">${it.unit ? " " + escapeHtml(it.unit) : ""}</span></div>
          <div class="s4-name">${escapeHtml(it.name.toUpperCase())}</div>
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-s4">
      <header class="s4-header">${escapeHtml((data.title || "SENSORS").toUpperCase())}</header>
      <section class="s4-grid">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// S5 — EDITORIAL
// ===========================================================
function renderS5(data, items) {
  const tiles = items.map((it, i) => {
    const rsep = i % 3 < 2;
    const bsep = i < items.length - 3;
    return `
      <div class="s5-tile${rsep ? " s5-tile--rsep" : ""}${bsep ? " s5-tile--bsep" : ""}">
        <div class="s5-name">${escapeHtml(it.name.toUpperCase())}</div>
        <div class="s5-value">
          <span class="s5-num">${escapeHtml(it.value || "—")}</span>
          <span class="s5-unit">${it.unit ? escapeHtml(it.unit) : ""}</span>
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-s5">
      <header class="s5-header">
        <span class="s5-title">${escapeHtml(data.title || "Sensors")}</span>
        <span class="s5-meta">${items.length} TRACKED</span>
      </header>
      <div class="s5-rules"><div class="s5-rule s5-rule--thick"></div><div class="s5-rule s5-rule--thin"></div></div>
      <section class="s5-grid">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// S6 — GLANCEABLE
// ===========================================================
function renderS6(data, items) {
  const tiles = items.map((it) => {
    const s = inferState(it);
    const hot = s === "alert";
    return `
      <article class="s6-tile${hot ? " s6-tile--hot" : ""}" style="--chip:${stateColor(s)}">
        <i class="ph-bold ph-${escapeHtml(it.icon)} s6-icon" aria-hidden="true"></i>
        <div class="s6-bottom">
          <div class="s6-value">${escapeHtml(it.value || "—")}<span class="s6-unit">${it.unit ? escapeHtml(it.unit) : ""}</span></div>
          <div class="s6-name">${escapeHtml(it.name.toUpperCase())}</div>
        </div>
      </article>
    `;
  }).join("");
  return `<div class="variant variant-s6">${tiles}</div>`;
}

const VARIANTS = {
  s1: renderS1, s2: renderS2, s3: renderS3,
  s4: renderS4, s5: renderS5, s6: renderS6,
};

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/plugins/ha_sensor/client.css">
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
        <div class="hs-stub">
          <i class="ph-duotone ph-gauge" aria-hidden="true"></i>
          <div class="hs-stub-primary">Pick sensors</div>
          <div class="hs-stub-secondary">List Home Assistant entity ids in the cell options.</div>
        </div>
      </div>`;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "s1";
  const renderer = VARIANTS[variant] || renderS1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, data.items)}
    </div>`;
}
