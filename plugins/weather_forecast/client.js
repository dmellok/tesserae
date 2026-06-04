// weather_forecast — Spectra weather archetype, multi-day strip.
//
// Skips the wx-now hero (no current-conditions block on this widget),
// goes straight to wx-forecast with one .wx-cell per day. Today's cell
// is tinted with --accent-4 so it stands out without breaking the
// no-borders rule.

const PH_BY_NAME = {
  sun: "ph-sun",
  moon: "ph-moon",
  cloud: "ph-cloud",
  partly: "ph-cloud-sun",
  "partly-night": "ph-cloud-moon",
  drizzle: "ph-drop",
  rain: "ph-cloud-rain",
  "rain-heavy": "ph-cloud-rain",
  showers: "ph-cloud-rain",
  snow: "ph-snowflake",
  storm: "ph-cloud-lightning",
  fog: "ph-cloud-fog",
};

// Same condition palette as weather_now so a single dashboard reads
// consistently across both widgets.
const COND_ACCENT = {
  sun: "var(--accent-2)",
  moon: "var(--text-secondary)",
  cloud: "var(--accent-5)",
  partly: "var(--accent-2)",
  "partly-night": "var(--text-secondary)",
  drizzle: "var(--accent-4)",
  rain: "var(--accent-4)",
  "rain-heavy": "var(--accent-4)",
  showers: "var(--accent-4)",
  snow: "var(--accent-5)",
  storm: "var(--accent-1)",
  fog: "var(--text-muted)",
};

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
      <div class="w" data-widget="weather_forecast">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Forecast</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.place || data.label || "";
  const days = Array.isArray(data.days) ? data.days : [];

  const cells = days.map((d) => {
    const ph = PH_BY_NAME[d.icon] || "ph-cloud";
    const accent = COND_ACCENT[d.icon] || "var(--accent-5)";
    const isToday = d.today;
    // Today gets the day label tinted accent-4 so the column stands out
    // without taking the condition accent away from the icon itself.
    const dayStyle = isToday ? ' style="color:var(--accent-4);font-weight:var(--fw-black)"' : "";
    const dayText = d.weekday || d.day || "";
    return `
      <div class="wx-cell">
        <span class="d"${dayStyle}>${escapeHtml(dayText)}</span>
        <i class="ph-bold ${ph}" style="color:${accent}"></i>
        <span class="t">${escapeHtml(fmtTemp(d.hi ?? d.high))}<span class="u-muted" style="font-weight:var(--fw-semi)"> ${escapeHtml(fmtTemp(d.lo ?? d.low))}</span></span>
      </div>`;
  }).join("");

  const titleBar = `
    <div class="w-title">
      <i class="ph-bold ph-calendar" style="color:var(--accent-4)"></i>
      <h3>${escapeHtml(label || "Forecast")}</h3>
      ${data.rangeHi != null && data.rangeLo != null
        ? `<span class="w-title-meta">${escapeHtml(fmtTemp(data.rangeHi))} / ${escapeHtml(fmtTemp(data.rangeLo))}</span>`
        : ""}
    </div>`;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="weather_forecast">
      ${titleBar}
      <div class="w-body wx-body">
        <div class="wx-forecast">${cells || '<p class="u-muted">No forecast.</p>'}</div>
      </div>
    </div>`;
}
