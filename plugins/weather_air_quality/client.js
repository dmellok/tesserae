// weather_air_quality — Bauhaus AQI card.
//
// Layout:
//   1. Inverted header bar (mark + AIR · place + time)
//   2. Hero split 50/50: big AQI number + band label | wind icon panel
//   3. Four-up pollutant chip strip (PM2.5 / PM10 / O3 / NO2)
//
// The AQI hero block recolours by band so the eye picks "good vs bad"
// before reading the number. All decorative tokens (accent / accent2 /
// accent3) — never warn / danger.

const EAQI_BANDS = [
  { max: 20,  label: "Good",            cls: "aq-band--good"     },
  { max: 40,  label: "Fair",            cls: "aq-band--fair"     },
  { max: 60,  label: "Moderate",        cls: "aq-band--moderate" },
  { max: 80,  label: "Poor",            cls: "aq-band--poor"     },
  { max: 100, label: "Very Poor",       cls: "aq-band--vpoor"    },
  { max: Infinity, label: "Extreme",    cls: "aq-band--vpoor"    },
];
const US_BANDS = [
  { max: 50,  label: "Good",            cls: "aq-band--good"     },
  { max: 100, label: "Moderate",        cls: "aq-band--fair"     },
  { max: 150, label: "Unhealthy SG",    cls: "aq-band--moderate" },
  { max: 200, label: "Unhealthy",       cls: "aq-band--poor"     },
  { max: 300, label: "Very Unhealthy",  cls: "aq-band--vpoor"    },
  { max: Infinity, label: "Hazardous",  cls: "aq-band--vpoor"    },
];

function bandFor(scale, value) {
  const bands = scale === "us" ? US_BANDS : EAQI_BANDS;
  if (value == null) return { label: "—", cls: "" };
  return bands.find((b) => value <= b.max) || bands[bands.length - 1];
}

function fmtInt(v) { return v == null ? "—" : String(Math.round(v)); }
function fmtNum(v) { return v == null ? "—" : Number(v).toFixed(1); }

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
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/weather_air_quality/client.css">
    <div class="root error">
      <i class="ph ph-warning-circle" aria-hidden="true"></i>
      <span>${escapeHtml(msg)}</span>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = renderError(data.error);
    return;
  }

  const size = ctx.cell.size;
  const scale = data.scale === "us" ? "us" : "european";
  const aqi = scale === "us" ? data.us_aqi : data.european_aqi;
  const scaleLabel = scale === "us" ? "US AQI" : "EAQI";
  const band = bandFor(scale, aqi);

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_air_quality/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="aq-place">${escapeHtml(data.label || "—")}</span>
        <span class="aq-time">${nowTime()}</span>
      </header>
      <section class="aq-hero ${band.cls}">
        <div class="aq-hero-text">
          <div class="aq-aqi">${fmtInt(aqi)}</div>
          <div class="aq-band-label">${escapeHtml(band.label)}</div>
          <div class="aq-scale">${escapeHtml(scaleLabel)}</div>
        </div>
        <div class="aq-hero-icon" aria-hidden="true">
          <i class="ph-bold ph-wind"></i>
        </div>
      </section>
      <section class="aq-stats">
        <div class="aq-stat aq-stat--accent">
          <i class="ph-bold ph-circle-dashed aq-stat-icon" aria-hidden="true"></i>
          <span class="aq-stat-label">PM2.5</span>
          <span class="aq-stat-value">${fmtNum(data.pm2_5)}<small>μg/m³</small></span>
        </div>
        <div class="aq-stat aq-stat--surface">
          <i class="ph-bold ph-circles-three aq-stat-icon" aria-hidden="true"></i>
          <span class="aq-stat-label">PM10</span>
          <span class="aq-stat-value">${fmtNum(data.pm10)}<small>μg/m³</small></span>
        </div>
        <div class="aq-stat aq-stat--accent2">
          <i class="ph-bold ph-sun aq-stat-icon" aria-hidden="true"></i>
          <span class="aq-stat-label">Ozone</span>
          <span class="aq-stat-value">${fmtInt(data.ozone)}<small>μg/m³</small></span>
        </div>
        <div class="aq-stat aq-stat--accent3">
          <i class="ph-bold ph-factory aq-stat-icon" aria-hidden="true"></i>
          <span class="aq-stat-label">NO2</span>
          <span class="aq-stat-value">${fmtInt(data.no2)}<small>μg/m³</small></span>
        </div>
      </section>
    </div>
  `;
}
