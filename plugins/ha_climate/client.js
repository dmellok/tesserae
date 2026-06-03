// ha_climate — six visual directions for thermostat tiles (C1–C6).
//
//   c1  Bauhaus Refined   — header chip + accent rule + tinted tiles
//   c2  Bauhaus Geometric — solid state-colour fills + heavy black gaps
//   c3  Swiss / Intl      — hairlines, tabular figures, tiny state dot
//   c4  Gauge Dial        — 270° SVG dial with setpoint tick
//   c5  Editorial         — serif numerals, double rule, italic chip
//   c6  Glanceable        — bold cards, active = filled in state colour
//
// State→colour comes from the running action / mode (heating, cooling,
// idle, off). The 6-colour reference palette is mapped to the theme's
// --c-* semantic tokens so every Tesserae theme restyles cleanly.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Derive a state token from the climate item's action + mode.
function climateState(it) {
  if (it.unavailable) return "off";
  const a = (it.action || "").toLowerCase();
  const m = (it.mode || "").toLowerCase();
  if (a === "heating" || a === "heat" || m === "heat") return "heat";
  if (a === "cooling" || a === "cool" || m === "cool") return "cool";
  if (m === "off" || a === "off") return "off";
  if (a === "idle") return "idle";
  return "info";
}

const STATE_VAR = {
  heat: "var(--c-danger)",
  alert: "var(--c-danger)",
  high: "var(--c-danger)",
  cool: "var(--c-info)",
  info: "var(--c-info)",
  on: "var(--c-ok)",
  ok: "var(--c-ok)",
  warn: "var(--c-warn)",
  off: "var(--c-text-soft)",
  idle: "var(--c-text-soft)",
};
function stateColor(s) { return STATE_VAR[s] || "var(--c-text-soft)"; }
function stateTint(s) {
  if (s === "off" || s === "idle") return "var(--c-bg)";
  return `color-mix(in oklab, ${stateColor(s)} 18%, transparent)`;
}

function targetText(it) {
  if (it.target) return `Set ${it.target}°`;
  if (it.target_low && it.target_high) return `${it.target_low}°–${it.target_high}°`;
  return "";
}

// Just the value portion — used by c2..c6 which want to compose
// their own "SET …" / "TARGET …" prefix. Returns "21°" for a
// single setpoint or "18°–22°" for a range. Falls back to ``—`` so
// the row doesn't render as a bare "SET °".
function targetValue(it) {
  if (it.target) return `${it.target}°`;
  if (it.target_low && it.target_high) return `${it.target_low}°–${it.target_high}°`;
  return "—";
}

function modeChip(it) {
  return (it.mode_label || it.action || "").toUpperCase();
}

