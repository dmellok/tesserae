// weather_hourly — Spectra chart archetype, hourly temperature.
//
// Title bar shows place + current temp meta; body is a bar chart of the
// next N hours' temperatures. Bars use --accent-5 except the current
// hour (now), which is highlighted with --accent-1.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTemp(v) {
  if (v == null) return "—";
  return Math.round(Number(v)) + "°";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="weather_hourly">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Hourly</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.place || data.label || "";
  const hours = Array.isArray(data.hours) ? data.hours : [];

  // Normalise the bar heights: max temp in the window → 100%; min → 10%
  // so a flat-ish day still shows a visible bar. Negative temps still
  // render (clamp to the 10% floor).
  const temps = hours.map((h) => Number(h.temp)).filter((t) => !Number.isNaN(t));
  const tMax = temps.length ? Math.max(...temps) : 0;
  const tMin = temps.length ? Math.min(...temps) : 0;
  const range = Math.max(1, tMax - tMin);

  const bars = hours.map((h, i) => {
    const t = Number(h.temp);
    const valid = !Number.isNaN(t);
    const pct = valid ? Math.max(10, ((t - tMin) / range) * 90 + 10) : 10;
    const isNow = i === 0; // first hour is "current" in the trimmed array
    const color = isNow ? "var(--accent-1)" : "var(--accent-5)";
    return `
      <div class="chart-col">
        <div class="chart-bar"><span style="height:${pct}%;background:${color}"></span></div>
        <span class="chart-x">${escapeHtml(h.hour ?? "")}</span>
      </div>`;
  }).join("");

  const titleBar = `
    <div class="w-title">
      <i class="ph-bold ph-clock"></i>
      <h3>${escapeHtml(label || "Hourly")}</h3>
      ${data.now != null ? `<span class="w-title-meta">${escapeHtml(fmtTemp(data.now))} now</span>` : ""}
    </div>`;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="weather_hourly">
      ${titleBar}
      <div class="w-body chart-body">
        <div class="chart-figure">${bars || '<p class="u-muted">No hourly data.</p>'}</div>
        <div class="chart-legend">
          <span class="chart-key"><span class="dot" style="background:var(--accent-5)"></span>Forecast</span>
          <span class="chart-key"><span class="dot" style="background:var(--accent-1)"></span>Now</span>
        </div>
      </div>
    </div>`;
}
