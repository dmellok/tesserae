// weather_air_quality, Spectra status archetype, AQI band as state.
//
// Hero is a half-circle gauge that arcs through the 6 EAQI bands (green
// → yellow → ochre → terracotta → plum → red) with a marker at the
// current value; AQI number sits inside the arc, band label below.
// The 6-pollutant grid underneath gains a micro-bar per cell showing
// how full the pollutant's current band is, tinted in that band's
// colour so the eye can pick out the worst offender at a glance.

// Band index → accent: 0 Good → moss, 1 Fair → teal, 2 Moderate → ochre,
// 3 Poor → terracotta, 4 Very poor → plum, 5 Extreme → terracotta.
const BAND_ACCENT_RAW = [
  "var(--accent-3)", // Good, moss
  "var(--accent-4)", // Fair, teal
  "var(--accent-2)", // Moderate, ochre
  "var(--accent-1)", // Poor, terracotta
  "var(--accent-6)", // Very poor, plum
  "var(--accent-1)", // Extreme, terracotta
];

const POLLUTANT_PH = {
  pm2_5: "ph-virus",
  pm10: "ph-virus",
  pm2: "ph-virus",
  pm: "ph-virus",
  o3: "ph-leaf",
  ozone: "ph-leaf",
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
  return BAND_ACCENT_RAW[i] || "var(--accent-3)";
}

// Polar → cartesian on the gauge circle. Angles in degrees, 0° = right
// horizon, 90° = top of arc, 180° = left horizon. SVG y is flipped so
// we subtract from cy.
function polar(cx, cy, r, angleDeg) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
}

// Half-circle gauge SVG. Six band segments arc across the top from
// left to right, a chunky marker sits at the current AQI position.
// viewBox padded above the arc so the marker's outer ring doesn't get
// clipped at the apex; padding below leaves room for the value + band
// label that get overlaid via absolute positioning in HTML.
function gaugeSvg(eaqi, bandIdx) {
  const cx = 100;
  const cy = 100;
  const r = 80;
  const strokeW = 16;
  const eaqiNum = Number(eaqi);
  const scaleVal = Number.isFinite(eaqiNum) ? Math.max(0, Math.min(120, eaqiNum)) : null;

  const segments = [];
  for (let i = 0; i < 6; i++) {
    const a1 = 180 - i * 30;
    const a2 = a1 - 30;
    const p1 = polar(cx, cy, r, a1);
    const p2 = polar(cx, cy, r, a2);
    segments.push(
      `<path d="M ${p1.x.toFixed(2)} ${p1.y.toFixed(2)} ` +
        `A ${r} ${r} 0 0 1 ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}" ` +
        `fill="none" stroke="${bandAccent(i)}" stroke-width="${strokeW}" stroke-linecap="butt"/>`
    );
  }

  let marker = "";
  if (scaleVal != null) {
    const angle = 180 - (scaleVal / 120) * 180;
    const m = polar(cx, cy, r, angle);
    const color = bandAccent(bandIdx ?? 0);
    marker = `
      <circle cx="${m.x.toFixed(2)}" cy="${m.y.toFixed(2)}" r="13"
              fill="var(--surface)" stroke="${color}" stroke-width="3"/>
      <circle cx="${m.x.toFixed(2)}" cy="${m.y.toFixed(2)}" r="6"
              fill="${color}"/>
    `;
  }

  // viewBox: 0..200 x 0..115. Arc lives at y=20..100 (peak to horizon);
  // y=100..115 is reserved for the overlay text padding so it never
  // crowds the arc.
  return `
    <svg class="aqi-gauge-svg" viewBox="0 0 200 115" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      ${segments.join("")}
      ${marker}
    </svg>`;
}

