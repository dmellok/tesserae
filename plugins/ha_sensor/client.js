// ha_sensor — Spectra stat (single) or list (multi).
//
// One sensor → hero number (stat archetype) with the unit as a small
// trailing label, a trend arrow next to the unit, and a mini SVG
// sparkline filling the bottom strip. Two or more → list archetype
// with the sensor icon leading, each row carrying a trend arrow + a
// hairline sparkline beside its value.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const TREND_ICON = {
  up: "ph-arrow-up-right",
  down: "ph-arrow-down-right",
  flat: "ph-arrow-right",
};

const TREND_ACCENT = {
  up: "var(--accent-3)",
  down: "var(--accent-1)",
  flat: "var(--text-muted)",
};

function trendIcon(t) {
  return TREND_ICON[t] || "";
}

function trendAccent(t) {
  return TREND_ACCENT[t] || "var(--text-muted)";
}

function sensorAccent(icon) {
  // Pick a coherent accent per sensor category. Tracks the expanded
  // device-class set in the server's _DEVICE_CLASS_ICONS table.
  switch (icon) {
    case "drop":
    case "drop-half":
    case "wind":
      return "var(--accent-4)";
    case "thermometer":
    case "thermometer-simple":
    case "thermometer-hot":
    case "thermometer-cold":
    case "sun":
    case "sun-dim":
    case "flame":
    case "battery-medium":
    case "lightning":
      return "var(--accent-2)";
    case "wave-sine":
    case "wave-sawtooth":
    case "wave-square":
    case "wave-triangle":
    case "wifi-high":
    case "signal-strength":
      return "var(--accent-5)";
    case "currency-circle-dollar":
    case "skull":
    case "circles-three-plus":
      return "var(--accent-1)";
    case "cloud":
    case "speedometer":
    case "ruler":
    case "scales":
      return "var(--accent-3)";
    default:
      return "var(--text-secondary)";
  }
}

// SVG sparkline. Accepts a series + colour + dimensions. Uses
// preserveAspectRatio="none" so the curve stretches to fill the
// container in both axes — perfect for inline strips of varying
// aspect ratios.
function sparklineSvg(series, color, opts = {}) {
  if (!Array.isArray(series) || series.length < 2) return "";
  const w = opts.w ?? 100;
  const h = opts.h ?? 24;
  const padY = opts.padY ?? 2;
  const innerH = h - padY * 2;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min < 0.0001 ? 1 : max - min;
  const step = w / Math.max(1, series.length - 1);
  const points = series.map((v, i) => {
    const x = i * step;
    const y = padY + innerH - ((v - min) / range) * innerH;
    return { x, y };
  });
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" ");
  const fill = opts.fill
    ? `${path} L ${w} ${h} L 0 ${h} Z`
    : null;
  return `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
         width="100%" height="100%" aria-hidden="true">
      ${fill ? `<path d="${fill}" fill="${color}" opacity="0.18"/>` : ""}
      <path d="${path}" fill="none" stroke="${color}"
            stroke-width="${opts.strokeWidth ?? 2}"
            stroke-linejoin="round" stroke-linecap="round"/>
    </svg>`;
}

function renderStat(item, title) {
  const accent = sensorAccent(item.icon);
  const muted = item.unavailable;
  const color = muted ? "var(--text-muted)" : accent;
  const ph = `ph-${item.icon || "gauge"}`;
  const trend = item.trend;
  const trendBit = trend && trendIcon(trend)
    ? `<i class="ph-bold ${trendIcon(trend)}" style="color:${trendAccent(trend)};font-size:.6em;margin-left:.25em;vertical-align:.15em" title="${trend} vs 24h ago"></i>`
    : "";
  const spark = Array.isArray(item.sparkline) && item.sparkline.length >= 2
    ? `<div class="sensor-spark">${sparklineSvg(item.sparkline, color, { w: 200, h: 36, fill: true, strokeWidth: 2.4 })}</div>`
    : "";
  return `
    <div class="w-title">
      <i class="ph-bold ${ph}" style="color:${color}"></i>
      <h3>${escapeHtml(title)}</h3>
    </div>
    <div class="w-body stat-body" style="gap:var(--space-2)">
      <div class="stat-value">
        ${escapeHtml(item.value ?? "—")}
        ${item.unit ? `<span class="unit">${escapeHtml(item.unit)}</span>` : ""}
        ${trendBit}
      </div>
      ${spark}
    </div>`;
}

function renderList(items, title) {
  const rows = items.map((it, i) => {
    const accent = it.unavailable ? "var(--text-muted)" : sensorAccent(it.icon);
    const ph = `ph-${it.icon || "gauge"}`;
    const unit = it.unit ? `<span class="u-muted" style="font-weight:var(--fw-semi)"> ${escapeHtml(it.unit)}</span>` : "";
    const trend = it.trend;
    const trendBit = trend && trendIcon(trend)
      ? `<i class="ph-bold ${trendIcon(trend)} sensor-trend" style="color:${trendAccent(trend)}" title="${trend} vs 24h"></i>`
      : "";
    const spark = Array.isArray(it.sparkline) && it.sparkline.length >= 2
      ? `<span class="sensor-row-spark">${sparklineSvg(it.sparkline, accent, { w: 64, h: 16, strokeWidth: 1.5 })}</span>`
      : "";
    return `
      <div class="sensor-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead sensor-row-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(it.name)}</span>
        </div>
        <div class="sensor-row-meta">
          ${spark}
          ${trendBit}
          <span class="sensor-row-value" style="color:${accent}">${escapeHtml(it.value ?? "—")}${unit}</span>
        </div>
      </div>`;
  }).join("");
  return `
    <div class="w-title">
      <i class="ph-bold ph-gauge" style="color:var(--accent-3)"></i>
      <h3>${escapeHtml(title)}</h3>
      <span class="w-title-meta">${items.length}</span>
    </div>
    <div class="w-body list-body">${rows}</div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_sensor">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Sensors</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const title = data.title || (items.length === 1 ? items[0].name : "Sensors");

  if (data.empty || items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_sensor">
        <div class="w-title"><i class="ph-bold ph-gauge"></i><h3>${escapeHtml(title)}</h3></div>
        <div class="w-body"><p class="u-muted">No sensors selected.</p></div>
      </div>`;
    return;
  }

  const layout = `
    /* Hero sparkline strip for the single-entity stat mode. Fills
       the body's flexible space so the hero number sits on top and
       the curve carries the bottom. */
    .sensor-spark {
      flex: 1 1 auto;
      min-height: 2.5em;
      width: 100%;
    }
    /* Per-row meta for the list mode. Tabular nums on the value so
       columns line up across rows. */
    .sensor-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .sensor-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .sensor-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .sensor-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .sensor-row-meta {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      flex: 0 0 auto;
    }
    .sensor-row-spark {
      width: 4em;
      height: 1em;
      display: inline-flex;
      align-items: center;
      flex: 0 0 auto;
    }
    .sensor-trend {
      font-size: .9em;
    }
    .sensor-row-value {
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
    }
    @container (max-width: 320px) {
      .sensor-row-spark { display: none; }
    }
  `;

  const body = items.length === 1 ? renderStat(items[0], title) : renderList(items, title);
  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_sensor">${body}</div>`;
}
