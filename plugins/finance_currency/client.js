// finance_currency — Spectra stat archetype with a Chart.js sparkline.
// Hero is the exchange rate; delta carries the 30-day % change and
// the "as of" date sits in the title meta.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtRate(n) {
  if (n == null) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  if (v >= 100) return v.toFixed(2);
  if (v >= 1) return v.toFixed(4);
  return v.toFixed(6);
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="finance_currency">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>FX</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const base = String(data.base || "—").toUpperCase();
  const quote = String(data.quote || "—").toUpperCase();
  const rate = fmtRate(data.rate);
  const change = data.change_30d;
  const asOf = data.as_of || "";

  const up = change != null && change >= 0;
  const deltaAccent = up ? "var(--accent-3)" : "var(--accent-1)";
  const deltaPh = up ? "ph-trend-up" : "ph-trend-down";
  const deltaText = change == null ? "—" : `${Math.abs(change).toFixed(2)}%`;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="finance_currency">
      <div class="w-title">
        <i class="ph-bold ph-currency-circle-dollar" style="color:var(--accent-5)"></i>
        <h3>${escapeHtml(base)}/${escapeHtml(quote)}</h3>
        ${asOf ? `<span class="w-title-meta">${escapeHtml(asOf)}</span>` : ""}
      </div>
      <div class="w-body stat-body">
        <div class="stat-value">${escapeHtml(rate)}<span class="unit">${escapeHtml(quote)}</span></div>
        <div class="stat-caption u-row">
          <span class="stat-delta" style="color:${deltaAccent}"><i class="ph-bold ${deltaPh}"></i>${escapeHtml(deltaText)}</span>
          <span class="u-muted">30d</span>
        </div>
        <div class="stat-sparkline"><canvas></canvas></div>
      </div>
    </div>`;

  const canvas = shadow.querySelector("canvas");
  const t = tokens(shadow.host);
  const lineColor = up ? t.accent3 : t.accent1;
  sparkline(canvas, data.series, lineColor);
}