// Per-pollutant micro-bar. Fills 0..100 % of (current value / band
// upper edge), tinted by the pollutant's own band. The "band upper
// edge" comes from the server (p.max) so each pollutant uses its real
// EAQI breakpoints rather than a one-size-fits-all 0-100 scale.
function microBar(p) {
  const level = Number(p?.level ?? p?.value);
  const max = Number(p?.max);
  if (!Number.isFinite(level) || !Number.isFinite(max) || max <= 0) {
    return `
      <div class="aq-microbar" aria-hidden="true">
        <div class="aq-microbar-fill" style="width:0%"></div>
      </div>`;
  }
  const pct = Math.max(2, Math.min(100, (level / max) * 100));
  const color = bandAccent(p.bandIndex ?? 0);
  return `
    <div class="aq-microbar" aria-hidden="true">
      <div class="aq-microbar-fill" style="width:${pct.toFixed(1)}%;background:${color}"></div>
    </div>`;
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
  const band = data.band || "-";
  const bandIdx = Number.isFinite(data.bandIndex) ? data.bandIndex : 0;
  const accent = bandAccent(bandIdx);
  const dominant = data.dominant || "";

  const pollutants = Array.isArray(data.pollutants) ? data.pollutants : [];
  const cells = pollutants.slice(0, 6).map((p) => {
    const ph = POLLUTANT_PH[p.icon] || POLLUTANT_PH[p.label?.toLowerCase()] || "ph-circle";
    const accentP = bandAccent(p.bandIndex ?? 0);
    return `
      <div class="aq-cell">
        <span class="aq-cell-label">${escapeHtml(p.label)}</span>
        <span class="aq-cell-value" style="color:${accentP}">
          <i class="ph-bold ${ph}"></i>
          <span>${escapeHtml(p.value ?? "-")}<small> ${escapeHtml(p.unit ?? "")}</small></span>
        </span>
        ${microBar(p)}
      </div>`;
  }).join("");

  const layout = `
    .aq-body {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-4);
    }
    /* Gauge, half-circle SVG with overlay text. The overlay sits in
       the lower half of the SVG's viewBox area so the number + band
       label centre under the arc's apex. max-width caps how wide the
       gauge can grow on a tall cell so it doesn't blow up past the
       point where the band segments read as discrete colours. */
    .aqi-gauge {
      position: relative;
      width: 100%;
      max-width: clamp(14em, 80%, 28em);
      margin: 0 auto;
      flex: 0 0 auto;
    }
    .aqi-gauge-svg { width: 100%; height: auto; display: block; }
    .aqi-gauge-overlay {
      position: absolute;
      left: 0;
      right: 0;
      /* Stack value + band, sit just below the arc's apex so the text
         reads as the gauge's needle pointing down at the value. */
      top: 38%;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0;
      pointer-events: none;
    }
    .aqi-gauge-value {
      font-size: clamp(2em, 18cqmin, 4.6em);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-tight);
      line-height: 1;
      color: var(--text-primary);
    }
    .aqi-gauge-band {
      font-size: clamp(0.7em, 3cqmin, 1.1em);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-secondary);
      margin-top: 0.2em;
    }

    /* Pollutant grid, 3 columns on md+, 2 on sm, hidden on xs. Each
       cell holds 3 stacked items (label, value-with-icon, micro-bar)
       and participates in the parent grid via subgrid so the three
       sub-rows stay synchronised across every cell in the same row.
       That way, if PM10's value wraps to two lines, every cell in the
       same row picks up the taller value row and their micro-bars
       all land on the same horizontal, instead of one bar falling
       below its neighbours. */
    .aq-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      grid-template-rows: repeat(2, auto auto auto);
      gap: var(--space-3) var(--space-4);
      flex: 0 0 auto;
    }
    .aq-cell {
      display: grid;
      grid-template-rows: subgrid;
      grid-row: span 3;
      gap: 0.2em;
      min-width: 0;
    }
    .aq-cell-label {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-muted);
    }
    .aq-cell-value {
      display: inline-flex;
      align-items: baseline;
      gap: 0.3em;
      font-size: var(--fs-lead);
      font-weight: var(--fw-bold);
      line-height: 1;
      /* Keep the value + icon + unit on a single line so a cell with a
         wide reading (e.g. "4.2 μg/m³") doesn't shove its micro-bar
         down a row relative to its neighbours. Overflow clips with
         ellipsis if a cell is genuinely too narrow. */
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
      min-width: 0;
    }
    .aq-cell-value .ph-bold {
      font-size: 0.9em;
      align-self: center;
      flex: 0 0 auto;
    }
    .aq-cell-value small {
      font-size: 0.55em;
      font-weight: var(--fw-semi);
      color: var(--text-muted);
      margin-left: 0.15em;
      flex: 0 0 auto;
    }
    .aq-microbar {
      width: 100%;
      height: var(--stroke-3);
      background: var(--surface-sunken);
      margin-top: 0.4em;
      overflow: hidden;
    }
    .aq-microbar-fill {
      height: 100%;
      background: var(--accent-3);
      transition: none;
    }

    /* xs: drop the pollutant grid entirely, the gauge carries the
       headline and there's no room for six tiny cells with bars. */
    @container (max-width: 280px) {
      .aq-grid { display: none; }
      .aqi-gauge { max-width: 100%; }
    }
    /* sm: 2 columns + drop the last 2 pollutants so the grid doesn't
       wrap into a third row. Two cell rows (2 visible cells per
       column) so the subgrid track count matches. */
    @container (min-width: 281px) and (max-width: 440px) {
      .aq-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        grid-template-rows: repeat(2, auto auto auto);
      }
      .aq-grid .aq-cell:nth-child(n+5) { display: none; }
    }
    /* lg: side-by-side gauge + grid so the gauge isn't drowning in
       empty space above a 3-cell row. Gauge gets the left column, the
       6-cell grid stacks 2 wide on the right so each row keeps a
       comfortable label + bar size. Three cell-rows × 3 sub-rows
       each. */
    @container (min-width: 700px) {
      .aq-body {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr);
        align-items: center;
        gap: var(--space-5);
      }
      .aqi-gauge { max-width: 100%; }
      .aq-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        grid-template-rows: repeat(3, auto auto auto);
        gap: var(--space-3) var(--space-5);
      }
      .aq-cell-value {
        font-size: clamp(1.2em, 4cqmin, 2em);
      }
    }
  `;

  const valueDisplay = (eaqi == null || eaqi === "" || Number.isNaN(Number(eaqi)))
    ? "-"
    : String(Math.round(Number(eaqi)));

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="weather_air_quality">
      <div class="w-title">
        <i class="ph-bold ph-wind" style="color:${accent}"></i>
        <h3>${escapeHtml(label || "Air Quality")}</h3>
        ${dominant ? `<span class="w-title-meta">${escapeHtml(dominant)}</span>` : ""}
      </div>
      <div class="w-body aq-body">
        <div class="aqi-gauge">
          ${gaugeSvg(eaqi, bandIdx)}
          <div class="aqi-gauge-overlay">
            <span class="aqi-gauge-value">${escapeHtml(valueDisplay)}</span>
            <span class="aqi-gauge-band" style="color:${accent}">${escapeHtml(band)}</span>
          </div>
        </div>
        ${cells ? `<div class="aq-grid">${cells}</div>` : ""}
      </div>
    </div>`;
}
