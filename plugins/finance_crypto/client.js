// finance_crypto — single coin with 24h range. Bauhaus shell: header
// bar, big price hero, Chart.js line + area fill with open baseline,
// colour-blocked stat strip (24h high / low / market cap / range %).

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
function fmtPrice(v) {
  if (v == null) return "—";
  if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (v >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (v >= 0.01) return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return v.toLocaleString(undefined, { maximumFractionDigits: 6 });
}
function fmtCompact(v) {
  if (v == null) return "—";
  if (v >= 1e12) return (v / 1e12).toFixed(2) + "T";
  if (v >= 1e9)  return (v / 1e9).toFixed(2)  + "B";
  if (v >= 1e6)  return (v / 1e6).toFixed(2)  + "M";
  if (v >= 1e3)  return (v / 1e3).toFixed(1)  + "K";
  return String(v);
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
      <link rel="stylesheet" href="/plugins/finance_crypto/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const change = data.change_24h;
  const up = change != null && change >= 0;
  const sym = (data.vs || "usd").toUpperCase();
  const coin = (data.coin || "").toUpperCase();

  const series = data.series || [];
  const high = series.length ? Math.max(...series) : null;
  const low = series.length ? Math.min(...series) : null;
  const open = series.length ? series[0] : null;
  const rangePct = (high != null && low != null && open) ? ((high - low) / open) * 100 : null;

  // Asset icon picker — Phosphor has named symbols for the big three;
  // anything else falls back to a generic coin glyph.
  const iconMap = { BTC: "ph-currency-btc", ETH: "ph-currency-eth", "BITCOIN": "ph-currency-btc", "ETHEREUM": "ph-currency-eth" };
  const heroIcon = iconMap[coin] || "ph-coin-vertical";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/finance_crypto/client.css">
    <div class="root size-${size} ${up ? 'is-up' : 'is-down'}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="fc-coin">${escapeHtml(coin)} / ${escapeHtml(sym)}</span>
        <i class="ph-bold ${heroIcon} wb-bar-icon"></i>
      </header>

      <section class="fc-hero">
        <div class="fc-hero-text">
          <div class="fc-price">
            <span class="fc-curr">${escapeHtml(sym)}</span>
            <span class="fc-val">${fmtPrice(data.price)}</span>
          </div>
          <div class="fc-change">
            <i class="ph-bold ph-${up ? 'trend-up' : 'trend-down'}"></i>
            <span>${change != null ? (up ? "+" : "") + change.toFixed(2) + "%" : "—"}</span>
            <span class="fc-change-lbl">24h</span>
          </div>
        </div>
        <div class="fc-hero-icon" aria-hidden="true">
          <i class="ph-bold ${heroIcon}"></i>
        </div>
      </section>

      <section class="fc-chart">
        <canvas class="fc-canvas" data-fc-canvas></canvas>
      </section>

      <section class="fc-stats">
        <div class="fc-stat fc-stat--accent">
          <i class="ph-bold ph-arrow-up fc-stat-icon"></i>
          <span class="fc-stat-label">24h high</span>
          <span class="fc-stat-value">${fmtPrice(high)}</span>
        </div>
        <div class="fc-stat fc-stat--surface">
          <i class="ph-bold ph-arrow-down fc-stat-icon"></i>
          <span class="fc-stat-label">24h low</span>
          <span class="fc-stat-value">${fmtPrice(low)}</span>
        </div>
        <div class="fc-stat fc-stat--accent2">
          <i class="ph-bold ph-bank fc-stat-icon"></i>
          <span class="fc-stat-label">Mkt cap</span>
          <span class="fc-stat-value">${fmtCompact(data.market_cap)}</span>
        </div>
        <div class="fc-stat fc-stat--accent3">
          <i class="ph-bold ph-arrows-out-line-vertical fc-stat-icon"></i>
          <span class="fc-stat-label">Range</span>
          <span class="fc-stat-value">${rangePct != null ? rangePct.toFixed(2) + "%" : "—"}</span>
        </div>
      </section>
    </div>
  `;

  // Mount Chart.js if there's a chart panel + enough points to draw.
  // xs collapses the chart panel via CSS, so we skip mounting then.
  if (size === "xs" || series.length < 2) return;
  const canvas = shadow.querySelector("[data-fc-canvas]");
  if (!canvas) return;
  try {
    const Chart = await loadChart();
    const t = ctx.theme;
    const color = up ? t.accent : t.accent3;
    const fontFamily = ctx.font?.family || 'system-ui, sans-serif';
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
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
          // Dashed open-price reference line.
          annotation: undefined,
        },
        scales: {
          x: { display: false },
          y: {
            display: false,
            grace: "5%",
          },
        },
        layout: { padding: 0 },
        elements: { line: { capBezierPoints: true } },
      },
      plugins: [
        // Custom plugin: dashed baseline at the open price.
        {
          id: "fc-baseline",
          afterDatasetsDraw(chart) {
            if (open == null) return;
            const ya = chart.scales.y;
            if (!ya) return;
            const y = ya.getPixelForValue(open);
            const ctx2 = chart.ctx;
            ctx2.save();
            ctx2.strokeStyle = hexToRgba(t.fg, 0.45);
            ctx2.lineWidth = 1;
            ctx2.setLineDash([3, 4]);
            ctx2.beginPath();
            ctx2.moveTo(chart.chartArea.left, y);
            ctx2.lineTo(chart.chartArea.right, y);
            ctx2.stroke();
            ctx2.restore();
          },
        },
      ],
    });
    void fontFamily; // currently unused — kept for future axis labels
  } catch {
    // Chart.js failed to load — leave the panel empty rather than 500.
  }
}
