// finance_currency — FX pair with 30-day spark.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
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
      <link rel="stylesheet" href="/plugins/finance_currency/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const up = (data.change_30d ?? 0) >= 0;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/finance_currency/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="fx-pair">${escapeHtml(data.base)}<span class="fx-slash">/</span>${escapeHtml(data.quote)}</span>
        <span class="fx-asof">${escapeHtml(data.as_of || "—")}</span>
      </header>
      <section class="fx-hero ${up ? 'fx-hero--up' : 'fx-hero--down'}">
        <div class="fx-rate">${data.rate ? data.rate.toFixed(4) : "—"}</div>
        <div class="fx-change">
          <i class="ph-bold ph-${up ? 'trend-up' : 'trend-down'}"></i>
          ${data.change_30d != null ? data.change_30d.toFixed(2) + "%" : "—"}
          <span class="fx-change-lbl">30d</span>
        </div>
      </section>
      <section class="fx-spark">
        <svg viewBox="0 0 200 60" preserveAspectRatio="none" class="fx-svg ${up ? 'is-up' : 'is-down'}">
          ${spark(data.series, 200, 60)}
        </svg>
      </section>
    </div>
  `;
}
