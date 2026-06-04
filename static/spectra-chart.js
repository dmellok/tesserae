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

// Read the current Spectra tokens. Walks the cascade explicitly —
// host → body → documentElement — instead of trusting a single
// getComputedStyle call on the host. The host being temporarily
// detached during a re-render, or the cell mid-paint, was making
// chart colours silently drop to the light-theme fallback even on a
// dark page. ``<body data-theme>`` is the canonical theme anchor
// (compose.html sets it there), so the body lookup catches any
// situation where the host's cascade hasn't resolved yet.
function readToken(host, name) {
  if (host) {
    const v = getComputedStyle(host).getPropertyValue(name).trim();
    if (v) return v;
  }
  if (typeof document !== "undefined") {
    if (document.body) {
      const v = getComputedStyle(document.body).getPropertyValue(name).trim();
      if (v) return v;
    }
    if (document.documentElement) {
      const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      if (v) return v;
    }
  }
  return "";
}

export function tokens(host) {
  if (!host) {
    console.warn(
      "[spectra-chart] tokens(host=null) — pass the cell host (shadow.host) so charts inherit the cell's per-cell theme override."
    );
  }
  const missing = [];
  const get = (name, fallback) => {
    const v = readToken(host, name);
    if (!v) missing.push(name);
    return v || fallback;
  };
  const result = {
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
  if (missing.length) {
    console.warn(
      `[spectra-chart] Spectra tokens not resolved on host OR body OR documentElement: ${missing.join(", ")}` +
      " — falling back to light-theme hex defaults. Charts will look light-theme even on a dark page." +
      " Check that spectra-tokens.css is linked from compose.html."
    );
  }
  return result;
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

// Convert ``#RRGGBB`` to ``rgba(r, g, b, alpha)`` so we can build
// translucent area fills under chart lines without touching the
// caller's source colour. Falls back to the input untouched if it
// isn't a 6-digit hex (a CSS function form like rgb(...) already
// renders correctly on canvas).
function withAlpha(color, alpha) {
  if (typeof color !== "string" || !color.startsWith("#") || color.length !== 7) return color;
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) return color;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Minimal sparkline — no axes, no legend, no tooltip. Tension 0.3 so
// the line reads as a smooth trend rather than connected straight
// segments. Stroke at 3px respects the Spectra data-stroke floor;
// the area beneath is filled with the line colour at 18% alpha so
// the trend has visible weight even at small sizes. Used by finance,
// weather, energy.
export function sparkline(canvas, values, color) {
  if (!ensureChart(canvas) || !Array.isArray(values) || values.length < 2) return null;
  const chart = new window.Chart(canvas, {
    type: "line",
    data: {
      labels: values.map((_, i) => i),
      datasets: [{
        data: values,
        borderColor: color,
        backgroundColor: withAlpha(color, 0.18),
        borderWidth: 3,
        tension: 0.3,
        pointRadius: 0,
        fill: "origin",
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      // For positive-only series (finance, energy flow) the y origin
      // sits below the data so the fill reaches the bottom of the
      // chart. Min/max stay auto so the line still tracks the range.
      scales: {
        x: { display: false },
        y: {
          display: false,
          beginAtZero: false,
          // Pad below the min so the fill doesn't collapse to a
          // sliver against the bottom edge.
          suggestedMin: Math.min(...values) - (Math.max(...values) - Math.min(...values)) * 0.15,
        },
      },
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
// time-series). Default styling matches the sparkline — 3px stroke
// with the area filled at 18% alpha — so the chart reads as a
// confident bauhaus block, not a thin technical line. Pass
// ``fill: false`` to opt out of the shaded area.
export function lineChart(canvas, opts) {
  if (!ensureChart(canvas) || !opts || !Array.isArray(opts.values) || opts.values.length < 2) return null;
  const t = opts.tokens || FALLBACK;
  const labels = opts.labels || opts.values.map((_, i) => i);
  const color = opts.color || t.accent4;
  const wantsFill = opts.fill !== false;

  const chart = new window.Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: opts.values,
        borderColor: color,
        backgroundColor: wantsFill ? withAlpha(color, 0.18) : "transparent",
        borderWidth: 3,
        tension: 0.25,
        pointRadius: 0,
        fill: wantsFill ? "origin" : false,
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
