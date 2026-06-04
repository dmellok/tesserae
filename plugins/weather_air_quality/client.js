// weather_air_quality — air-quality card.
//
// Ships five visual directions, picked per-cell via the ``variant``
// option:
//   legacy   The original Bauhaus AQI card we shipped pre-handoff.
//   r1       Refined — charcoal header + EAQI hero + 6-pollutant
//            hairline grid. Primary direction.
//   g2       Geometric — De Stijl colour blocks, big EAQI block,
//            4-tile pollutant strip.
//   s3       Swiss — hairline header, light numerals, marks for
//            each pollutant.
//   d4       Data — SVG ring gauge for EAQI + horizontal bars per
//            pollutant (fill = value / band-max).
//
// All five render from the same ``ctx.data`` shape that server.py
// returns. Legacy paints from the flat fields (``pm2_5``, ``ozone``,
// …); the new directions paint from ``data.eaqi`` / ``data.band`` /
// ``data.pollutants``. Both shapes are always present so a cell can
// flip variants without re-fetching.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

// Map band-name → accent token. Matches the design handoff's
// BAND_ACCENT table (Very poor / Extreme share red).
const BAND_ACCENT = {
  Good: "green",
  Fair: "blue",
  Moderate: "yellow",
  Poor: "red",
  "Very poor": "red",
  Extreme: "red",
};

function escapeHtml(s) { return WX.escapeHtml(s); }
function fmtInt(v) { return v == null ? "—" : String(Math.round(v)); }
function fmtNum(v) { return v == null ? "—" : Number(v).toFixed(1); }
function fmtPollutant(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n >= 100 ? String(Math.round(n)) : n.toFixed(1);
}
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// ===========================================================
// LEGACY — original Bauhaus card (preserved as-was)
// ===========================================================
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

function renderLegacy(data, size) {
  const scale = data.scale === "us" ? "us" : "european";
  const aqi = scale === "us" ? data.us_aqi : data.european_aqi;
  const scaleLabel = scale === "us" ? "US AQI" : "EAQI";
  const band = bandFor(scale, aqi);
  return `
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

// ===========================================================
// Shared helpers for the four new directions
// ===========================================================
function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
    <link rel="stylesheet" href="/plugins/weather_air_quality/client.css">
  `;
}

function pollutantsAvailable(data) {
  return Array.isArray(data.pollutants) ? data.pollutants : [];
}

// The 6-segment EAQI band scale — coloured pip on the active band, all
// others a hairline. Used by R1 + S3.
function bandScale(data, { height = 8, gap = 3 } = {}) {
  const bands = Array.isArray(data.bands) ? data.bands : [];
  const active = Number.isInteger(data.bandIndex) ? data.bandIndex : -1;
  return `
    <div style="display:flex;gap:${gap}px;width:100%">
      ${bands.map((b, i) => `
        <div style="flex:1;height:${height}px;background:${i === active ? WX.col(BAND_ACCENT[b]) : "var(--c-line)"}"></div>
      `).join("")}
    </div>
  `;
}

