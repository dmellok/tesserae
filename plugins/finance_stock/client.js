// finance_stock — single ticker with intraday chart (Chart.js with a
// dashed prev-close baseline), and a colour-blocked stat strip.

function loadChart() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (window.__tesseraeChartJs) return window.__tesseraeChartJs;
  window.__tesseraeChartJs = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = (window.TESSERAE_URL_PREFIX || "") + "/static/vendor/chart.umd.min.js";
    s.async = true;
    s.onload = () => resolve(window.Chart);
    s.onerror = () => reject(new Error("failed to load chart.js"));
    document.head.appendChild(s);
  });
  return window.__tesseraeChartJs;
}

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
function hexToRgba(hex, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return `rgba(0,0,0,${a})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
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
  const series = data.series || [];
  const high = series.length ? Math.max(...series) : null;
  const low = series.length ? Math.min(...series) : null;
  const absChange = (data.price != null && data.prev_close != null) ? (data.price - data.prev_close) : null;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/finance_stock/client.css">
    <div class="root size-${size} ${up ? 'is-up' : 'is-down'}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="fs-symbol">${escapeHtml(data.symbol)}</span>
        <span class="fs-exchange">${escapeHtml(data.exchange || "")}</span>
      </header>

      <section class="fs-hero">
        <div class="fs-hero-text">
          <div class="fs-name">${escapeHtml(data.name)}</div>
          <div class="fs-price">
            <span class="fs-curr">${escapeHtml(data.currency)}</span>
            <span class="fs-val">${fmtPrice(data.price)}</span>
          </div>
          <div class="fs-change">
            <i class="ph-bold ph-${up ? 'trend-up' : 'trend-down'}"></i>
            <span>${change != null ? (up ? "+" : "") + change.toFixed(2) + "%" : "—"}</span>
            <span class="fs-range-lbl">${escapeHtml(data.range || "1d")}</span>
          </div>
        </div>
        <div class="fs-hero-icon" aria-hidden="true">
          <i class="ph-bold ph-${up ? 'chart-line-up' : 'chart-line-down'}"></i>
        </div>
      </section>

      <section class="fs-chart">
        <canvas class="fs-canvas" data-fs-canvas></canvas>
      </section>

      <section class="fs-stats">
        <div class="fs-stat fs-stat--accent">
          <i class="ph-bold ph-arrow-up fs-stat-icon"></i>
          <span class="fs-stat-label">High</span>
          <span class="fs-stat-value">${fmtPrice(high)}</span>
        </div>
        <div class="fs-stat fs-stat--surface">
          <i class="ph-bold ph-arrow-down fs-stat-icon"></i>
          <span class="fs-stat-label">Low</span>
          <span class="fs-stat-value">${fmtPrice(low)}</span>
        </div>
        <div class="fs-stat fs-stat--accent2">
          <i class="ph-bold ph-flag fs-stat-icon"></i>
          <span class="fs-stat-label">Prev close</span>
          <span class="fs-stat-value">${fmtPrice(data.prev_close)}</span>
        </div>
        <div class="fs-stat fs-stat--accent3">
          <i class="ph-bold ph-${up ? 'trend-up' : 'trend-down'} fs-stat-icon"></i>
          <span class="fs-stat-label">Change</span>
          <span class="fs-stat-value">${absChange != null ? (up ? "+" : "") + absChange.toFixed(2) : "—"}</span>
        </div>
      </section>
    </div>
  `;

  if (size === "xs" || series.length < 2) return;
  const canvas = shadow.querySelector("[data-fs-canvas]");
  if (!canvas) return;
  try {
    const Chart = await loadChart();
    const t = ctx.theme;
    const color = up ? t.accent : t.accent3;
    new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: series.map((_, i) => i),
        datasets: [
          {
            data: series,
            borderColor: color,
            backgroundColor: hexToRgba(color, 0.18),
            borderWidth: 3,
            tension: 0.32,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false, grace: "5%" } },
        layout: { padding: 0 },
      },
      plugins: [
        {
          id: "fs-baseline",
          afterDatasetsDraw(chart) {
            if (data.prev_close == null) return;
            const ya = chart.scales.y;
            if (!ya) return;
            const y = ya.getPixelForValue(data.prev_close);
            const c2 = chart.ctx;
            c2.save();
            c2.strokeStyle = hexToRgba(t.fg, 0.45);
            c2.lineWidth = 1;
            c2.setLineDash([3, 4]);
            c2.beginPath();
            c2.moveTo(chart.chartArea.left, y);
            c2.lineTo(chart.chartArea.right, y);
            c2.stroke();
            c2.restore();
          },
        },
      ],
    });
  } catch { /* chart.js load failed — leave panel empty */ }
}
