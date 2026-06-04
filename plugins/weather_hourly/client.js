// weather_hourly — Spectra chart archetype with a condition-icon
// strip above a Chart.js bar chart. Each hour shows its weather
// icon (sun / cloud-rain / snowflake …) coloured by category, then
// the bar below shows the temperature. Current hour highlighted in
// accent-1 so the eye lands on "now" first.
//
// Server provides a 24-slot structured pair (data.hoursArr +
// data.temps) the new variants paint from; falls back to data.points
// when the older shape is what's cached.

import { barChart, tokens } from "../../static/spectra-chart.js";

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

// Same condition palette as weather_now / weather_forecast so a
// multi-widget weather dashboard reads consistently.
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
      <div class="w" data-widget="weather_hourly">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Hourly</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.place || data.label || "";

  // Prefer the structured pair (hoursArr + temps) the server pre-
  // computes for variant widgets — labels + icons + values come from
  // a single coherent 24-slot window. Falls back to data.points for
  // older cached payloads.
  const hoursArr = Array.isArray(data.hoursArr) ? data.hoursArr : [];
  const tempsArr = Array.isArray(data.temps) ? data.temps : [];
  const points = Array.isArray(data.points) ? data.points : [];

  let labels = [];
  let values = [];
  let icons = [];
  if (hoursArr.length && tempsArr.length) {
    const n = Math.min(hoursArr.length, tempsArr.length);
    labels = hoursArr.slice(0, n).map((h) => h.t || "");
    icons = hoursArr.slice(0, n).map((h) => h.icon || "");
    values = tempsArr.slice(0, n).map((v) => Number(v));
  } else if (points.length) {
    labels = points.map((h) => h.hour ?? "");
    icons = points.map(() => "");
    values = points.map((h) => {
      const v = Number(h.temp);
      return Number.isNaN(v) ? 0 : v;
    });
  }

  const hasData = values.length >= 2;

  const iconStrip = hasData
    ? `
      <div style="display:grid;grid-template-columns:repeat(${values.length}, 1fr);justify-items:center;align-items:center;font-size:.9em;line-height:1">
        ${icons.map((name, i) => {
          if (!name) return `<span></span>`;
          const ph = PH_BY_NAME[name] || "ph-cloud";
          const accent = COND_ACCENT[name] || "var(--text-secondary)";
          const isNow = i === 0;
          return `<i class="ph-bold ${ph}" style="color:${isNow ? "var(--accent-1)" : accent}" title="${escapeHtml(name)}"></i>`;
        }).join("")}
      </div>`
    : "";

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="weather_hourly">
      <div class="w-title">
        <i class="ph-bold ph-clock" style="color:var(--accent-4)"></i>
        <h3>${escapeHtml(label || "Hourly")}</h3>
        ${data.now != null ? `<span class="w-title-meta">${escapeHtml(fmtTemp(data.now))} NOW</span>` : ""}
      </div>
      <div class="w-body" style="gap:var(--space-2)">
        ${iconStrip}
        <div style="flex:1 1 auto;min-height:0;position:relative">
          ${hasData ? '<canvas></canvas>' : '<p class="u-muted">No hourly data.</p>'}
        </div>
        <div class="chart-legend">
          <span class="chart-key"><span class="dot" style="background:var(--accent-5)"></span>Forecast</span>
          <span class="chart-key"><span class="dot" style="background:var(--accent-1)"></span>Now</span>
        </div>
      </div>
    </div>`;

  if (hasData) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    barChart(canvas, {
      tokens: t,
      labels,
      values,
      color: t.accent5,
      highlightColor: t.accent1,
      highlightIdx: 0,
      showY: false,
    });
  }
}