// ===========================================================
// C1 — BAUHAUS REFINED
// ===========================================================
function renderC1(data, items) {
  const tiles = items.map((it) => {
    const s = climateState(it);
    const cur = it.unavailable ? "—" : (it.current || "—");
    return `
      <article class="c1-tile" style="--chip:${stateColor(s)}; background:${stateTint(s)}">
        <div class="c1-tile-head">
          <i class="ph-bold ph-${escapeHtml(it.icon)}" aria-hidden="true" style="color:${stateColor(s)}"></i>
          <span class="c1-tile-name">${escapeHtml(it.name)}</span>
        </div>
        <div class="c1-tile-cur">${escapeHtml(cur)}<span class="c1-deg">°</span></div>
        <div class="c1-tile-meta">
          <span class="c1-target">${escapeHtml(targetText(it))}</span>
          <span class="c1-chip" style="background:${stateColor(s)}">${escapeHtml(modeChip(it))}</span>
        </div>
      </article>
    `;
  }).join("");
  return `
    <div class="variant variant-c1">
      <header class="c1-header">
        <span class="c1-mark" aria-hidden="true"></span>
        <span class="c1-title">${escapeHtml((data.title || "CLIMATE").toUpperCase())}</span>
        <i class="ph ph-thermometer-simple c1-header-icon" aria-hidden="true"></i>
      </header>
      <section class="c1-grid">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// C2 — BAUHAUS GEOMETRIC (De Stijl)
// ===========================================================
function renderC2(data, items) {
  const tiles = items.map((it) => {
    const s = climateState(it);
    const active = s !== "off" && s !== "idle";
    const cur = it.unavailable ? "—" : (it.current || "—");
    return `
      <article class="c2-tile${active ? " c2-tile--active" : ""}" style="--chip:${stateColor(s)}">
        <div class="c2-tile-head">
          <span class="c2-tile-name">${escapeHtml(it.name)}</span>
          <i class="ph-bold ph-${escapeHtml(it.icon)} c2-tile-icon" aria-hidden="true"></i>
        </div>
        <div class="c2-tile-cur">${escapeHtml(cur)}<span class="c2-deg">°</span></div>
        <div class="c2-tile-foot">
          <span>SET ${escapeHtml(targetValue(it))}</span>
          <span class="c2-chip">${escapeHtml(modeChip(it))}</span>
        </div>
      </article>
    `;
  }).join("");
  return `<div class="variant variant-c2">${tiles}</div>`;
}

// ===========================================================
// C3 — SWISS / INTERNATIONAL
// ===========================================================
function renderC3(data, items) {
  const cols = items.map((it, i) => {
    const s = climateState(it);
    const cur = it.unavailable ? "—" : (it.current || "—");
    return `
      <div class="c3-col${i ? " c3-col--sep" : ""}">
        <div class="c3-col-name">${escapeHtml(it.name.toUpperCase())}</div>
        <div class="c3-col-cur">
          <span class="c3-cur">${escapeHtml(cur)}</span>
          <span class="c3-unit">°</span>
        </div>
        <div class="c3-col-foot">
          <span class="c3-dot" style="background:${stateColor(s)}"></span>
          <span>SET ${escapeHtml(targetValue(it))} · ${escapeHtml(modeChip(it))}</span>
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-c3">
      <div class="c3-eyebrow">
        <span>Climate</span><span>${items.length} ZONE${items.length === 1 ? "" : "S"}</span>
      </div>
      <div class="c3-rule"></div>
      <div class="c3-grid">${cols}</div>
    </div>
  `;
}

// ===========================================================
// C4 — GAUGE DIAL
// ===========================================================
function gaugeSvg(cur, set, color) {
  const lo = 10, hi = 30, R = 58, CX = 75, CY = 72, START = 135, SWEEP = 270;
  const polar = (deg) => [
    CX + R * Math.cos(deg * Math.PI / 180),
    CY + R * Math.sin(deg * Math.PI / 180),
  ];
  const arc = (a, b) => {
    const [x1, y1] = polar(a);
    const [x2, y2] = polar(b);
    return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${R} ${R} 0 ${b - a > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
  };
  const curN = parseFloat(cur);
  const f = Number.isFinite(curN)
    ? Math.max(0, Math.min(1, (curN - lo) / (hi - lo)))
    : 0;
  const setN = parseFloat(set);
  const setF = Number.isFinite(setN)
    ? Math.max(0, Math.min(1, (setN - lo) / (hi - lo)))
    : 0.5;
  const [sx, sy] = polar(START + setF * SWEEP);
  const valText = Number.isFinite(curN) ? cur : "—";
  return `
    <svg viewBox="0 0 150 150" class="c4-gauge" preserveAspectRatio="xMidYMid meet">
      <path d="${arc(START, START + SWEEP)}" fill="none" stroke="color-mix(in oklab, var(--c-text) 14%, transparent)" stroke-width="10" stroke-linecap="round" />
      <path d="${arc(START, START + f * SWEEP)}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round" />
      <line x1="${CX}" y1="${CY}" x2="${sx.toFixed(2)}" y2="${sy.toFixed(2)}" stroke="var(--c-text)" stroke-width="2.5" />
      <circle cx="${CX}" cy="${CY}" r="4" fill="var(--c-text)" />
      <text x="${CX}" y="${CY + 30}" text-anchor="middle" class="c4-gauge-text">${escapeHtml(valText)}°</text>
    </svg>
  `;
}
function renderC4(data, items) {
  const tiles = items.map((it, i) => {
    const s = climateState(it);
    const colour = stateColor(s);
    return `
      <div class="c4-tile${i ? " c4-tile--sep" : ""}">
        ${gaugeSvg(it.current, it.target, colour)}
        <div class="c4-tile-meta">
          <div class="c4-tile-name">
            <i class="ph ph-${escapeHtml(it.icon)}" aria-hidden="true" style="color:${colour}"></i>
            <span>${escapeHtml(it.name)}</span>
          </div>
          <div class="c4-tile-set">SET ${escapeHtml(targetValue(it))} · ${escapeHtml(modeChip(it))}</div>
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-c4">
      <header class="c4-header">CLIMATE</header>
      <section class="c4-grid">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// C5 — EDITORIAL
// ===========================================================
function renderC5(data, items) {
  const cols = items.map((it, i) => {
    const s = climateState(it);
    const cur = it.unavailable ? "—" : (it.current || "—");
    return `
      <div class="c5-col${i ? " c5-col--sep" : ""}">
        <div class="c5-col-name">${escapeHtml(it.name.toUpperCase())}</div>
        <div class="c5-col-cur">${escapeHtml(cur)}<span class="c5-deg">°</span></div>
        <div class="c5-col-foot">
          set to ${escapeHtml(targetValue(it))}
          · <span class="c5-mode" style="color:${stateColor(s)}">${escapeHtml(modeChip(it))}</span>
        </div>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-c5">
      <header class="c5-header">
        <span class="c5-title">${escapeHtml(data.title || "Climate")}</span>
        <span class="c5-meta">${items.length === 1 ? "ONE ZONE" : items.length + " ZONES"}</span>
      </header>
      <div class="c5-rules"><div class="c5-rule c5-rule--thick"></div><div class="c5-rule c5-rule--thin"></div></div>
      <section class="c5-grid">${cols}</section>
    </div>
  `;
}

// ===========================================================
// C6 — GLANCEABLE
// ===========================================================
function renderC6(data, items) {
  const tiles = items.map((it) => {
    const s = climateState(it);
    const active = s !== "off" && s !== "idle";
    const cur = it.unavailable ? "—" : (it.current || "—");
    return `
      <article class="c6-tile${active ? " c6-tile--active" : ""}" style="--chip:${stateColor(s)}">
        <div class="c6-tile-head">
          <i class="ph-bold ph-${escapeHtml(it.icon)}" aria-hidden="true"></i>
          <span class="c6-mode">${escapeHtml(modeChip(it))}</span>
        </div>
        <div class="c6-tile-cur">${escapeHtml(cur)}<span class="c6-deg">°</span></div>
        <div class="c6-tile-foot">
          <div class="c6-tile-name">${escapeHtml(it.name.toUpperCase())}</div>
          <div class="c6-tile-set">TARGET ${escapeHtml(targetValue(it))}</div>
        </div>
      </article>
    `;
  }).join("");
  return `<div class="variant variant-c6">${tiles}</div>`;
}

const VARIANTS = {
  c1: renderC1, c2: renderC2, c3: renderC3,
  c4: renderC4, c5: renderC5, c6: renderC6,
};

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/plugins/ha_climate/client.css">`;

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
        <div class="hc-stub">
          <i class="ph-duotone ph-thermometer-simple" aria-hidden="true"></i>
          <div class="hc-stub-primary">Pick climate entities</div>
          <div class="hc-stub-secondary">List thermostat entity ids in the cell options.</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "c1";
  const renderer = VARIANTS[variant] || renderC1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, data.items)}
    </div>`;
}
