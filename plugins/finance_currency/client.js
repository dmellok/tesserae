// finance_currency, Spectra stat archetype. Hero is the exchange
// rate; the title carries a flag pair (🇦🇺/🇺🇸) for instant pair
// recognition. Sparkline uses Chart.js (`sparkline()` helper) and
// overlays a 7-day rolling average dashed line on top of the daily
// rate so trend reads through the noise.

import { sparkline, tokens } from "../../static/spectra-chart.js";

// ISO 4217 → Unicode flag (regional-indicator letters). Same trick
// every currency-pair indicator uses: take the first two letters of
// the ISO code and shift to regional-indicator codepoints.
const CURRENCY_TO_COUNTRY = {
  USD: "US", EUR: "EU", GBP: "GB", JPY: "JP", AUD: "AU", NZD: "NZ",
  CAD: "CA", CHF: "CH", CNY: "CN", HKD: "HK", SGD: "SG", KRW: "KR",
  INR: "IN", BRL: "BR", MXN: "MX", ZAR: "ZA", SEK: "SE", NOK: "NO",
  DKK: "DK", PLN: "PL", TRY: "TR", THB: "TH", MYR: "MY", PHP: "PH",
  IDR: "ID", ILS: "IL", CZK: "CZ", HUF: "HU", RON: "RO", ISK: "IS",
  BGN: "BG", HRK: "HR", AED: "AE", SAR: "SA", VND: "VN", TWD: "TW",
};

function flagFor(code) {
  const country = CURRENCY_TO_COUNTRY[code];
  if (!country) return "";
  // EU = 🇪🇺, same regional-indicator trick.
  return String.fromCodePoint(...[...country].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtRate(n) {
  if (n == null) return "-";
  const v = Number(n);
  if (Number.isNaN(v)) return "-";
  if (v >= 100) return v.toFixed(2);
  if (v >= 1) return v.toFixed(4);
  return v.toFixed(6);
}

// 7-day simple moving average over a series. Handles edge windows
// by averaging the available samples (so the line extends across
// the full chart instead of starting 6 days in).
function rollingAverage(series, window = 7) {
  const out = [];
  for (let i = 0; i < series.length; i++) {
    const lo = Math.max(0, i - Math.floor(window / 2));
    const hi = Math.min(series.length, lo + window);
    let sum = 0;
    let count = 0;
    for (let j = lo; j < hi; j++) {
      if (Number.isFinite(series[j])) {
        sum += series[j];
        count++;
      }
    }
    out.push(count > 0 ? sum / count : 0);
  }
  return out;
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

  const base = String(data.base || "-").toUpperCase();
  const quote = String(data.quote || "-").toUpperCase();
  const rate = fmtRate(data.rate);
  const change = data.change_30d;
  const asOf = data.as_of || "";
  const series = Array.isArray(data.series) ? data.series : [];

  const up = change != null && change >= 0;
  const deltaAccent = up ? "var(--accent-3)" : "var(--accent-1)";
  const deltaPh = up ? "ph-trend-up" : "ph-trend-down";
  const deltaText = change == null ? "-" : `${Math.abs(change).toFixed(2)}%`;

  const baseFlag = flagFor(base);
  const quoteFlag = flagFor(quote);
  const t = tokens(shadow.host);
  const lineColor = up ? t.accent3 : t.accent1;

  const layout = `
    .fx-flags {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      font-size: 1.05em;
      margin-right: var(--space-1);
    }
    .fx-flags .fx-slash {
      color: var(--text-muted);
      font-weight: var(--fw-bold);
      font-size: .9em;
    }
    .fx-delta {
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
    .fx-caption {
      display: flex;
      align-items: center;
      gap: var(--space-2);
    }
    .fx-window {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      color: var(--text-muted);
    }
  `;

  const flagPair = baseFlag && quoteFlag
    ? `<span class="fx-flags">${baseFlag}<span class="fx-slash">/</span>${quoteFlag}</span>`
    : "";

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="finance_currency">
      <div class="w-title">
        ${flagPair || `<i class="ph-bold ph-currency-circle-dollar" style="color:var(--accent-5)"></i>`}
        <h3>${escapeHtml(base)}/${escapeHtml(quote)}</h3>
        ${asOf ? `<span class="w-title-meta">${escapeHtml(asOf)}</span>` : ""}
      </div>
      <div class="w-body stat-body">
        <div class="stat-value">${escapeHtml(rate)}<span class="unit">${escapeHtml(quote)}</span></div>
        <div class="stat-caption fx-caption">
          <span class="fx-delta"><i class="ph-bold ${deltaPh}"></i>${escapeHtml(deltaText)}</span>
          <span class="fx-window">30d</span>
        </div>
        <div class="stat-sparkline"><canvas></canvas></div>
      </div>
    </div>`;

  const canvas = shadow.querySelector("canvas");
  if (canvas && series.length >= 2) {
    sparkline(canvas, series, {
      color: lineColor,
      overlay: { values: rollingAverage(series, 7), color: lineColor, dash: [4, 4] },
    });
  }
}
