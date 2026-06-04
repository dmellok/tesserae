// ha_history — single sensor → full Chart.js line chart with axes
// (current value + trend arrow + min/max in the chart legend strip).
// Multiple sensors → compact list with each row's current value.

import { lineChart, tokens } from "../../static/spectra-chart.js";

const TREND_ICON = {
  up: "ph-arrow-up-right",
  down: "ph-arrow-down-right",
  flat: "ph-arrow-right",
};

const TREND_ACCENT = {
  up: "var(--accent-3)",
  down: "var(--accent-1)",
  flat: "var(--text-secondary)",
};

const TREND_ACCENT_TOKEN = {
  up: "accent3",
  down: "accent1",
  flat: "textSecondary",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderSingle(item, hours) {
  const trendAccent = TREND_ACCENT[item.trend] || TREND_ACCENT.flat;
  const trendPh = TREND_ICON[item.trend] || TREND_ICON.flat;
  return `
    <div class="w-title">
      <i class="ph-bold ph-chart-line-up" style="color:${trendAccent}"></i>
      <h3>${escapeHtml(item.name)}</h3>
      <span class="w-title-meta">${hours}H</span>
    </div>
    <div class="w-body" style="gap:var(--space-2)">
      <div style="flex:1 1 auto;min-height:0;position:relative">
        ${Array.isArray(item.values) && item.values.length
          ? '<canvas></canvas>'
          : '<p class="u-muted">No samples in the window.</p>'}
      </div>
      <div class="chart-legend">
        <span class="chart-key u-spread" style="gap:var(--space-3)">
          <span style="font-weight:var(--fw-black);font-size:var(--fs-lead);color:var(--text-primary)">
            ${escapeHtml(item.current)}${item.unit ? `<small style="font-size:.6em;color:var(--text-muted);font-weight:var(--fw-bold)"> ${escapeHtml(item.unit)}</small>` : ""}
          </span>
          <i class="ph-bold ${trendPh}" style="color:${trendAccent};font-size:1em"></i>
        </span>
        <span class="chart-key"><span class="u-label">Low</span> ${escapeHtml(item.min || "—")}</span>
        <span class="chart-key"><span class="u-label">High</span> ${escapeHtml(item.max || "—")}</span>
      </div>
    </div>`;
}

function renderMulti(items, title, hours) {
  const rows = items.map((it, i) => {
    const accent = TREND_ACCENT[it.trend] || TREND_ACCENT.flat;
    const ph = TREND_ICON[it.trend] || TREND_ICON.flat;
    const unit = it.unit ? `<small style="font-size:.65em;color:var(--text-muted);font-weight:var(--fw-semi)"> ${escapeHtml(it.unit)}</small>` : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(it.name)}</span>
        </div>
        <span class="list-meta">${escapeHtml(it.current)}${unit}</span>
      </div>`;
  }).join("");
  return `
    <div class="w-title">
      <i class="ph-bold ph-chart-line-up" style="color:var(--accent-3)"></i>
      <h3>${escapeHtml(title)}</h3>
      <span class="w-title-meta">${hours}H</span>
    </div>
    <div class="w-body list-body">${rows}</div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_history">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>History</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const title = data.title || "History";
  const hours = data.hours || 24;

  if (data.empty || items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_history">
        <div class="w-title"><i class="ph-bold ph-chart-line-up"></i><h3>${escapeHtml(title)}</h3></div>
        <div class="w-body"><p class="u-muted">No sensors selected.</p></div>
      </div>`;
    return;
  }

  const body = items.length === 1 ? renderSingle(items[0], hours) : renderMulti(items, title, hours);
  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_history">${body}</div>`;

  if (items.length === 1) {
    const item = items[0];
    const canvas = shadow.querySelector("canvas");
    if (canvas && Array.isArray(item.values) && item.values.length >= 2) {
      const t = tokens(shadow.host);
      const accent = t[TREND_ACCENT_TOKEN[item.trend] || "accent3"];
      lineChart(canvas, {
        tokens: t,
        labels: item.values.map((_, i) => `${i + 1}`),
        values: item.values,
        color: accent,
      });
    }
  }
}