// ===========================================================
// R1 — REFINED
// Charcoal header → accent rule → hero split (tinted EAQI text +
// solid accent leaf panel) → plain paper pollutant grid.
// ===========================================================
function renderR1(data) {
  const pollutants = pollutantsAvailable(data);
  const bandAccent = BAND_ACCENT[data.band] || "green";
  const firstEdge = Array.isArray(data.bands) ? data.bands[0] : "";
  const lastEdge  = Array.isArray(data.bands) ? data.bands[data.bands.length - 1] : "";
  return `
    ${styleBlock()}
    <style>
      .wa-r1-hero { display:grid; grid-template-columns:1.6fr 1fr; min-width:0; min-height:0; border-top:3px solid var(--c-accent); }
      .wa-r1-hero-text { background:var(--wx-tint); padding:clamp(10px, 2.4cqw, 18px) clamp(12px, 2.6cqw, 22px); display:flex; flex-direction:column; justify-content:center; gap:6px; min-width:0; }
      .wa-r1-eaqi { font-family:var(--wx-black); font-size:clamp(40px, 13cqw, 72px); line-height:.82; color:var(--c-accent); }
      .wa-r1-band { font-family:var(--wx-black); font-size:clamp(16px, 5cqw, 30px); letter-spacing:.01em; }
      .wa-r1-scale-label { font-family:var(--wx-mono); font-size:11px; letter-spacing:.04em; color:var(--wx-ink-60); margin-top:8px; }
      .wa-r1-hero-icon { background:var(--c-accent); color:var(--wx-red-fg); display:flex; align-items:center; justify-content:center; padding:clamp(8px, 2cqw, 16px); }
      .wa-r1-hero-icon .ph-bold { font-size:clamp(48px, 16cqw, 110px); }
      .wa-r1-grid { flex:1; display:grid; grid-template-columns:repeat(3,1fr); grid-template-rows:1fr 1fr; gap:1px; background:var(--c-line); border-top:3px solid var(--c-accent); min-height:0; }
      .wa-r1-cell { background:var(--wx-paper); padding:10px 16px; display:flex; flex-direction:column; justify-content:center; gap:6px; min-width:0; overflow:hidden; }
      .wa-r1-cell-head { display:flex; align-items:center; gap:8px; }
      .wa-r1-cell-num { font-family:var(--wx-black); font-size:clamp(16px, 3cqw, 22px); color:var(--c-text); }

      @container (max-width: 460px) {
        .wa-r1-hero { grid-template-columns:1fr; }
        .wa-r1-grid { grid-template-columns:repeat(2,1fr); grid-template-rows:repeat(${Math.max(2, Math.ceil(pollutants.length / 2))},1fr); }
      }
      @container (max-width: 280px) {
        .wa-r1-grid { display:none; }
      }
    </style>
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: (data.label || "—") + " · AIR QUALITY", accent: "red", right: nowTime() })}
      <div class="wa-r1-hero">
        <div class="wa-r1-hero-text">
          <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">
            <span class="wx-tnum wa-r1-eaqi">${fmtInt(data.eaqi)}</span>
            <span class="wa-r1-band" style="color:${WX.col(bandAccent)}">${escapeHtml((data.band || "").toUpperCase())}</span>
          </div>
          <div style="font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.06em;color:var(--wx-ink-60)">EUROPEAN AQI · DOMINANT ${escapeHtml((data.dominant || "—").toUpperCase())}</div>
          <div style="display:flex;justify-content:space-between" class="wa-r1-scale-label">
            <span>${escapeHtml(String(firstEdge))}</span><span>${escapeHtml(String(lastEdge))}</span>
          </div>
          ${bandScale(data, { height: 10 })}
        </div>
        <div class="wa-r1-hero-icon">
          <i class="ph-bold ph-leaf" aria-hidden="true"></i>
        </div>
      </div>
      <div class="wa-r1-grid">
        ${pollutants.map((p) => `
          <div class="wa-r1-cell">
            <div class="wa-r1-cell-head">
              ${WX.icon(p.icon, { size: 15, color: "var(--c-accent)" })}
              <span style="font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.08em;color:var(--wx-ink-60)">${escapeHtml(p.label.toUpperCase())}</span>
              <span style="margin-left:auto;font-family:var(--wx-mono);font-size:11px;color:${WX.col(p.accent)};letter-spacing:.04em">${escapeHtml((p.band || "").toUpperCase())}</span>
            </div>
            <div style="display:flex;align-items:baseline;gap:4px">
              <span class="wx-tnum wa-r1-cell-num">${fmtPollutant(p.value)}</span>
              <span style="font-family:var(--wx-mono);font-size:11.5px;color:var(--wx-ink-60)">${escapeHtml(p.unit || "")}</span>
            </div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (De Stijl colour blocks)
// ===========================================================
function renderG2(data) {
  const pollutants = pollutantsAvailable(data);
  const tiles = pollutants.slice(0, 4);
  const bandAccent = BAND_ACCENT[data.band] || "green";
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="flex:1;display:flex;gap:4px">
        <div style="flex:1;background:${WX.col(bandAccent)};color:${WX.inkOn(bandAccent)};padding:20px 26px;display:flex;flex-direction:column;justify-content:center">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:76px;line-height:.8">${fmtInt(data.eaqi)}</span>
          <span style="font-family:var(--wx-black);font-size:26px;margin-top:2px">${escapeHtml((data.band || "").toUpperCase())}</span>
          <span style="font-family:var(--wx-mono);font-size:12px;opacity:.85;margin-top:6px">EAQI · DOM ${escapeHtml((data.dominant || "—").toUpperCase())}</span>
        </div>
        <div style="width:42%;flex-shrink:0;background:var(--wx-paper);display:flex;align-items:center;justify-content:center">
          ${WX.icon("leaf", { size: 120, color: WX.col("green") })}
        </div>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:4px">
        ${tiles.map((p) => `
          <div style="background:${WX.col(p.accent)};color:${WX.inkOn(p.accent)};padding:12px 16px;display:flex;flex-direction:column;justify-content:space-between">
            <div style="display:flex;justify-content:space-between;align-items:flex-start">
              <span style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.04em">${escapeHtml(p.label.toUpperCase())}</span>
              ${WX.icon(p.icon, { size: 18, color: WX.inkOn(p.accent) })}
            </div>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:28px;line-height:.85">${fmtPollutant(p.value)}<span style="font-size:12px"> ${escapeHtml(p.unit || "")}</span></span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, whitespace, light numerals)
// ===========================================================
function renderS3(data) {
  const pollutants = pollutantsAvailable(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Air Quality</span>
        <span style="font-size:12px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(data.label || "")} · ${nowTime()}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="display:flex;align-items:flex-end;gap:20px">
        <span class="wx-tnum" style="font-size:76px;font-weight:300;line-height:.82;letter-spacing:-.02em">${fmtInt(data.eaqi)}</span>
        <div style="flex:1">
          <div style="font-size:16px;font-weight:500">${escapeHtml(data.band || "")}</div>
          <div style="font-size:11.5px;color:var(--wx-ink-60);margin-top:3px;letter-spacing:.04em">European AQI · dominant ${escapeHtml((data.dominant || "—").toLowerCase())}</div>
        </div>
        <div style="width:280px">${bandScale(data, { height: 6, gap: 2 })}</div>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(3,1fr);grid-template-rows:1fr 1fr;margin-top:14px">
        ${pollutants.map((p, i) => `
          <div style="border-top:1px solid var(--c-line);border-right:${(i % 3) < 2 ? "1px solid var(--c-line)" : "none"};padding:9px 14px 0;display:flex;flex-direction:column;gap:4px">
            <span style="font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--wx-ink-60);display:flex;align-items:center;gap:6px">
              <span style="width:6px;height:6px;background:${WX.col(p.accent)};display:inline-block"></span>
              ${escapeHtml(p.label)}
            </span>
            <span class="wx-tnum" style="font-size:21px;font-weight:300">${fmtPollutant(p.value)}<span style="font-size:11px;color:var(--wx-ink-60)"> ${escapeHtml(p.unit || "")}</span></span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (ring gauge + horizontal pollutant bars)
// ===========================================================
function aqGauge({ value, max = 100, color, size = 150 }) {
  const sw = 16;
  const r = (size - sw) / 2;
  const cx = size / 2;
  const C = 2 * Math.PI * r;
  const v = Number(value);
  const safe = Number.isFinite(v) ? v : 0;
  const f = Math.max(0, Math.min(1, safe / max));
  const display = value == null ? "—" : String(Math.round(safe));
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="var(--c-line)" stroke-width="${sw}"></circle>
      <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="butt"
              stroke-dasharray="${(C * f).toFixed(1)} ${C.toFixed(1)}"
              transform="rotate(-90 ${cx} ${cx})"></circle>
      <text x="${cx}" y="${cx - 2}" text-anchor="middle" style="font-family:var(--wx-black);font-size:42px;fill:var(--wx-ink)" class="wx-tnum">${escapeHtml(display)}</text>
      <text x="${cx}" y="${cx + 22}" text-anchor="middle" style="font-family:var(--wx-mono);font-size:12px;fill:var(--wx-ink-60);letter-spacing:.1em">EAQI</text>
    </svg>
  `;
}

function renderD4(data) {
  const pollutants = pollutantsAvailable(data);
  const bandAccent = BAND_ACCENT[data.band] || "green";
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 22px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">AIR QUALITY · ${escapeHtml((data.label || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${nowTime()}</span>
      </div>
      <div style="display:flex;gap:26px;flex:1">
        <div style="width:220px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px">
          ${aqGauge({ value: data.eaqi, color: WX.col(bandAccent) })}
          <div style="display:flex;align-items:center;gap:8px">
            ${WX.icon("leaf", { size: 18, color: WX.col("green") })}
            <span style="font-family:var(--wx-black);font-size:18px;color:${WX.col(bandAccent)}">${escapeHtml((data.band || "—").toUpperCase())}</span>
          </div>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:11px;border-left:1px solid var(--c-line);padding-left:24px">
          ${pollutants.map((p) => `
            <div>
              <div style="display:flex;justify-content:space-between;font-family:var(--wx-mono);font-size:11px;margin-bottom:3px">
                <span style="color:var(--wx-ink-60);letter-spacing:.04em;display:flex;align-items:center;gap:6px">
                  ${WX.icon(p.icon, { size: 13, color: WX.col(p.accent) })}
                  ${escapeHtml(p.label.toUpperCase())}
                </span>
                <span style="font-weight:700">${fmtPollutant(p.value)} ${escapeHtml(p.unit || "")}</span>
              </div>
              ${WX.barChart({ value: Number(p.level || 0), max: Number(p.max || 1), color: WX.col(p.accent), height: 8 })}
            </div>
          `).join("")}
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// Variant dispatch
// ===========================================================
const VARIANTS = {
  legacy: renderLegacy,
  r1: renderR1,
  g2: renderG2,
  s3: renderS3,
  d4: renderD4,
};

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
  const variant = ctx.cell.options.variant || "legacy";
  const renderer = VARIANTS[variant] || renderLegacy;

  shadow.innerHTML = renderer(data, size);
}
