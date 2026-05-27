// finance_currency — single FX pair with 30-day chart (Chart.js,
// dashed open baseline), and a colour-blocked stat strip (30d high /
// low / open / range %).

function loadChart() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (window.__tesseraeChartJs) return window.__tesseraeChartJs;
  window.__tesseraeChartJs = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/chart.umd.min.js";
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
function fmtRate(v, digits = 4) {
  return v == null ? "—" : v.toFixed(digits);
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
      <link rel="stylesheet" href="/plugins/finance_currency/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const change = data.change_30d;
  const up = (change ?? 0) >= 0;
  const series = data.series || [];
  const high = series.length ? Math.max(...series) : null;
  const low = series.length ? Math.min(...series) : null;
  const open = series.length ? series[0] : null;
  const rangePct = (high != null && low != null && open) ? ((high - low) / open) * 100 : null;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/finance_currency/client.css">
    <div class="root size-${size} ${up ? 'is-up' : 'is-down'}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="fx-pair">${escapeHtml(data.base)}<span class="fx-slash">/</span>${escapeHtml(data.quote)}</span>
        <span class="fx-asof">${escapeHtml(data.as_of || "—")}</span>
      </header>

      <section class="fx-hero">
        <div class="fx-hero-text">
          <div class="fx-rate">${fmtRate(data.rate)}</div>
          <div class="fx-change">
            <i class="ph-bold ph-${up ? 'trend-up' : 'trend-down'}"></i>
            <span>${change != null ? (up ? "+" : "") + change.toFixed(2) + "%" : "—"}</span>
            <span class="fx-change-lbl">30d</span>
          </div>
        </div>
        <div class="fx-hero-icon" aria-hidden="true">
          <i class="ph-bold ph-currency-circle-dollar"></i>
        </div>
      </section>

      <section class="fx-chart">
        <canvas class="fx-canvas" data-fx-canvas></canvas>
      </section>

      <section class="fx-stats">
        <div class="fx-stat fx-stat--accent">
          <i class="ph-bold ph-arrow-up fx-stat-icon"></i>
          <span class="fx-stat-label">30d high</span>
          <span class="fx-stat-value">${fmtRate(high)}</span>
        </div>
        <div class="fx-stat fx-stat--surface">
          <i class="ph-bold ph-arrow-down fx-stat-icon"></i>
          <span class="fx-stat-label">30d low</span>
          <span class="fx-stat-value">${fmtRate(low)}</span>
        </div>
        <div class="fx-stat fx-stat--accent2">
          <i class="ph-bold ph-flag fx-stat-icon"></i>
          <span class="fx-stat-label">Open</span>
          <span class="fx-stat-value">${fmtRate(open)}</span>
        </div>
        <div class="fx-stat fx-stat--accent3">
          <i class="ph-bold ph-arrows-out-line-vertical fx-stat-icon"></i>
          <span class="fx-stat-label">Range</span>
          <span class="fx-stat-value">${rangePct != null ? rangePct.toFixed(2) + "%" : "—"}</span>
        </div>
      </section>
    </div>
  `;

  if (size === "xs" || series.length < 2) return;
  const canvas = shadow.querySelector("[data-fx-canvas]");
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
          id: "fx-baseline",
          afterDatasetsDraw(chart) {
            if (open == null) return;
            const ya = chart.scales.y;
            if (!ya) return;
            const y = ya.getPixelForValue(open);
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
