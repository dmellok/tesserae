// weather_hourly — Spectra chart archetype, hourly temperature, now
// rendered as a Chart.js bar chart so it benefits from the same
// e-ink-tuned axis styling as the other charted widgets. The
// current hour (index 0 in the trimmed window) is highlighted with
// accent-1 so the eye lands on "now" first.

import { barChart, tokens } from "../../static/spectra-chart.js";

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
  const hours = Array.isArray(data.hours) ? data.hours : [];

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="weather_hourly">
      <div class="w-title">
        <i class="ph-bold ph-clock" style="color:var(--accent-4)"></i>
        <h3>${escapeHtml(label || "Hourly")}</h3>
        ${data.now != null ? `<span class="w-title-meta">${escapeHtml(fmtTemp(data.now))} NOW</span>` : ""}
      </div>
      <div class="w-body" style="gap:var(--space-2)">
        <div style="flex:1 1 auto;min-height:0;position:relative">
          ${hours.length ? '<canvas></canvas>' : '<p class="u-muted">No hourly data.</p>'}
        </div>
        <div class="chart-legend">
          <span class="chart-key"><span class="dot" style="background:var(--accent-5)"></span>Forecast</span>
          <span class="chart-key"><span class="dot" style="background:var(--accent-1)"></span>Now</span>
        </div>
      </div>
    </div>`;

  if (hours.length) {
    const canvas = shadow.querySelector("canvas");
    const t = tokens(shadow.host);
    barChart(canvas, {
      tokens: t,
      labels: hours.map((h) => h.hour ?? ""),
      values: hours.map((h) => {
        const v = Number(h.temp);
        return Number.isNaN(v) ? 0 : v;
      }),
      color: t.accent5,
      highlightColor: t.accent1,
      highlightIdx: 0,
      showY: false,
    });
  }
}
