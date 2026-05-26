// weather_hourly — Bauhaus hourly card. Inverted header strip with
// place + window label + HI/LO chips on the right, Chart.js line on
// the surface, rain probability blocks at the bottom (md/lg only).

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

function hexToRgba(hex, alpha) {
  if (typeof hex !== "string" || !/^#[0-9a-f]{6}$/i.test(hex)) return `rgba(0,0,0,${alpha})`;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/plugins/weather_hourly/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

function labelEvery(points, size) {
  const target = size === "sm" ? 4 : size === "md" ? 6 : 8;
  return Math.max(1, Math.ceil(points.length / target));
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }
  const points = Array.isArray(data.points) ? data.points : [];
  if (!points.length) {
    shadow.innerHTML = renderError("no hourly data");
    return;
  }

  const size = ctx.cell.size;
  const showRain = size === "md" || size === "lg";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_hourly/client.css">
    <div class="root size-${size}">
      <header class="wh-bar">
        <span class="wh-mark" aria-hidden="true"></span>
        <span class="wh-title">${data.label ? escapeHtml(data.label) + " · " : ""}Next ${data.hours || 24} hr</span>
        <span class="wh-time">${nowTime()}</span>
      </header>
      <section class="wh-chips">
        <div class="wh-chip wh-chip--high">
          <span class="wh-chip-label">High</span>
          <span class="wh-chip-value">${fmtTemp(data.max)}</span>
        </div>
        <div class="wh-chip wh-chip--low">
          <span class="wh-chip-label">Low</span>
          <span class="wh-chip-value">${fmtTemp(data.min)}</span>
        </div>
        ${data.current != null ? `
        <div class="wh-chip wh-chip--current">
          <span class="wh-chip-label">Now</span>
          <span class="wh-chip-value">${fmtTemp(data.current)}</span>
        </div>` : ""}
      </section>
      <section class="wh-chart">
        <canvas class="chart"></canvas>
      </section>
      ${showRain ? `
      <section class="wh-rain">
        <span class="wh-rain-label">Rain</span>
        <div class="wh-rain-bars">
          ${renderRainBars(points)}
        </div>
      </section>` : ""}
    </div>
  `;

  let Chart;
  try {
    Chart = await loadChart();
  } catch (err) {
    shadow.innerHTML = renderError(err.message || "chart.js load failed");
    return;
  }

  const canvas = shadow.querySelector(".chart");
  if (!canvas) return;
  const t = ctx.theme;
  const step = labelEvery(points, size);
  const labels = points.map((p, i) => (i % step === 0 ? `${p.hour}:00` : ""));
  const temps = points.map((p) => p.temp);

  const fontFamily =
    ctx.font?.family || 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  const baseFont = {
    family: fontFamily,
    size: size === "lg" ? 13 : 11,
    weight: "700",
  };

  new Chart(canvas.getContext("2d"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          data: temps,
          borderColor: t.accent,
          backgroundColor: hexToRgba(t.accent, 0.16),
          borderWidth: 3,
          tension: 0.35,
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
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: t.fgSoft,
            font: baseFont,
            autoSkip: false,
            maxRotation: 0,
            callback(_value, index) { return labels[index] || ""; },
          },
        },
        y: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: t.fgSoft,
            font: baseFont,
            callback: (v) => `${Math.round(v)}°`,
            maxTicksLimit: 4,
          },
        },
      },
      layout: { padding: { top: 8, right: 12, bottom: 0, left: 0 } },
    },
  });
}

function renderRainBars(points) {
  return points
    .map((p) => {
      const pct = p.rain == null ? 0 : Math.max(0, Math.min(100, p.rain));
      const wet = pct >= 30;
      return `<span class="wh-rain-bar${wet ? " is-wet" : ""}" style="--rain: ${pct}%" title="${pct}% at ${p.hour}:00"></span>`;
    })
    .join("");
}
