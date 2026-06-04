// finance_crypto — Spectra stat archetype with a Chart.js sparkline.
// Hero is the price; the delta carries the 24h % change (accent-3
// up, accent-1 down) and the market cap goes in the title meta.
// A Chart.js line of the 24h price series sits below the caption so
// the tile carries a sense of momentum at a glance.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtPrice(n, vs) {
  if (n == null) return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  const sym = (vs === "usd" ? "$" : vs === "eur" ? "€" : vs === "gbp" ? "£" : "");
  if (v >= 10000) return `${sym}${Math.round(v).toLocaleString()}`;
  if (v >= 1) return `${sym}${v.toFixed(2)}`;
  if (v >= 0.01) return `${sym}${v.toFixed(4)}`;
  return `${sym}${v.toExponential(2)}`;
}

function fmtMarketCap(n) {
  if (n == null) return "";
  const v = Number(n);
  if (Number.isNaN(v) || v <= 0) return "";
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  return v.toLocaleString();
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="finance_crypto">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Crypto</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const coin = String(data.coin || "—").toUpperCase();
  const vs = String(data.vs || "usd").toLowerCase();
  const price = fmtPrice(data.price, vs);
  const change = data.change_24h;
  const cap = fmtMarketCap(data.market_cap);

  const up = change != null && change >= 0;
  const deltaAccent = up ? "var(--accent-3)" : "var(--accent-1)";
  const deltaPh = up ? "ph-trend-up" : "ph-trend-down";
  const deltaText = change == null ? "—" : `${Math.abs(change).toFixed(2)}%`;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="finance_crypto">
      <div class="w-title">
        <i class="ph-bold ph-coin" style="color:var(--accent-2)"></i>
        <h3>${escapeHtml(coin)}</h3>
        ${cap ? `<span class="w-title-meta">${escapeHtml(cap)} CAP</span>` : ""}
      </div>
      <div class="w-body stat-body">
        <div class="stat-value">${escapeHtml(price)}<span class="unit">${escapeHtml(vs.toUpperCase())}</span></div>
        <div class="stat-caption u-row">
          <span class="stat-delta" style="color:${deltaAccent}"><i class="ph-bold ${deltaPh}"></i>${escapeHtml(deltaText)}</span>
          <span class="u-muted">24h</span>
        </div>
        <div class="stat-sparkline"><canvas></canvas></div>
      </div>
    </div>`;

  const canvas = shadow.querySelector("canvas");
  const t = tokens(shadow.host);
  const lineColor = up ? t.accent3 : t.accent1;
  sparkline(canvas, data.series, lineColor);
}
