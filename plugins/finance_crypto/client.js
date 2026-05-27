// finance_crypto — single coin, 24h spark.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function fmtPrice(v) {
  if (v == null) return "—";
  if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (v >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (v >= 0.01) return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return v.toLocaleString(undefined, { maximumFractionDigits: 6 });
}
function spark(series, w, h) {
  if (!series || series.length < 2) return "";
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const points = series.map((v, i) => {
    const x = (i / (series.length - 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/plugins/finance_crypto/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const change = data.change_24h;
  const up = change != null && change >= 0;
  const sym = (data.vs || "usd").toUpperCase();

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/finance_crypto/client.css">
    <div class="root size-${size}">
      <header class="fc-bar">
        <span class="fc-mark" aria-hidden="true"></span>
        <span class="fc-coin">${escapeHtml(data.coin.toUpperCase())} / ${escapeHtml(sym)}</span>
        <i class="ph-bold ph-currency-btc fc-bar-icon"></i>
      </header>
      <section class="fc-hero ${up ? 'fc-hero--up' : 'fc-hero--down'}">
        <div class="fc-price">
          <span class="fc-curr">${escapeHtml(sym)}</span>
          <span class="fc-val">${fmtPrice(data.price)}</span>
        </div>
        <div class="fc-change">
          <i class="ph-bold ph-${up ? 'trend-up' : 'trend-down'}"></i>
          ${change != null ? change.toFixed(2) + "%" : "—"}
          <span class="fc-change-lbl">24h</span>
        </div>
      </section>
      <section class="fc-spark">
        <svg viewBox="0 0 200 60" preserveAspectRatio="none" class="fc-svg ${up ? 'is-up' : 'is-down'}">
          ${spark(data.series, 200, 60)}
        </svg>
      </section>
    </div>
  `;
}
