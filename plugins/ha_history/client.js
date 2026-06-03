// ha_history — six visual directions for a sensor + sparkline (H1–H6).
//
//   h1  Bauhaus Refined   — chip + accent rule + big numeral + sparkline
//   h2  Bauhaus Geometric — solid accent header, paper chart, yellow footer
//   h3  Swiss / Intl      — hairlines, thin numerals, ink sparkline
//   h4  Data Chart        — gridlines, min/max markers, avg readout
//   h5  Editorial         — serif numerals, double rule, soft fill chart
//   h6  Glanceable        — hero numeral + 70px chart + ↑/↓ legends
//
// When multiple entities are configured the variant renders the first
// series in full. Stack additional cells to chart more series.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Single trend → categorical accent. Up vs down vs flat is decorative
// (a temperature going up isn't a hazard, a battery going down isn't
// "good") so reach for the categorical --c-data-* ramp, not the
// --c-ok/warn/danger status tokens. Themes where danger is loud red
// would otherwise paint a rising weather sensor as an alarm.
function trendColour(trend) {
  if (trend === "up") return "var(--c-data-3)";
  if (trend === "down") return "var(--c-data-2)";
  return "var(--c-text-soft)";
}

// Build the line + area paths for the SVG sparkline. The view-box matches
// the spec (640 × 150) — preserveAspectRatio=none lets it stretch fluidly
// while vector-effect=non-scaling-stroke keeps the stroke crisp.
const W = 640, H = 150;
function buildSpark(values, pad = 8) {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = (hi - lo) || 1;
  const n = values.length;
  const X = (i) => n > 1 ? (i / (n - 1)) * W : 0;
  const Y = (v) => H - pad - ((v - lo) / span) * (H - pad * 2);
  let line = "";
  for (let i = 0; i < n; i++) {
    const x = X(i).toFixed(1);
    const y = Y(values[i]).toFixed(1);
    line += (i ? "L" : "M") + x + " " + y + " ";
  }
  let area = `M0 ${H} L0 ${Y(values[0]).toFixed(1)} `;
  for (let i = 0; i < n; i++) {
    area += `L${X(i).toFixed(1)} ${Y(values[i]).toFixed(1)} `;
  }
  area += `L${W} ${H} Z`;
  return { line: line.trim(), area, X, Y };
}

function trendArrow(dir, color, size = 30) {
  if (dir === "down") {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="8" x2="19" y2="17"></line><polyline points="19 11 19 17 13 17"></polyline></svg>`;
  }
  if (dir === "up") {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="17" x2="19" y2="8"></line><polyline points="13 8 19 8 19 14"></polyline></svg>`;
  }
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2.6" stroke-linecap="round"><line x1="5" y1="12" x2="19" y2="12"></line></svg>`;
}

