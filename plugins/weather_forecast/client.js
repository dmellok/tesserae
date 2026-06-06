// weather_forecast — Spectra weather archetype, 5-day vertical stack.
//
// Each day is one row: ``[day] [icon] [Lo ─── Hi] [rain%]``. At lg the
// body becomes a two-column grid with a Chart.js high-temp trend on
// the right; below lg the chart is dropped entirely so cramped cells
// don't try to paint a sliver of a line. Today's row picks out its
// day label in --accent-4 and bumps the row background a touch so the
// "now" anchor is obvious.

import { lineChart, tokens } from "../../static/spectra-chart.js";

const PH_BY_NAME = {
  sun: "ph-sun",
  moon: "ph-moon",
  cloud: "ph-cloud",
  partly: "ph-cloud-sun",
  "partly-night": "ph-cloud-moon",
  drizzle: "ph-drop",
  rain: "ph-cloud-rain",
  "rain-heavy": "ph-cloud-rain",
  showers: "ph-cloud-rain",
  snow: "ph-snowflake",
  storm: "ph-cloud-lightning",
  fog: "ph-cloud-fog",
};

// Same condition palette as weather_now so a single dashboard reads
// consistently across both widgets.
const COND_ACCENT = {
  sun: "var(--accent-2)",
  moon: "var(--text-secondary)",
  cloud: "var(--accent-5)",
  partly: "var(--accent-2)",
  "partly-night": "var(--text-secondary)",
  drizzle: "var(--accent-4)",
  rain: "var(--accent-4)",
  "rain-heavy": "var(--accent-4)",
  showers: "var(--accent-4)",
  snow: "var(--accent-5)",
  storm: "var(--accent-1)",
  fog: "var(--text-muted)",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTemp(v) {
  if (v == null) return "—";
  return Math.round(Number(v)) + "°";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="weather_forecast">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Forecast</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.place || data.label || "";
  const days = Array.isArray(data.days) ? data.days : [];

  // Overall lo / hi across all days — every per-day range bar is
  // scaled into this window so a calm week reads as short slices and
  // a swingy week stretches edge to edge.
  const allHis = days.map((d) => Number(d.hi ?? d.high)).filter(Number.isFinite);
  const allLos = days.map((d) => Number(d.lo ?? d.low)).filter(Number.isFinite);
  const weekMax = allHis.length ? Math.max(...allHis) : 0;
  const weekMin = allLos.length ? Math.min(...allLos) : 0;
  const weekSpan = Math.max(1, weekMax - weekMin);

  const rows = days.map((d) => {
    const ph = PH_BY_NAME[d.icon] || "ph-cloud";
    const accent = COND_ACCENT[d.icon] || "var(--accent-5)";
    const isToday = d.today;
    const dayText = d.day || (typeof d.weekday === "number" ? "" : d.weekday) || "";
    const hi = Number(d.hi ?? d.high);
    const lo = Number(d.lo ?? d.low);
    const left = Number.isFinite(lo) ? ((lo - weekMin) / weekSpan) * 100 : 0;
    const width = Number.isFinite(lo) && Number.isFinite(hi) ? ((hi - lo) / weekSpan) * 100 : 0;
    const rainRaw = Number(d.rain);
    const rainPct = Number.isFinite(rainRaw) ? Math.max(0, Math.min(100, rainRaw)) : null;
    // The droplet icon turns teal when the chance is high enough to
    // matter ("grab a jacket"); a dry day still gets a muted droplet
    // so every row keeps the same column rhythm.
    // Icon sits AFTER the value so when the row right-aligns the
    // chip, the droplet glyph parks at the same x-position every row
    // — the percentage value flexes leftward as digits grow ("5%" vs
    // "65%") without shifting the icon column.
    const rainBlock = rainPct == null
      ? '<span class="wxf-rain wxf-rain--unknown" aria-hidden="true"></span>'
      : `<span class="wxf-rain ${rainPct >= 40 ? "is-wet" : ""}" title="Chance of precipitation">
           <span class="wxf-rain-val">${rainPct.toFixed(0)}%</span>
           <i class="ph-bold ph-drop"></i>
         </span>`;
    return `
      <div class="wxf-row ${isToday ? "is-today" : ""}">
        <span class="wxf-day">${escapeHtml(dayText)}</span>
        <i class="ph-bold ${ph} wxf-icon" style="color:${accent}"></i>
        <span class="wxf-lo">${escapeHtml(fmtTemp(lo))}</span>
        <div class="wxf-bar">
          <div class="wxf-bar-fill"
               style="left:${left.toFixed(1)}%;width:${Math.max(8, width).toFixed(1)}%"></div>
        </div>
        <span class="wxf-hi">${escapeHtml(fmtTemp(hi))}</span>
        ${rainBlock}
      </div>`;
  }).join("");

  const titleBar = `
    <div class="w-title">
      <i class="ph-bold ph-calendar" style="color:var(--accent-4)"></i>
      <h3>${escapeHtml(label || "Forecast")}</h3>
      ${data.rangeHi != null && data.rangeLo != null
        ? `<span class="w-title-meta">${escapeHtml(fmtTemp(data.rangeHi))} / ${escapeHtml(fmtTemp(data.rangeLo))}</span>`
        : ""}
    </div>`;

  // Layout — vertical day stack as the base; a 2-column grid at lg
  // with the Chart.js trend on the right. xs strips back to the
  // essentials (day, icon, hi) so cramped cells don't try to fit a
  // range bar that would compress to nothing.
  const layout = `
    .wxf-body {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-3);
    }
    .wxf-rows {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
      justify-content: space-around;
      gap: var(--space-2);
    }
    .wxf-row {
      display: grid;
      grid-template-columns:
        minmax(3em, auto)   /* day label */
        1.6em                /* icon */
        minmax(2em, auto)    /* lo */
        minmax(0, 1fr)       /* range bar */
        minmax(2.2em, auto)  /* hi */
        minmax(2.8em, auto); /* rain */
      align-items: center;
      gap: var(--space-2) var(--space-3);
      padding: var(--space-1) var(--space-2);
      border-radius: var(--radius-0);
      min-width: 0;
    }
    .wxf-row.is-today {
      background: var(--surface-sunken);
    }
    .wxf-day {
      font-size: var(--fs-label);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .wxf-row.is-today .wxf-day {
      color: var(--accent-4);
      font-weight: var(--fw-black);
    }
    .wxf-icon {
      font-size: 1.4em;
      line-height: 1;
      color: var(--icon);
      justify-self: center;
    }
    .wxf-lo {
      font-size: var(--fs-body);
      font-weight: var(--fw-bold);
      color: var(--text-muted);
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    .wxf-bar {
      position: relative;
      height: var(--stroke-3);
      background: var(--surface-sunken);
      min-width: 1em;
    }
    .wxf-row.is-today .wxf-bar {
      background: var(--surface);
    }
    .wxf-bar-fill {
      position: absolute;
      top: 0;
      bottom: 0;
      background: linear-gradient(to right, var(--accent-5), var(--accent-2));
    }
    .wxf-hi {
      font-size: var(--fs-body);
      font-weight: var(--fw-black);
      color: var(--text-primary);
      text-align: left;
      font-variant-numeric: tabular-nums;
    }
    .wxf-rain {
      display: inline-flex;
      align-items: center;
      gap: 0.25em;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      color: var(--text-muted);
      font-variant-numeric: tabular-nums;
      justify-self: end;
    }
    .wxf-rain .ph-bold {
      font-size: 1.05em;
      color: var(--text-muted);
    }
    .wxf-rain.is-wet,
    .wxf-rain.is-wet .ph-bold {
      color: var(--accent-4);
    }
    .wxf-rain--unknown {
      visibility: hidden;
    }
    .wxf-chart {
      display: none;
    }

    /* lg: side-by-side day stack + Chart.js trend. The chart's column
       starts at 18em so the rows never get squeezed below their
       comfortable reading width, even on a wide-and-short cell. */
    @container (min-width: 700px) {
      .wxf-body {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(18em, 1fr);
        gap: var(--space-5);
      }
      .wxf-rows {
        gap: var(--space-3);
      }
      .wxf-row {
        padding: var(--space-2) var(--space-3);
        font-size: 1.05em;
      }
      .wxf-icon { font-size: 1.6em; }
      .wxf-chart {
        display: flex;
        position: relative;
        min-width: 0;
        min-height: 0;
        background: var(--surface-sunken);
        border-radius: var(--radius-0);
        padding: var(--space-3) var(--space-4);
      }
      .wxf-chart canvas {
        flex: 1 1 auto;
        max-width: 100%;
        max-height: 100%;
      }
    }

    /* xs: drop the range bar + rain so the row stays readable at
       180px wide. Just day, icon, hi — enough to scan tomorrow's
       sky and tomorrow's temperature without a sliver of bar that
       can't show anything useful. */
    @container (max-width: 280px) {
      .wxf-row {
        grid-template-columns: 1fr 1.6em auto;
      }
      .wxf-row .wxf-lo,
      .wxf-row .wxf-bar,
      .wxf-row .wxf-rain,
      .wxf-row .wxf-rain--unknown {
        display: none;
      }
    }
  `;

  // Chart at lg: high temperatures across the 5 days, with day labels
  // on the x-axis. Today's value is the first label so the chart's
  // leftmost point lines up conceptually with the topmost row.
  const labels = days.map((d) => d.day || "");
  const values = days.map((d) => Number(d.hi ?? d.high)).map((v) => Number.isFinite(v) ? v : null);
  const chartReady = values.filter((v) => v != null).length >= 2;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="weather_forecast">
      ${titleBar}
      <div class="w-body wxf-body">
        <div class="wxf-rows">${rows || '<p class="u-muted">No forecast.</p>'}</div>
        <div class="wxf-chart">
          ${chartReady ? '<canvas></canvas>' : ""}
        </div>
      </div>
    </div>`;

  if (chartReady) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    // Filled line painted in accent-1 (terracotta) — vibrant enough
    // to stand out against the muted bauhaus surfaces and reads as
    // "warm / temperature" without competing with the rain droplets
    // (accent-4 teal) or the per-row range gradient
    // (accent-5 → accent-2). weather_hourly uses accent-5 for its
    // temperature curve, so the two widgets stay distinguishable at
    // a glance even when stacked on the same dashboard.
    lineChart(canvas, {
      tokens: t,
      labels,
      values,
      color: t.accent1,
      fill: true,
      showY: true,
    });
  }
}
