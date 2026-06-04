// weather_now — Spectra weather archetype.
//
// Renders ``.w`` → optional ``.w-title`` → ``.w-body.wx-body`` with a
// hero (icon + temp + condition) and a 4-cell metric strip pulled from
// ctx.data.metrics. Semantic weather icon names map to Phosphor bold
// glyphs via PH_BY_NAME; metric icons via METRIC_PH.

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

// Condition → accent token. Sun/UV uses ochre, rain/water uses teal,
// stable cloud/cold uses slate blue, storms get the terracotta alert
// colour. Night-time icons fall back to text-secondary so a dark icon
// on a dark theme doesn't go invisible.
const COND_ACCENT = {
  sun: "var(--accent-2)",            // ochre
  moon: "var(--text-secondary)",
  cloud: "var(--accent-5)",          // slate blue
  partly: "var(--accent-2)",
  "partly-night": "var(--text-secondary)",
  drizzle: "var(--accent-4)",        // teal
  rain: "var(--accent-4)",
  "rain-heavy": "var(--accent-4)",
  showers: "var(--accent-4)",
  snow: "var(--accent-5)",
  storm: "var(--accent-1)",          // terracotta — alert
  fog: "var(--text-muted)",
};

const METRIC_PH = {
  humidity: "ph-drop",
  wind: "ph-wind",
  rainprob: "ph-cloud-rain",
  uv: "ph-sun",
  pressure: "ph-gauge",
  dew: "ph-drop-half",
  visibility: "ph-eye",
  cloud: "ph-cloud",
};

// Metric icon accent — water-themed metrics teal, sun-themed ochre,
// neutral measurements stay text-secondary so the grid keeps a steady
// rhythm rather than every cell shouting for attention.
const METRIC_ACCENT = {
  humidity: "var(--accent-4)",
  wind: "var(--text-secondary)",
  rainprob: "var(--accent-4)",
  uv: "var(--accent-2)",
  pressure: "var(--text-secondary)",
  dew: "var(--accent-4)",
  visibility: "var(--text-secondary)",
  cloud: "var(--text-secondary)",
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

function fmtMetric(m) {
  if (m == null || m.value == null) return "—";
  const v = m.value;
  if (typeof v === "number") {
    return (v >= 100 ? Math.round(v) : v).toString();
  }
  return String(v);
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/style/spectra-widgets.css">
      <div class="w">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Weather</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.label || "";
  const icon = PH_BY_NAME[data.icon] || "ph-cloud";
  const heroAccent = COND_ACCENT[data.icon] || "var(--accent-4)";
  const temp = fmtTemp(data.temp);
  const cond = data.cond || "";
  const feels = data.feels != null ? `feels ${fmtTemp(data.feels)}` : "";
  const subParts = [cond, feels].filter(Boolean);

  const metrics = Array.isArray(data.metrics) ? data.metrics.slice(0, 4) : [];
  const cells = metrics.map((m) => {
    const ph = METRIC_PH[m.icon] || "ph-circle";
    const accent = METRIC_ACCENT[m.icon] || "var(--text-secondary)";
    const unit = m.unit ? `<span class="unit"> ${escapeHtml(m.unit)}</span>` : "";
    return `
      <div class="wx-cell">
        <span class="d">${escapeHtml(m.label || "")}</span>
        <i class="ph-bold ${ph}" style="color:${accent}"></i>
        <span class="t">${escapeHtml(fmtMetric(m))}${unit}</span>
      </div>`;
  }).join("");

  const titleBar = label
    ? `<div class="w-title"><i class="ph-bold ph-map-pin" style="color:var(--accent-4)"></i><h3>${escapeHtml(label)}</h3></div>`
    : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <div class="w" data-widget="weather_now">
      ${titleBar}
      <div class="w-body wx-body">
        <div class="wx-now">
          <i class="ph-bold ${icon}" style="color:${heroAccent}"></i>
          <div>
            <div class="wx-temp">${escapeHtml(temp)}</div>
            <div class="wx-cond">${escapeHtml(subParts.join(" · "))}</div>
          </div>
        </div>
        ${cells ? `<div class="wx-forecast">${cells}</div>` : ""}
      </div>
    </div>`;
}