function sparkSvg({ line, area }, { color, fill, sw = 2.5 }) {
  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="hh-svg" aria-hidden="true">
      ${fill ? `<path d="${area}" fill="${fill}" stroke="none"/>` : ""}
      <path d="${line}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    </svg>
  `;
}

function firstSeries(items) {
  return (items || []).find((it) => Array.isArray(it.values) && it.values.length) || null;
}

// ===========================================================
// H1 — BAUHAUS REFINED
// ===========================================================
function renderH1(data, it) {
  const acc = trendColour(it.trend);
  const spark = buildSpark(it.values);
  return `
    <div class="variant variant-h1">
      <header class="h1-header">
        <span class="h1-mark" aria-hidden="true" style="background:${acc}"></span>
        <span class="h1-title">${escapeHtml((data.title || it.name).toUpperCase())}</span>
        <span class="h1-window">${data.hours}H</span>
      </header>
      <div class="h1-cur">
        <span class="h1-num">${escapeHtml(it.current)}</span>
        <span class="h1-unit">${escapeHtml(it.unit || "")}</span>
        <span class="h1-trend">${trendArrow(it.trend, acc, 30)}</span>
      </div>
      <div class="h1-chart">
        ${sparkSvg(spark, { color: acc, fill: `color-mix(in oklab, ${acc} 22%, transparent)` })}
      </div>
      <div class="h1-axis">
        <span>min ${escapeHtml(it.min || "—")}</span>
        <span>max ${escapeHtml(it.max || "—")}</span>
      </div>
    </div>
  `;
}

// ===========================================================
// H2 — BAUHAUS GEOMETRIC
// ===========================================================
function renderH2(data, it) {
  const acc = trendColour(it.trend);
  const spark = buildSpark(it.values);
  return `
    <div class="variant variant-h2">
      <header class="h2-header" style="background:${acc}">
        <div class="h2-cur">
          <span class="h2-num">${escapeHtml(it.current)}</span>
          <span class="h2-unit">${escapeHtml(it.unit || "")}</span>
        </div>
        <div class="h2-meta">
          <div class="h2-meta-name">${escapeHtml((data.title || it.name).toUpperCase())} · ${data.hours}H</div>
          ${trendArrow(it.trend, "var(--c-bg)", 26)}
        </div>
      </header>
      <div class="h2-chart">
        ${sparkSvg(spark, { color: acc, fill: `color-mix(in oklab, ${acc} 22%, transparent)`, sw: 3 })}
      </div>
      <footer class="h2-footer">
        <span>MIN ${escapeHtml(it.min || "—")}</span>
        <span>MAX ${escapeHtml(it.max || "—")}</span>
      </footer>
    </div>
  `;
}

// ===========================================================
// H3 — SWISS
// ===========================================================
function renderH3(data, it) {
  const acc = trendColour(it.trend);
  const spark = buildSpark(it.values);
  return `
    <div class="variant variant-h3">
      <div class="h3-eyebrow">
        <span>${escapeHtml(data.title || it.name)}</span>
        <span>Last ${data.hours}H</span>
      </div>
      <div class="h3-rule"></div>
      <div class="h3-cur">
        <span class="h3-num">${escapeHtml(it.current)}</span>
        <span class="h3-unit">${escapeHtml(it.unit || "")}</span>
        <span class="h3-trend" style="color:${acc}">
          ${trendArrow(it.trend, acc, 18)}
          <span>${it.trend === "down" ? "falling" : it.trend === "up" ? "rising" : "stable"}</span>
        </span>
      </div>
      <div class="h3-chart">${sparkSvg(spark, { color: "var(--c-text)", sw: 1.5 })}</div>
      <div class="h3-axis">
        <span>${escapeHtml(it.min || "—")}</span>
        <span>${escapeHtml(it.max || "—")} ${escapeHtml(it.unit || "")}</span>
      </div>
    </div>
  `;
}

// ===========================================================
// H4 — DATA CHART
// ===========================================================
function renderH4(data, it) {
  const acc = trendColour(it.trend);
  const spark = buildSpark(it.values);
  const avg = it.values.length
    ? (it.values.reduce((a, b) => a + b, 0) / it.values.length).toFixed(2)
    : "—";
  return `
    <div class="variant variant-h4">
      <header class="h4-header">
        <span class="h4-title">${escapeHtml((data.title || it.name).toUpperCase())}</span>
        <span class="h4-readout"><b>${escapeHtml(it.current)}</b> ${escapeHtml(it.unit || "")} · avg ${avg}</span>
      </header>
      <div class="h4-chart">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="hh-svg" aria-hidden="true">
          <line x1="0" y1="${H * 0.25}" x2="${W}" y2="${H * 0.25}" stroke="color-mix(in srgb, var(--c-text) 12%, transparent)" stroke-width="1" vector-effect="non-scaling-stroke"/>
          <line x1="0" y1="${H * 0.5}" x2="${W}" y2="${H * 0.5}" stroke="color-mix(in srgb, var(--c-text) 12%, transparent)" stroke-width="1" vector-effect="non-scaling-stroke"/>
          <line x1="0" y1="${H * 0.75}" x2="${W}" y2="${H * 0.75}" stroke="color-mix(in srgb, var(--c-text) 12%, transparent)" stroke-width="1" vector-effect="non-scaling-stroke"/>
          <path d="${spark.area}" fill="color-mix(in oklab, ${acc} 22%, transparent)" stroke="none"/>
          <path d="${spark.line}" fill="none" stroke="${acc}" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
        </svg>
        <span class="h4-max">${escapeHtml(it.max || "—")}</span>
        <span class="h4-min">${escapeHtml(it.min || "—")}</span>
      </div>
      <div class="h4-axis">
        <span>-${data.hours}h</span>
        <span>-${Math.round(data.hours / 2)}h</span>
        <span>now</span>
      </div>
    </div>
  `;
}

// ===========================================================
// H5 — EDITORIAL
// ===========================================================
function renderH5(data, it) {
  const spark = buildSpark(it.values);
  return `
    <div class="variant variant-h5">
      <header class="h5-header">
        <span class="h5-title">${escapeHtml(data.title || it.name)}</span>
        <span class="h5-meta">PAST ${data.hours}H</span>
      </header>
      <div class="h5-rules"><div class="h5-rule h5-rule--thick"></div><div class="h5-rule h5-rule--thin"></div></div>
      <div class="h5-cur">
        <span class="h5-num">${escapeHtml(it.current)}</span>
        <span class="h5-unit">${escapeHtml(it.unit || "")}</span>
      </div>
      <div class="h5-chart">
        ${sparkSvg(spark, { color: "var(--c-text)", fill: "color-mix(in srgb, var(--c-text) 8%, transparent)", sw: 1.5 })}
      </div>
      <footer class="h5-footer">ranged ${escapeHtml(it.min || "—")}–${escapeHtml(it.max || "—")} ${escapeHtml(it.unit || "")} over the window</footer>
    </div>
  `;
}

// ===========================================================
// H6 — GLANCEABLE
// ===========================================================
function renderH6(data, it) {
  const acc = trendColour(it.trend);
  const spark = buildSpark(it.values);
  return `
    <div class="variant variant-h6">
      <div class="h6-eyebrow">${escapeHtml((data.title || it.name).toUpperCase())} · NOW</div>
      <div class="h6-cur">
        <span class="h6-num">${escapeHtml(it.current)}</span>
        <span class="h6-unit">${escapeHtml(it.unit || "")}</span>
        <span class="h6-trend">${trendArrow(it.trend, acc, 28)}</span>
      </div>
      <div class="h6-chart">
        ${sparkSvg(spark, { color: acc, fill: `color-mix(in oklab, ${acc} 22%, transparent)`, sw: 3 })}
      </div>
      <div class="h6-axis">
        <span>↓ ${escapeHtml(it.min || "—")}</span>
        <span>↑ ${escapeHtml(it.max || "—")}</span>
      </div>
    </div>
  `;
}

const VARIANTS = {
  h1: renderH1, h2: renderH2, h3: renderH3,
  h4: renderH4, h5: renderH5, h6: renderH6,
};

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/plugins/ha_history/client.css">
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
        <div class="hh-stub">
          <i class="ph-duotone ph-chart-line" aria-hidden="true"></i>
          <div class="hh-stub-primary">Pick entities</div>
          <div class="hh-stub-secondary">List numeric Home Assistant entity ids to chart.</div>
        </div>
      </div>`;
    return;
  }
  const it = firstSeries(data.items);
  if (!it) {
    shadow.innerHTML = `${HEAD}
      <div class="root">
        <div class="hh-stub">
          <i class="ph-duotone ph-chart-line" aria-hidden="true"></i>
          <div class="hh-stub-primary">No data in window</div>
          <div class="hh-stub-secondary">Try a longer window or check the entity in HA.</div>
        </div>
      </div>`;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "h1";
  const renderer = VARIANTS[variant] || renderH1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, it)}
    </div>`;
}
