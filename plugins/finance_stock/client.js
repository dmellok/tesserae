// finance_stock — Spectra stat archetype with a Chart.js sparkline.
// Hero is the latest price; delta carries the change vs previous
// close (accent-3 up, accent-1 down). Title bar shows the ticker
// with the exchange + range as meta.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtPrice(n, ccy) {
  if (n == null) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  const sym = (ccy === "USD" ? "$" : ccy === "EUR" ? "€" : ccy === "GBP" ? "£" : ccy === "JPY" ? "¥" : "");
  if (v >= 1000) return `${sym}${v.toFixed(2)}`;
  if (v >= 1) return `${sym}${v.toFixed(2)}`;
  return `${sym}${v.toFixed(4)}`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="finance_stock">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Stock</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const symbol = data.symbol || "—";
  const name = data.name || "";
  const ccy = (data.currency || "USD").toUpperCase();
  const exchange = data.exchange || "";
  const rng = data.range || "";
  const price = fmtPrice(data.price, ccy);
  const change = data.change_pct;

  const up = change != null && change >= 0;
  const deltaAccent = up ? "var(--accent-3)" : "var(--accent-1)";
  const deltaPh = up ? "ph-trend-up" : "ph-trend-down";
  const deltaText = change == null ? "—" : `${Math.abs(change).toFixed(2)}%`;

  const metaBits = [exchange, rng].filter(Boolean).join(" · ");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="finance_stock">
      <div class="w-title">
        <i class="ph-bold ph-chart-line" style="color:var(--accent-5)"></i>
        <h3>${escapeHtml(symbol)}</h3>
        ${metaBits ? `<span class="w-title-meta">${escapeHtml(metaBits)}</span>` : ""}
      </div>
      <div class="w-body stat-body">
        <div class="stat-value">${escapeHtml(price)}<span class="unit">${escapeHtml(ccy)}</span></div>
        <div class="stat-caption u-row">
          <span class="stat-delta" style="color:${deltaAccent}"><i class="ph-bold ${deltaPh}"></i>${escapeHtml(deltaText)}</span>
          <span class="u-muted">${escapeHtml(name)}</span>
        </div>
        <div class="stat-sparkline"><canvas></canvas></div>
      </div>
    </div>`;

  const canvas = shadow.querySelector("canvas");
  const t = tokens(shadow.host);
  const lineColor = up ? t.accent3 : t.accent1;
  sparkline(canvas, data.series, lineColor);
}
