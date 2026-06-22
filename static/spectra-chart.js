// Spectra · Chart.js helpers. Each widget that needs a chart imports
// this module with a relative path so HA Ingress prefixing rides the
// document base:
//
//   import { sparkline, tokens } from "../../static/spectra-chart.js";
//
// Chart.js is loaded as a regular `<script>` in compose.html so the
// global Chart constructor is ready by the time these helpers run.
// All chart instances get `animation: false`, the Spectra e-ink
// spec forbids motion, and the renderer screenshots mid-animation
// otherwise.

const FALLBACK = {
  accent1: "#A84B2A", accent2: "#9A7414", accent3: "#4F6F36",
  accent4: "#256E6B", accent5: "#3F5A88", accent6: "#7E4068",
  surface: "#F7F5F0", surfaceSunken: "#E1DDD2",
  textPrimary: "#1B1A16", textSecondary: "#4D4A42", textMuted: "#837F73",
  fontFamily: "Helvetica Neue, Arial, sans-serif",
};

// Probe the active CSS cascade for a token value. We render an invisible
// element next to the chart host (so it inherits the same per-cell theme
// override + page-level data-theme that the chart should be using),
// apply the token to a real CSS property, then read the resolved style
// back. ``transparent`` as the var() fallback computes to
// ``rgba(0, 0, 0, 0)``, a value no Spectra theme produces, so we can
// distinguish "the var resolved" from "the var was undefined."
//
// Why not `getComputedStyle(host).getPropertyValue('--accent-1')`?
// That path returns empty for inherited custom properties in some
// shadow-host scenarios (host attached but cascade not yet flushed,
// or reading before layout), even when spectra-tokens.css is loaded
// and the var IS defined upstream. Probing through a real property
// forces var() substitution through the rendering pipeline, so we
// get the actual cascaded value every time.
function probeColor(parent, name) {
  const probe = document.createElement("span");
  probe.style.cssText =
    "position:absolute;visibility:hidden;width:0;height:0;pointer-events:none;";
  probe.style.color = `var(${name}, transparent)`;
  parent.appendChild(probe);
  const v = getComputedStyle(probe).color || "";
  probe.remove();
  return v === "rgba(0, 0, 0, 0)" ? "" : v;
}

// Same trick for --font-family. A made-up family name as the var()
// fallback is unique enough to detect, no real font ships with that
// name, so finding it in the resolved string means the var was unset.
function probeFontFamily(parent, name) {
  const SENTINEL = "__spectra_missing_family__";
  const probe = document.createElement("span");
  probe.style.cssText =
    "position:absolute;visibility:hidden;width:0;height:0;pointer-events:none;";
  probe.style.fontFamily = `var(${name}, ${SENTINEL})`;
  parent.appendChild(probe);
  const v = getComputedStyle(probe).fontFamily || "";
  probe.remove();
  return v.includes(SENTINEL) ? "" : v;
}

