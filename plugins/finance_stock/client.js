// finance_stock, Spectra stat archetype. Hero is the latest price;
// the 24h delta wears an up/down badge with chunky arrow + percent.
// A day-range track sits below showing the session's low → high
// with the current price marked. Volume bars stack underneath the
// price sparkline as a thin secondary band (when Yahoo serves them).

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtPrice(n, ccy) {
  if (n == null) return "-";
  const v = Number(n);
  if (Number.isNaN(v)) return "-";
  const sym = (ccy === "USD" ? "$" : ccy === "EUR" ? "€" : ccy === "GBP" ? "£" : ccy === "JPY" ? "¥" : "");
  if (v >= 1000) return `${sym}${v.toFixed(2)}`;
  if (v >= 1) return `${sym}${v.toFixed(2)}`;
  return `${sym}${v.toFixed(4)}`;
}

function fmtRaw(n) {
  if (n == null) return "-";
  const v = Number(n);
  if (Number.isNaN(v)) return "-";
  return v >= 100 ? v.toFixed(2) : v.toFixed(2);
}

// Day-range bar, a horizontal track with low on the left, high on
// the right, and a marker pip at the current price's position.
function dayRangeBar({ low, high, price, accent }) {
  if (!Number.isFinite(low) || !Number.isFinite(high) || !Number.isFinite(price)) return "";
  if (high - low < 0.0001) return "";
  const pct = Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100));
  return `
    <div class="stock-range">
      <span class="stock-range-end">${escapeHtml(fmtRaw(low))}</span>
      <div class="stock-range-track">
        <div class="stock-range-fill" style="width:${pct.toFixed(1)}%;background:${accent}"></div>
        <div class="stock-range-pip" style="left:${pct.toFixed(1)}%;background:${accent}"></div>
      </div>
      <span class="stock-range-end">${escapeHtml(fmtRaw(high))}</span>
    </div>`;
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

  const symbol = data.symbol || "-";
  const name = data.name || "";
  const ccy = (data.currency || "USD").toUpperCase();
  const exchange = data.exchange || "";
  const rng = data.range || "";
  const price = fmtPrice(data.price, ccy);
  const change = data.change_pct;

  const up = change != null && change >= 0;
  const deltaAccent = up ? "var(--accent-3)" : "var(--accent-1)";
  const deltaPh = up ? "ph-trend-up" : "ph-trend-down";
  const deltaText = change == null ? "-" : `${Math.abs(change).toFixed(2)}%`;

  const metaBits = [exchange, rng].filter(Boolean).join(" · ");
  const t = tokens(shadow.host);

  const rangeBar = dayRangeBar({
    low: Number(data.day_low),
    high: Number(data.day_high),
    price: Number(data.price),
    accent: deltaAccent,
  });

  const layout = `
    .stock-delta {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 1px var(--space-2);
      border-radius: 999px;
      background: color-mix(in oklab, ${deltaAccent} 14%, var(--surface));
      color: ${deltaAccent};
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      font-size: var(--fs-caption);
    }
    .stock-name {
      color: var(--text-muted);
      font-size: var(--fs-caption);
      font-weight: var(--fw-semi);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1 1 auto;
      min-width: 0;
    }
    .stock-range {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      color: var(--text-muted);
    }
    .stock-range-track {
      flex: 1 1 auto;
      position: relative;
      height: 6px;
      border-radius: 3px;
      background: color-mix(in oklab, var(--text-primary) 6%, transparent);
      overflow: visible;
    }
    .stock-range-fill {
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      border-radius: 3px;
      opacity: 0.4;
    }
    .stock-range-pip {
      position: absolute;
      top: -3px;
      bottom: -3px;
      width: 4px;
      border-radius: 2px;
      transform: translateX(-50%);
      box-shadow: 0 0 0 1.5px var(--surface);
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="finance_stock">
      <div class="w-title">
        <i class="ph-bold ph-chart-line" style="color:${deltaAccent}"></i>
        <h3>${escapeHtml(symbol)}</h3>
        ${metaBits ? `<span class="w-title-meta">${escapeHtml(metaBits)}</span>` : ""}
      </div>
      <div class="w-body stat-body">
        <div class="stat-value">${escapeHtml(price)}<span class="unit">${escapeHtml(ccy)}</span></div>
        <div class="stat-caption" style="display:flex;align-items:center;gap:var(--space-2)">
          <span class="stock-delta"><i class="ph-bold ${deltaPh}"></i>${escapeHtml(deltaText)}</span>
          <span class="stock-name">${escapeHtml(name)}</span>
        </div>
        ${rangeBar}
        <div class="stat-sparkline"><canvas></canvas></div>
      </div>
    </div>`;

  const canvas = shadow.querySelector("canvas");
  const lineColor = up ? t.accent3 : t.accent1;
  sparkline(canvas, data.series, lineColor);
}
