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

  // Horizontal AQI band scale — six coloured segments showing the
  // 0/20/40/60/80/100+ European AQI bands with a marker pip sitting at
  // the current value. Gives the widget a real visual anchor: instead
  // of "22 European AQI / FAIR" reading as just two text rows, the
  // scale shows at a glance "you're sitting in the lower-half of band
  // 2". Band boundaries match Open-Meteo's European AQI segmentation
  // (0–20 Good, 20–40 Fair, 40–60 Moderate, 60–80 Poor, 80–100 Very
  // Poor, 100+ Extreme).
  const BAND_STOPS = [0, 20, 40, 60, 80, 100];
  const BAND_LABELS = ["GOOD", "FAIR", "MOD", "POOR", "V.POOR", "EXT"];
  const eaqiNum = Number(eaqi);
  const scaleVal = Number.isFinite(eaqiNum) ? Math.max(0, Math.min(120, eaqiNum)) : null;
  const scaleSegments = BAND_STOPS.map((_, i) => {
    const segAccent = bandAccent(i);
    const segLabel = BAND_LABELS[i] || "";
    return `
      <div class="aqi-seg" style="background:${segAccent}">
        <span class="aqi-seg-label">${segLabel}</span>
      </div>`;
  }).join("");
  // Marker position as a percentage across the 0..120 scale (we cap at
  // 120 so an extreme reading still has a visible pip rather than
  // disappearing off the right edge).
  const markerPct = scaleVal != null ? (scaleVal / 120) * 100 : null;
  const scale = scaleVal != null ? `
    <div class="aqi-scale">
      <div class="aqi-segs">${scaleSegments}</div>
      <div class="aqi-marker" style="left:${markerPct.toFixed(1)}%">
        <span class="aqi-marker-pip" style="background:${accent}"></span>
        <span class="aqi-marker-value" style="color:${accent}">${escapeHtml(eaqi ?? "—")}</span>
      </div>
    </div>` : "";

  const layout = `
    .aqi-scale {
      position: relative;
      width: 100%;
      padding-top: 1.8em;
    }
    .aqi-segs {
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: var(--stroke-1);
      height: 1.2em;
    }
    .aqi-seg {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }
    .aqi-seg-label {
      font-size: 0.62em;
      font-weight: var(--fw-bold);
      letter-spacing: 0.06em;
      color: var(--on-accent);
      text-transform: var(--label-transform, uppercase);
      white-space: nowrap;
    }
    @container (max-width: 360px) {
      .aqi-seg-label { display: none; }
    }
    .aqi-marker {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 0;
      transform: translateX(-50%);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.2em;
    }
    .aqi-marker-pip {
      width: 0.7em;
      height: 1.6em;
      border-radius: 0.15em;
      border: var(--stroke-2) solid var(--surface);
      box-shadow: 0 0 0 var(--stroke-1) currentColor;
      flex: 0 0 auto;
      margin-top: 1.2em;
    }
    .aqi-marker-value {
      font-size: var(--fs-label);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-tight);
      order: -1;
    }
    /* Hero pill grows so the band label reads as the headline next to
       the AQI number rather than a footnote below it. */
    .aqi-hero {
      display: flex;
      align-items: baseline;
      gap: var(--space-3);
      flex-wrap: wrap;
    }
    .aqi-band {
      font-size: var(--fs-label);
      font-weight: var(--fw-black);
      letter-spacing: 0.1em;
      text-transform: var(--label-transform, uppercase);
      padding: 0.3em 0.7em;
      color: var(--on-accent);
      border-radius: var(--pill-radius, var(--radius-0));
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="weather_air_quality">
      <div class="w-title">
        <i class="ph-bold ph-wind" style="color:${accent}"></i>
        <h3>${escapeHtml(label || "Air Quality")}</h3>
        ${dominant ? `<span class="w-title-meta">${escapeHtml(dominant)}</span>` : ""}
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ph-gauge" style="color:${accent}"></i>
          <div class="lockup">
            <div class="aqi-hero">
              <span class="status-state">${escapeHtml(eaqi ?? "—")}</span>
              <span class="aqi-band" style="background:${accent}">${escapeHtml(band)}</span>
            </div>
            <span class="status-sub">European AQI</span>
          </div>
        </div>
        ${scale}
        ${grid ? `<div class="status-grid">${grid}</div>` : ""}
      </div>
    </div>`;
}