export function tokens(host) {
  if (!host) {
    console.warn(
      "[spectra-chart] tokens(host=null), pass the cell host (shadow.host) so charts inherit the cell's per-cell theme override."
    );
  }
  // Probe under host.parentElement so per-cell data-theme overrides take
  // effect (host = .cell-content, host.parentElement = .cell which is
  // where the per-cell data-theme attribute lives). Fall back to body /
  // documentElement if no host is available.
  const parent =
    (host && host.parentElement) ||
    (typeof document !== "undefined" ? document.body : null) ||
    (typeof document !== "undefined" ? document.documentElement : null);
  if (!parent) return { ...FALLBACK };

  const missing = [];
  const cget = (name, fallback) => {
    const v = probeColor(parent, name);
    if (!v) missing.push(name);
    return v || fallback;
  };
  const fget = (name, fallback) => {
    const v = probeFontFamily(parent, name);
    if (!v) missing.push(name);
    return v || fallback;
  };
  const result = {
    accent1: cget("--accent-1", FALLBACK.accent1),
    accent2: cget("--accent-2", FALLBACK.accent2),
    accent3: cget("--accent-3", FALLBACK.accent3),
    accent4: cget("--accent-4", FALLBACK.accent4),
    accent5: cget("--accent-5", FALLBACK.accent5),
    accent6: cget("--accent-6", FALLBACK.accent6),
    surface: cget("--surface", FALLBACK.surface),
    surfaceSunken: cget("--surface-sunken", FALLBACK.surfaceSunken),
    textPrimary: cget("--text-primary", FALLBACK.textPrimary),
    textSecondary: cget("--text-secondary", FALLBACK.textSecondary),
    textMuted: cget("--text-muted", FALLBACK.textMuted),
    fontFamily: fget("--font-family", FALLBACK.fontFamily),
  };
  if (missing.length) {
    console.warn(
      `[spectra-chart] Spectra tokens didn't resolve via cascade: ${missing.join(", ")}` +
      ", falling back to light-theme defaults. Check that spectra-tokens.css is linked from the page."
    );
  }
  // Opt-in diagnostic. Two ways to enable:
  //   1. paste ``window.__TESSERAE_CHART_DEBUG = true`` in DevTools, then
  //      trigger a re-render (a theme/style flip patches every cell).
  //   2. append ``?chartdebug=1`` to the page URL, works from a cold
  //      reload, useful when the page is loaded inside an iframe whose
  //      window object the parent can't touch.
  // Prints the resolved palette + the probe's ancestry every time a chart
  // asks for tokens so we can trace why a chart shows the wrong colour
  // without having to mess with the helper itself.
  const debug =
    (typeof window !== "undefined" && window.__TESSERAE_CHART_DEBUG) ||
    (typeof location !== "undefined" && /[?&]chartdebug=1\b/.test(location.search));
  if (debug) {
    const bodyTheme =
      (typeof document !== "undefined" && document.body && document.body.getAttribute("data-theme")) || "(none)";
    const probeParent = parent.tagName + (parent.className ? "." + parent.className.replace(/\s+/g, ".") : "");
    console.log("[spectra-chart] tokens resolved", {
      bodyDataTheme: bodyTheme,
      probedUnder: probeParent,
      missing,
      result,
    });
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

// Build ``rgba(r, g, b, alpha)`` from a CSS colour so we can derive
// translucent area fills under chart lines without touching the
// caller's source colour. Accepts both ``#RRGGBB`` (the FALLBACK
// constants, plus any hex token a caller passes directly) and
// ``rgb(...)`` / ``rgba(...)`` (the form ``probeColor`` returns,
// since getComputedStyle resolves to rgba). Returns the input
// untouched if it's neither, caller falls back to whatever Chart.js
// makes of it.
function withAlpha(color, alpha) {
  if (typeof color !== "string") return color;
  if (color.startsWith("#") && color.length === 7) {
    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);
    if (!Number.isNaN(r) && !Number.isNaN(g) && !Number.isNaN(b)) {
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
  }
  const m = color.match(
    /^\s*rgba?\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)/
  );
  if (m) return `rgba(${m[1]}, ${m[2]}, ${m[3]}, ${alpha})`;
  return color;
}

// Minimal sparkline, no axes, no legend, no tooltip. Tension 0.3 so
// the line reads as a smooth trend rather than connected straight
// segments. Stroke at 3px respects the Spectra data-stroke floor;
// the area beneath is filled with the line colour at 18% alpha so
// the trend has visible weight even at small sizes. Used by finance,
// weather, energy.
//
// Optional `opts` (third arg, when sparkline is called with the
// three-arg form `sparkline(canvas, values, color)` the third can
// instead be an options object). Supports:
//   overlay: { values, color, dash }, a thin secondary series
//     rendered behind the main line (e.g. rolling average).
export function sparkline(canvas, values, colorOrOpts) {
  if (!ensureChart(canvas) || !Array.isArray(values) || values.length < 2) return null;
  // Tolerate both `sparkline(c, v, "#hex")` and `sparkline(c, v, {color, overlay})`.
  const opts = (colorOrOpts && typeof colorOrOpts === "object" && !Array.isArray(colorOrOpts))
    ? colorOrOpts
    : { color: colorOrOpts };
  const color = opts.color;

  const datasets = [{
    data: values,
    borderColor: color,
    backgroundColor: withAlpha(color, 0.18),
    borderWidth: 3,
    tension: 0.3,
    pointRadius: 0,
    fill: "origin",
    order: 1,
  }];
  if (opts.overlay && Array.isArray(opts.overlay.values) && opts.overlay.values.length >= 2) {
    datasets.push({
      data: opts.overlay.values,
      borderColor: opts.overlay.color || color,
      borderWidth: 1.6,
      borderDash: opts.overlay.dash || [4, 4],
      tension: 0.35,
      pointRadius: 0,
      fill: false,
      order: 2,
    });
  }

  const chart = new window.Chart(canvas, {
    type: "line",
    data: { labels: values.map((_, i) => i), datasets },
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
          // Headroom above the max so the tension:0.3 spline's overshoot
          // on a sharp spike isn't sliced off flat against the top edge.
          grace: "12%",
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
// time-series). Default styling matches the sparkline, 3px stroke
// with the area filled at 18% alpha, so the chart reads as a
// confident bauhaus block, not a thin technical line. Pass
// ``fill: false`` to opt out of the shaded area.
//
// Optional advanced features:
//   threshold: { value, label, color }, draws a horizontal dashed line
//   markers: [{ index, color, label, position }], point markers on the line
//   overlay: { values, color, label }, secondary series rendered behind
//     the main line as a thin dashed ghost
export function lineChart(canvas, opts) {
  if (!ensureChart(canvas) || !opts || !Array.isArray(opts.values) || opts.values.length < 2) return null;
  const t = opts.tokens || FALLBACK;
  const labels = opts.labels || opts.values.map((_, i) => i);
  const color = opts.color || t.accent4;
  const wantsFill = opts.fill !== false;

  // Per-point radius array. Most points are size 0 (hidden); marker
  // indexes get a chunky pip.
  const markers = Array.isArray(opts.markers) ? opts.markers : [];
  const pointRadiusArr = opts.values.map(() => 0);
  const pointBgArr = opts.values.map(() => color);
  const pointBorderArr = opts.values.map(() => t.surface);
  for (const m of markers) {
    if (Number.isFinite(m.index) && m.index >= 0 && m.index < opts.values.length) {
      pointRadiusArr[m.index] = m.radius || 5;
      pointBgArr[m.index] = m.color || color;
    }
  }

  const datasets = [{
    data: opts.values,
    borderColor: color,
    backgroundColor: wantsFill ? withAlpha(color, 0.18) : "transparent",
    borderWidth: 3,
    tension: 0.25,
    pointRadius: pointRadiusArr,
    pointBackgroundColor: pointBgArr,
    pointBorderColor: pointBorderArr,
    pointBorderWidth: 2,
    fill: wantsFill ? "origin" : false,
    order: 1,
  }];

  // Secondary "overlay" series, e.g. an hourly-profile ghost on top
  // of a multi-day window. Always thin + dashed + muted so it reads
  // as background context, not data the eye should track first.
  if (opts.overlay && Array.isArray(opts.overlay.values) && opts.overlay.values.length >= 2) {
    datasets.push({
      data: opts.overlay.values,
      borderColor: opts.overlay.color || t.textMuted,
      borderWidth: 1.5,
      borderDash: [3, 3],
      tension: 0.4,
      pointRadius: 0,
      fill: false,
      order: 2,
    });
  }

  // Threshold-line plugin, draws a dashed horizontal line + a value
  // label at the threshold's y-coordinate. Defined inline because it
  // needs access to the per-call opts.
  const thresholdPlugin = opts.threshold && Number.isFinite(opts.threshold.value) ? {
    id: "tess_threshold",
    afterDatasetsDraw(chart) {
      const { ctx, chartArea, scales } = chart;
      const yScale = scales.y;
      if (!yScale) return;
      const y = yScale.getPixelForValue(opts.threshold.value);
      if (y < chartArea.top || y > chartArea.bottom) return;
      ctx.save();
      ctx.strokeStyle = opts.threshold.color || t.accent1;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(chartArea.left, y);
      ctx.lineTo(chartArea.right, y);
      ctx.stroke();
      ctx.setLineDash([]);
      // Label pill, top-right of the line.
      const label = String(opts.threshold.label ?? opts.threshold.value);
      ctx.font = `700 11px ${t.fontFamily}`;
      const pad = 4;
      const tw = ctx.measureText(label).width + pad * 2;
      const th = 14;
      const tx = chartArea.right - tw - 2;
      const ty = y - th - 2;
      ctx.fillStyle = opts.threshold.color || t.accent1;
      ctx.fillRect(tx, ty, tw, th);
      ctx.fillStyle = t.surface;
      ctx.textBaseline = "middle";
      ctx.fillText(label, tx + pad, ty + th / 2 + 0.5);
      ctx.restore();
    },
  } : null;

  // Marker-label plugin, draws a small label next to each marker
  // (e.g. "12.5" beside the min point).
  const markerLabelPlugin = markers.length > 0 ? {
    id: "tess_markers",
    afterDatasetsDraw(chart) {
      const { ctx, scales } = chart;
      const yScale = scales.y;
      const xScale = scales.x;
      if (!yScale || !xScale) return;
      ctx.save();
      ctx.font = `800 11px ${t.fontFamily}`;
      ctx.textBaseline = "middle";
      for (const m of markers) {
        if (!Number.isFinite(m.index) || !m.label) continue;
        const x = xScale.getPixelForValue(m.index);
        const y = yScale.getPixelForValue(opts.values[m.index]);
        const placeAbove = m.position === "above";
        const label = String(m.label);
        const pad = 4;
        const tw = ctx.measureText(label).width + pad * 2;
        const th = 14;
        const bx = x - tw / 2;
        const by = placeAbove ? y - th - 8 : y + 10;
        ctx.fillStyle = m.color || color;
        ctx.fillRect(bx, by, tw, th);
        ctx.fillStyle = t.surface;
        ctx.fillText(label, bx + pad, by + th / 2 + 0.5);
      }
      ctx.restore();
    },
  } : null;

  const plugins = [];
  if (thresholdPlugin) plugins.push(thresholdPlugin);
  if (markerLabelPlugin) plugins.push(markerLabelPlugin);

  const chart = new window.Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    plugins,
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

// Sankey diagram, flows between source rails (left) and sink rails
// (right) with proportional band thickness. Backed by
// chartjs-chart-sankey, which adds a `sankey` controller to the
// global Chart constructor when its script loads.
//
// Pass:
//   flows: [{ from: "Solar", to: "House", flow: 1.4 }, ...]
//   colors: { "Solar": "#A87412", "House": "#1B1A16", ... }
//   tokens (optional, used for axis text colour)
//
// Each `from` and `to` key becomes a labelled rail. The library lays
// the rails out automatically, heights ∝ summed flow through each
// node. `colors` keys must match the rail names exactly.
export function sankey(canvas, opts) {
  if (!ensureChart(canvas) || !opts || !Array.isArray(opts.flows) || !opts.flows.length) return null;
  const t = opts.tokens || FALLBACK;
  const colors = opts.colors || {};
  const data = opts.flows.map((f) => ({ from: String(f.from), to: String(f.to), flow: Number(f.flow) || 0 }));

  const chart = new window.Chart(canvas, {
    type: "sankey",
    data: {
      datasets: [{
        data,
        colorFrom: (c) => colors[c.dataset.data[c.dataIndex].from] || t.accent4,
        colorTo: (c) => colors[c.dataset.data[c.dataIndex].to] || t.accent4,
        colorMode: opts.colorMode || "gradient",
        labels: opts.labels || undefined,
        size: opts.sizeMode || "max",
        // Padding around each rail's stack of bands.
        nodePadding: opts.nodePadding ?? 8,
        borderWidth: 0,
        // Override font for rail labels so they pick up the cell's
        // theme tokens.
        font: { family: t.fontFamily, weight: 800, size: opts.labelSize ?? 12 },
        color: t.textSecondary,
      }],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
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
