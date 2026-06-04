// Spectra · Chart.js helpers. Each widget that needs a chart imports
// this module with a relative path so HA Ingress prefixing rides the
// document base:
//
//   import { sparkline, tokens } from "../../static/spectra-chart.js";
//
// Chart.js is loaded as a regular `<script>` in compose.html so the
// global Chart constructor is ready by the time these helpers run.
// All chart instances get `animation: false` — the Spectra e-ink
// spec forbids motion, and the renderer screenshots mid-animation
// otherwise.

const FALLBACK = {
  accent1: "#A84B2A", accent2: "#9A7414", accent3: "#4F6F36",
  accent4: "#256E6B", accent5: "#3F5A88", accent6: "#7E4068",
  surface: "#F7F5F0", surfaceSunken: "#E1DDD2",
  textPrimary: "#1B1A16", textSecondary: "#4D4A42", textMuted: "#837F73",
  fontFamily: "Helvetica Neue, Arial, sans-serif",
};

// Read the current Spectra tokens off the host element. Falls back
// to the light-theme values when a property is empty, so a chart
// always renders even if the cascade hasn't resolved yet.
export function tokens(host) {
  if (!host) return { ...FALLBACK };
  const s = getComputedStyle(host);
  const get = (name, fallback) => {
    const v = s.getPropertyValue(name).trim();
    return v || fallback;
  };
  return {
    accent1: get("--accent-1", FALLBACK.accent1),
    accent2: get("--accent-2", FALLBACK.accent2),
    accent3: get("--accent-3", FALLBACK.accent3),
    accent4: get("--accent-4", FALLBACK.accent4),
    accent5: get("--accent-5", FALLBACK.accent5),
    accent6: get("--accent-6", FALLBACK.accent6),
    surface: get("--surface", FALLBACK.surface),
    surfaceSunken: get("--surface-sunken", FALLBACK.surfaceSunken),
    textPrimary: get("--text-primary", FALLBACK.textPrimary),
    textSecondary: get("--text-secondary", FALLBACK.textSecondary),
    textMuted: get("--text-muted", FALLBACK.textMuted),
    fontFamily: get("--font-family", FALLBACK.fontFamily),
  };
}

function ensureChart(canvas) {
  if (!window.Chart || !canvas) return false;
  // Clean up any previous instance bound to this canvas so widgets
  // can re-render without leaking the old chart's resize observer.
  if (canvas._chart) {
    try { canvas._chart.destroy(); } catch { /* ignore */ }
    canvas._chart = null;
  }
  return true;
}

// Minimal sparkline — no axes, no legend, no tooltip. Tension 0.3 so
// the line reads as a smooth trend rather than connected straight
// segments. Used by finance, weather "now temp delta", energy flows.
export function sparkline(canvas, values, color) {
  if (!ensureChart(canvas) || !Array.isArray(values) || values.length < 2) return null;
  const chart = new window.Chart(canvas, {
    type: "line",
    data: {
      labels: values.map((_, i) => i),
      datasets: [{
        data: values,
        borderColor: color,
        borderWidth: 2,
        tension: 0.3,
        pointRadius: 0,
        fill: false,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      scales: { x: { display: false }, y: { display: false } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
  canvas._chart = chart;
  return chart;
}

// Bar chart with axis labels. Used by weather_hourly + ha_history.
// ``highlightIdx`` lets the caller bump one bar to a different colour
// (e.g. the current hour in weather_hourly).
export function barChart(canvas, opts) {
  if (!ensureChart(canvas) || !opts || !Array.isArray(opts.values) || !opts.values.length) return null;
  const t = opts.tokens || FALLBACK;
  const labels = opts.labels || opts.values.map((_, i) => i);
  const baseColor = opts.color || t.accent5;
  const highlightColor = opts.highlightColor || t.accent1;
  const highlightIdx = Number.isFinite(opts.highlightIdx) ? opts.highlightIdx : -1;
  const colors = opts.values.map((_, i) => (i === highlightIdx ? highlightColor : baseColor));

  const chart = new window.Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: opts.values,
        backgroundColor: colors,
        borderWidth: 0,
        borderRadius: 0,
        categoryPercentage: 0.85,
        barPercentage: 0.95,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          ticks: {
            color: t.textMuted,
            font: { family: t.fontFamily, weight: 700, size: 10 },
            autoSkip: true, maxRotation: 0,
          },
          grid: { display: false },
          border: { display: false },
        },
        y: {
          display: opts.showY !== false,
          ticks: {
            color: t.textMuted,
            font: { family: t.fontFamily, weight: 700, size: 10 },
          },
          grid: { color: t.surfaceSunken, drawTicks: false },
          border: { display: false },
        },
      },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
  canvas._chart = chart;
  return chart;
}

// Line chart with axis labels. Used by ha_history (single sensor
// time-series). Filled area under the line is optional — pass
// ``fill: true`` to get a soft tinted area beneath.
export function lineChart(canvas, opts) {
  if (!ensureChart(canvas) || !opts || !Array.isArray(opts.values) || opts.values.length < 2) return null;
  const t = opts.tokens || FALLBACK;
  const labels = opts.labels || opts.values.map((_, i) => i);
  const color = opts.color || t.accent4;

  const chart = new window.Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: opts.values,
        borderColor: color,
        backgroundColor: opts.fill ? color : "transparent",
        borderWidth: 3,
        tension: 0.25,
        pointRadius: 0,
        fill: opts.fill === true,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          ticks: {
            color: t.textMuted,
            font: { family: t.fontFamily, weight: 700, size: 10 },
            autoSkip: true, maxRotation: 0,
            callback(value, index) {
              const lbl = this.getLabelForValue(value);
              const total = opts.values.length;
              // Show roughly 6 labels evenly across the axis so a long
              // series doesn't get one tick per point.
              const stride = Math.max(1, Math.floor(total / 6));
              return index % stride === 0 ? lbl : "";
            },
          },
          grid: { display: false },
          border: { display: false },
        },
        y: {
          display: opts.showY !== false,
          ticks: {
            color: t.textMuted,
            font: { family: t.fontFamily, weight: 700, size: 10 },
            maxTicksLimit: 4,
          },
          grid: { color: t.surfaceSunken, drawTicks: false },
          border: { display: false },
        },
      },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
  canvas._chart = chart;
  return chart;
}

// Bauhaus-style horizontal track + filled bar for things like
// histograms / battery levels. Smaller helper but keeps the e-ink
// chart styling unified.
export function hbar(canvas, opts) {
  if (!ensureChart(canvas) || !opts || !Array.isArray(opts.values)) return null;
  const t = opts.tokens || FALLBACK;
  const labels = opts.labels || opts.values.map((_, i) => i);
  const color = opts.color || t.accent5;

  const chart = new window.Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: opts.values,
        backgroundColor: color,
        borderWidth: 0,
        borderRadius: 0,
        categoryPercentage: 0.8,
        barPercentage: 0.9,
      }],
    },
    options: {
      animation: false,
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { display: false, grid: { display: false }, border: { display: false } },
        y: {
          ticks: {
            color: t.textMuted,
            font: { family: t.fontFamily, weight: 700, size: 10 },
          },
          grid: { display: false }, border: { display: false },
        },
      },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
  canvas._chart = chart;
  return chart;
}
