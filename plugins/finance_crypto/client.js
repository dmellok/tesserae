// finance_crypto — Spectra stat archetype with a sparkline rail.
// Hero is the price; the delta carries the 24h % change (accent-3
// up, accent-1 down) and the market cap goes in the title meta.
// A tiny SVG sparkline of the 24h price series sits below the
// caption so the stat tile carries a sense of momentum at a glance.

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

// Build an SVG polyline from the series values. The viewBox is
// 100x30, scaled to fit by the parent. Stroke uses the delta colour
// so the line carries the same momentum signal as the % chip.
function sparkline(values, accent) {
  if (!Array.isArray(values) || values.length < 2) return "";
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const range = Math.max(0.0001, hi - lo);
  const w = 100, h = 30;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - ((v - lo) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <polyline points="${pts}" fill="none" stroke="${accent}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
    </svg>`;
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
        <div class="stat-sparkline">${sparkline(data.series, deltaAccent)}</div>
      </div>
    </div>`;
}
