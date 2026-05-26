// weather_hourly — Bauhaus hourly card.
//
// Layout (top to bottom):
//   1. Inverted header bar (place + window + time)
//   2. Chart.js line — bold accent stroke on theme-surface
//   3. Weather-condition icon strip (md/lg) — one ph icon per sampled hour
//   4. Rain probability blocks (md/lg)
//   5. High / Low / Now chip strip
//
// The chips are at the bottom so the eye reads the chart first; the
// icons strip below the chart maps directly to the chart's x-axis so
// you can tell at a glance "rain at 3PM" without reading the trace.

function loadChart() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (window.__tesseraeChartJs) return window.__tesseraeChartJs;
  window.__tesseraeChartJs = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/chart.umd.min.js";
    s.async = true;
    s.onload = () => resolve(window.Chart);
    s.onerror = () => reject(new Error("failed to load chart.js"));
    document.head.appendChild(s);
  });
  return window.__tesseraeChartJs;
}

// WMO code -> { day, night } Phosphor icon names.
const WMO_ICON = {
  0:  { day: "sun",             night: "moon" },
  1:  { day: "sun",             night: "moon" },
  2:  { day: "cloud-sun",       night: "cloud-moon" },
  3:  { day: "cloud",           night: "cloud" },
  45: { day: "cloud-fog",       night: "cloud-fog" },
  48: { day: "cloud-fog",       night: "cloud-fog" },
  51: { day: "cloud-rain",      night: "cloud-rain" },
  53: { day: "cloud-rain",      night: "cloud-rain" },
  55: { day: "cloud-rain",      night: "cloud-rain" },
  56: { day: "snowflake",       night: "snowflake" },
  57: { day: "snowflake",       night: "snowflake" },
  61: { day: "cloud-rain",      night: "cloud-rain" },
  63: { day: "cloud-rain",      night: "cloud-rain" },
  65: { day: "cloud-rain",      night: "cloud-rain" },
  66: { day: "snowflake",       night: "snowflake" },
  67: { day: "snowflake",       night: "snowflake" },
  71: { day: "snowflake",       night: "snowflake" },
  73: { day: "snowflake",       night: "snowflake" },
  75: { day: "snowflake",       night: "snowflake" },
  77: { day: "snowflake",       night: "snowflake" },
  80: { day: "cloud-rain",      night: "cloud-rain" },
  81: { day: "cloud-rain",      night: "cloud-rain" },
  82: { day: "cloud-rain",      night: "cloud-rain" },
  85: { day: "snowflake",       night: "snowflake" },
  86: { day: "snowflake",       night: "snowflake" },
  95: { day: "cloud-lightning", night: "cloud-lightning" },
  96: { day: "cloud-lightning", night: "cloud-lightning" },
  99: { day: "cloud-lightning", night: "cloud-lightning" },
};

function iconForPoint(p) {
  const entry = WMO_ICON[p.code];
  if (!entry) return "cloud";
  return p.is_day !== false ? entry.day : entry.night;
}

function hexToRgba(hex, alpha) {
  if (typeof hex !== "string" || !/^#[0-9a-f]{6}$/i.test(hex)) return `rgba(0,0,0,${alpha})`;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
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
    <link rel="stylesheet" href="/plugins/weather_hourly/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

function labelEvery(points, size) {
  const target = size === "sm" ? 4 : size === "md" ? 6 : 8;
  return Math.max(1, Math.ceil(points.length / target));
}

// Pick N evenly-spaced indexes from a points array.
function sampleIndexes(total, want) {
  if (total <= want) return Array.from({ length: total }, (_, i) => i);
  const out = [];
  for (let i = 0; i < want; i++) {
    out.push(Math.round((i / (want - 1)) * (total - 1)));
  }
  return out;
}

function renderConditionStrip(points, size) {
  const want = size === "md" ? 8 : 12;
  const idxs = sampleIndexes(points.length, want);
  return idxs
    .map((i) => {
      const p = points[i];
      const icon = iconForPoint(p);
      return `
        <div class="wh-cond-cell">
          <i class="ph-bold ph-${icon}" aria-hidden="true"></i>
          <span class="wh-cond-hour">${p.hour}</span>
        </div>
      `;
    })
    .join("");
}

function renderRainBars(points) {
  return points
    .map((p) => {
      const pct = p.rain == null ? 0 : Math.max(0, Math.min(100, p.rain));
      const wet = pct >= 30;
      return `<span class="wh-rain-bar${wet ? " is-wet" : ""}" style="--rain: ${pct}%" title="${pct}% at ${p.hour}:00"></span>`;
    })
    .join("");
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }
  const points = Array.isArray(data.points) ? data.points : [];
  if (!points.length) {
    shadow.innerHTML = renderError("no hourly data");
    return;
  }

  const size = ctx.cell.size;
  const showStrip = size === "md" || size === "lg";
  const showRain = size === "md" || size === "lg";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_hourly/client.css">
    <div class="root size-${size}">
      <header class="wh-bar">
        <span class="wh-mark" aria-hidden="true"></span>
        <span class="wh-title">${data.label ? escapeHtml(data.label) + " · " : ""}Next ${data.hours || 24} hr</span>
        <span class="wh-time">${nowTime()}</span>
      </header>
      <section class="wh-chart">
        <canvas class="chart"></canvas>
      </section>
      ${showStrip ? `
      <section class="wh-cond-strip" aria-label="Hourly conditions">
        ${renderConditionStrip(points, size)}
      </section>` : ""}
      ${showRain ? `
      <section class="wh-rain">
        <span class="wh-rain-label">Rain</span>
        <div class="wh-rain-bars">
          ${renderRainBars(points)}
        </div>
      </section>` : ""}
      <section class="wh-chips">
        <div class="wh-chip wh-chip--high">
          <span class="wh-chip-label">High</span>
          <span class="wh-chip-value">${fmtTemp(data.max)}</span>
        </div>
        <div class="wh-chip wh-chip--low">
          <span class="wh-chip-label">Low</span>
          <span class="wh-chip-value">${fmtTemp(data.min)}</span>
        </div>
        ${data.current != null ? `
        <div class="wh-chip wh-chip--current">
          <span class="wh-chip-label">Now</span>
          <span class="wh-chip-value">${fmtTemp(data.current)}</span>
        </div>` : ""}
      </section>
    </div>
  `;

  let Chart;
  try {
    Chart = await loadChart();
  } catch (err) {
    shadow.innerHTML = renderError(err.message || "chart.js load failed");
    return;
  }

  const canvas = shadow.querySelector(".chart");
  if (!canvas) return;
  const t = ctx.theme;
  const step = labelEvery(points, size);
  const labels = points.map((p, i) => (i % step === 0 ? `${p.hour}:00` : ""));
  const temps = points.map((p) => p.temp);

  const fontFamily =
    ctx.font?.family || 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  const baseFont = {
    family: fontFamily,
    size: size === "lg" ? 13 : 11,
    weight: "700",
  };

  new Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          data: temps,
          borderColor: t.accent,
          backgroundColor: hexToRgba(t.accent, 0.16),
          borderWidth: 3,
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: t.fgSoft,
            font: baseFont,
            autoSkip: false,
            maxRotation: 0,
            callback(_value, index) { return labels[index] || ""; },
          },
        },
        y: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: t.fgSoft,
            font: baseFont,
            callback: (v) => `${Math.round(v)}°`,
            maxTicksLimit: 4,
          },
        },
      },
      layout: { padding: { top: 8, right: 12, bottom: 0, left: 0 } },
    },
  });
}
