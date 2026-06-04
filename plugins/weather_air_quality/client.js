// weather_air_quality — Spectra status archetype, AQI band as state.
//
// Hero = AQI value + band label, pill colored by band, status-grid of
// individual pollutant levels.

// Band index → accent: 0 Good → moss, 1 Fair → teal, 2 Moderate → ochre,
// 3 Poor → terracotta, 4 Very poor → plum, 5 Extreme → terracotta.
const BAND_ACCENT = [
  "var(--accent-3)", // Good — moss
  "var(--accent-4)", // Fair — teal
  "var(--accent-2)", // Moderate — ochre
  "var(--accent-1)", // Poor — terracotta
  "var(--accent-6)", // Very poor — plum
  "var(--accent-1)", // Extreme — terracotta
];

const POLLUTANT_PH = {
  pm2_5: "ph-virus",
  pm10: "ph-virus",
  o3: "ph-leaf",
  no2: "ph-car",
  so2: "ph-factory",
  co: "ph-fire",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function bandAccent(i) {
  return BAND_ACCENT[i] || "var(--accent-3)";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="weather_air_quality">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Air Quality</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.label || "";
  const eaqi = data.eaqi ?? data.european_aqi;
  const band = data.band || "—";
  const bandIdx = Number.isFinite(data.bandIndex) ? data.bandIndex : 0;
  const accent = bandAccent(bandIdx);
  const dominant = data.dominant || "";

  const pollutants = Array.isArray(data.pollutants) ? data.pollutants : [];
  const grid = pollutants.slice(0, 6).map((p) => {
    const ph = POLLUTANT_PH[p.icon] || "ph-circle";
    const accentP = bandAccent(p.bandIndex ?? 0);
    return `
      <div class="status-cell">
        <span class="u-label">${escapeHtml(p.label)}</span>
        <span class="v" style="color:${accentP}">
          <i class="ph-bold ${ph}" style="font-size:.7em;color:${accentP}"></i>
          ${escapeHtml(p.value ?? "—")}<small style="font-size:.55em;font-weight:var(--fw-semi);color:var(--text-muted)"> ${escapeHtml(p.unit ?? "")}</small>
        </span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="weather_air_quality">
      <div class="w-title">
        <i class="ph-bold ph-wind"></i>
        <h3>${escapeHtml(label || "Air Quality")}</h3>
        ${dominant ? `<span class="w-title-meta">${escapeHtml(dominant)}</span>` : ""}
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ph-gauge" style="color:${accent}"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(eaqi ?? "—")}</span>
            <span class="status-sub">European AQI</span>
          </div>
        </div>
        <span class="pill" style="background:${accent}">${escapeHtml(band)}</span>
        ${grid ? `<div class="status-grid">${grid}</div>` : ""}
      </div>
    </div>`;
}
