// finance_stock — single ticker with sparkline.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function fmtPrice(v) {
  if (v == null) return "—";
  if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return v.toFixed(2);
}
function spark(series, w, h) {
  if (!series || series.length < 2) return "";
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const pts = series.map((v, i) => `${((i / (series.length - 1)) * w).toFixed(1)},${(h - (v - min) / range * h).toFixed(1)}`).join(" ");
  return `<polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/finance_stock/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const change = data.change_pct;
  const up = change != null && change >= 0;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/finance_stock/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="fs-symbol">${escapeHtml(data.symbol)}</span>
        <span class="fs-exchange">${escapeHtml(data.exchange || "")}</span>
      </header>
      <section class="fs-hero ${up ? 'fs-hero--up' : 'fs-hero--down'}">
        <div class="fs-name">${escapeHtml(data.name)}</div>
        <div class="fs-price">
          <span class="fs-curr">${escapeHtml(data.currency)}</span>
          <span class="fs-val">${fmtPrice(data.price)}</span>
        </div>
        <div class="fs-change">
          <i class="ph-bold ph-${up ? 'trend-up' : 'trend-down'}"></i>
          ${change != null ? change.toFixed(2) + "%" : "—"}
          <span class="fs-range-lbl">${escapeHtml(data.range)}</span>
        </div>
      </section>
      <section class="fs-spark">
        <svg viewBox="0 0 200 60" preserveAspectRatio="none" class="fs-svg ${up ? 'is-up' : 'is-down'}">
          ${spark(data.series, 200, 60)}
        </svg>
      </section>
    </div>
  `;
}
