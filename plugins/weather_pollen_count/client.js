// weather_pollen_count — Bauhaus pollen card.
//
// Layout matches weather_air_quality so the two pair nicely on a panel:
//   1. Inverted header bar (mark + POLLEN · place + time)
//   2. Hero: big level word + count (grass is the headline) | flower icon
//   3. Three-up species chips (Tree / Grass / Weed)

const BANDS = [
  { max: 30,  label: "Low",      cls: "pl-band--low"  },
  { max: 100, label: "Moderate", cls: "pl-band--mod"  },
  { max: 300, label: "High",     cls: "pl-band--high" },
  { max: Infinity, label: "Very High", cls: "pl-band--vhigh" },
];

function bandForCount(v) {
  if (v == null) return { label: "—", cls: "" };
  return BANDS.find((b) => v <= b.max) || BANDS[BANDS.length - 1];
}
function bandForLabel(label) {
  if (!label) return null;
  const map = {
    "low":       BANDS[0],
    "moderate":  BANDS[1],
    "high":      BANDS[2],
    "very high": BANDS[3],
    "extreme":   BANDS[3],
    "off season": { label: "Off Season", cls: "" },
  };
  return map[label.toLowerCase()] || null;
}

function fmtInt(v) { return v == null ? "—" : String(Math.round(v)); }

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/weather_pollen_count/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }

  const size = ctx.cell.size;
  // Prefer the scraped label when the source is text-only (MPC has no
  // grains count), fall back to the count-driven band otherwise.
  const band = bandForLabel(data.grass_label) || bandForCount(data.grass);
  const showCount = data.grass != null && !data.grass_label;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_pollen_count/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="pl-place">${escapeHtml(data.label || "—")}</span>
        <span class="pl-time">${nowTime()}</span>
      </header>
      <section class="pl-hero ${band.cls}">
        <div class="pl-hero-text">
          <div class="pl-level">${escapeHtml(band.label)}</div>
          <div class="pl-headline">Grass Pollen</div>
          ${showCount ? `<div class="pl-count">${fmtInt(data.grass)}<small>grains/m³</small></div>` : ""}
          ${data.source ? `<div class="pl-source">via ${escapeHtml(data.source)}</div>` : ""}
        </div>
        <div class="pl-hero-icon" aria-hidden="true">
          <i class="ph-bold ph-flower-tulip"></i>
        </div>
      </section>
      <section class="pl-stats">
        <div class="pl-stat pl-stat--accent">
          <i class="ph-bold ph-tree pl-stat-icon" aria-hidden="true"></i>
          <span class="pl-stat-label">Tree</span>
          <span class="pl-stat-value">${fmtInt(data.tree)}</span>
        </div>
        <div class="pl-stat pl-stat--accent2">
          <i class="ph-bold ph-plant pl-stat-icon" aria-hidden="true"></i>
          <span class="pl-stat-label">Grass</span>
          <span class="pl-stat-value">${fmtInt(data.grass)}</span>
        </div>
        <div class="pl-stat pl-stat--accent3">
          <i class="ph-bold ph-leaf pl-stat-icon" aria-hidden="true"></i>
          <span class="pl-stat-label">Weed</span>
          <span class="pl-stat-value">${fmtInt(data.weed)}</span>
        </div>
      </section>
    </div>
  `;
}
