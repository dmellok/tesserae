// weather_now — current conditions card.
//
// Ships five visual directions, picked per-cell via the ``variant``
// option:
//   legacy   The original Bauhaus card we shipped pre-handoff.
//   r1       Refined — charcoal header + dense metric grid + sun
//            row. The "primary" direction; closest to the
//            handoff hero shot.
//   g2       Geometric — De Stijl colour blocks, Archivo Black
//            numerals, big condition tile.
//   s3       Swiss — hairline header, light numerals, marks for
//            each metric.
//   d4       Data — sun arc + bar chart for the four most-glanced
//            stats (humidity / rain / cloud / UV).
//
// All five render from the same ``ctx.data`` shape that server.py
// returns. Legacy paints from the flat fields (``temp``, ``humidity``,
// …); the new directions paint from ``data.metrics`` + ``data.sun``
// minutes. Both shapes always present, so a cell can flip variants
// without re-fetching.

import { WX } from "../weather_core/static/wx-common.js";

// Bumped to a top-of-file constant so a future renderer can flip the
// font-face stack from one place rather than hunting through five
// variants. Variant ``s3`` overrides this with --wx-swiss.
const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }
function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function fmtInt(v) { return v == null ? "—" : String(Math.round(v)); }
function fmtNumeric(v, places = 1) {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n >= 100 ? String(Math.round(n)) : n.toFixed(places);
}
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// ===========================================================
// LEGACY — original Bauhaus card (preserved as-was)
// ===========================================================
function renderLegacy(data, size, windUnit) {
  const isDay = data.is_day !== false;
  const icon = data.icon || (isDay ? "sun" : "moon");
  const label = data.cond || "—";
  const showSun = size === "md" || size === "lg";
  const showRainTag = size !== "xs" && data.rainChance != null;
  const fmtTime = (iso) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    } catch (_e) { return "—"; }
  };

  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/plugins/weather_now/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wn-place">${escapeHtml(data.label || "—")}</span>
        <span class="wn-time">${nowTime()}</span>
      </header>
      <section class="wn-hero">
        <div class="wn-hero-text">
          <div class="wn-temp">${fmtTemp(data.temp)}</div>
          <div class="wn-cond">${escapeHtml(label)}</div>
          ${data.today_max != null || data.feels != null ? `
          <div class="wn-range">
            ${data.today_max != null ? `<span class="wn-range-high">High ${fmtTemp(data.today_max)}</span>` : ""}
            ${data.today_min != null ? `<span class="wn-range-low">Low ${fmtTemp(data.today_min)}</span>` : ""}
            ${data.feels != null ? `<span class="wn-range-feels">Feels ${fmtTemp(data.feels)}</span>` : ""}
          </div>` : ""}
        </div>
        <div class="wn-hero-icon" aria-hidden="true">
          <i class="ph-bold ph-${WX.phName(icon)}"></i>
        </div>
        ${showRainTag ? `<span class="wn-rain-tag">${Math.round(data.rainChance)}% Rain</span>` : ""}
      </section>
      <section class="wn-stats">
        <div class="wn-stat wn-stat--accent">
          <span class="wn-stat-label">Humidity</span>
          <span class="wn-stat-value">${fmtInt(data.humidity)}<small>%</small></span>
        </div>
        <div class="wn-stat wn-stat--surface">
          <span class="wn-stat-label">Wind</span>
          <span class="wn-stat-value">${fmtInt(data.wind)}<small>${windUnit}</small></span>
        </div>
        <div class="wn-stat wn-stat--accent2">
          <span class="wn-stat-label">UV Index</span>
          <span class="wn-stat-value">${data.uv == null ? "—" : Number(data.uv).toFixed(1)}</span>
        </div>
        <div class="wn-stat wn-stat--accent3">
          <span class="wn-stat-label">Rain</span>
          <span class="wn-stat-value">${fmtInt(data.rainChance)}<small>%</small></span>
        </div>
      </section>
      ${showSun ? `
      <section class="wn-sun">
        <div class="wn-sun-cell wn-sun-cell--accent">
          <i class="ph ph-sun-horizon" aria-hidden="true"></i>
          <span class="wn-sun-label">Sunrise</span>
          <span class="wn-sun-time">${fmtTime(data.sunrise)}</span>
        </div>
        <div class="wn-sun-cell wn-sun-cell--inverse">
          <i class="ph ph-moon" aria-hidden="true"></i>
          <span class="wn-sun-label">Sunset</span>
          <span class="wn-sun-time">${fmtTime(data.sunset)}</span>
        </div>
      </section>` : ""}
    </div>
  `;
}

// ===========================================================
// Shared helpers for the four new directions
// ===========================================================
function styleBlock() {
  // Returns the <link> tags every variant needs. Bauhaus-wx supplies
  // the design tokens; widget-bauhaus is kept too because the legacy
  // path still uses its .wb-* selectors.
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
    <link rel="stylesheet" href="/plugins/weather_now/client.css">
  `;
}

function metricsAvailable(data) {
  return Array.isArray(data.metrics) ? data.metrics : [];
}

function fmtMetricValue(m) {
  if (m.value == null) return "—";
  return fmtNumeric(m.value, 1);
}

function hiLoFeels(data) {
  return `
    <span style="font-family:var(--wx-mono);font-size:13px;color:var(--wx-ink-60);letter-spacing:.02em">
      <b style="color:var(--wx-ink)">HIGH ${fmtTemp(data.high)}</b>　LOW ${fmtTemp(data.low)}　FEELS ${fmtTemp(data.feels)}
    </span>
  `;
}

// ===========================================================
// R1 — REFINED
// Charcoal header + dense hairline-organised grid + sun row.
// ===========================================================
function renderR1(data) {
  const metrics = metricsAvailable(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: data.label || "—", accent: "blue", right: nowTime() })}
      <div style="display:flex;align-items:center;padding:16px 24px;gap:20px">
        <div style="flex:1">
          <div style="display:flex;align-items:flex-start;gap:2px">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:86px;line-height:.82">${fmtTemp(data.temp)}</span>
          </div>
          <div style="font-weight:800;font-size:20px;letter-spacing:.02em;margin-top:6px">${escapeHtml((data.cond || "").toUpperCase())}</div>
          <div style="margin-top:6px">${hiLoFeels(data)}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:8px">
          ${data.rainChance != null ? `
            <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700;letter-spacing:.06em;color:${WX.inkOn("blue")};background:${WX.col("blue")};padding:3px 9px">${Math.round(data.rainChance)}% RAIN</span>
          ` : ""}
          ${WX.icon(data.icon || "cloud", { size: 96, color: "var(--wx-ink)" })}
        </div>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(4,1fr);grid-template-rows:1fr 1fr;border-top:2px solid var(--wx-ink)">
        ${metrics.map((m, i) => `
          <div style="border-right:${(i % 4) < 3 ? "1px solid var(--c-line)" : "none"};border-bottom:${i < 4 ? "1px solid var(--c-line)" : "none"};padding:11px 14px;display:flex;flex-direction:column;justify-content:center;gap:5px">
            <div style="display:flex;align-items:center;gap:8px">
              ${WX.icon(m.icon, { size: 22, color: WX.col(m.accent) })}
              <span style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;color:var(--wx-ink-60)">${escapeHtml(m.label.toUpperCase())}</span>
            </div>
            <div style="display:flex;align-items:baseline;gap:4px">
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:27px">${fmtMetricValue(m)}</span>
              <span style="font-family:var(--wx-mono);font-size:13px;color:var(--wx-ink-60)">${escapeHtml(m.unit || "")}</span>
            </div>
          </div>
        `).join("")}
      </div>
      <div style="display:flex;border-top:2px solid var(--wx-ink)">
        <div style="flex:1;display:flex;align-items:center;gap:9px;padding:8px 16px;border-right:1px solid var(--c-line)">
          ${WX.icon("sunrise", { size: 18, color: WX.col("yellow") })}
          <span style="font-family:var(--wx-mono);font-size:13px;letter-spacing:.04em">SUNRISE</span>
          <span style="margin-left:auto;font-family:var(--wx-mono);font-weight:700;font-size:14px">${escapeHtml(data.sun?.rise || "")}</span>
        </div>
        <div style="flex:1;display:flex;align-items:center;gap:9px;padding:8px 16px">
          ${WX.icon("sunset", { size: 18, color: "var(--wx-ink)" })}
          <span style="font-family:var(--wx-mono);font-size:13px;letter-spacing:.04em">SUNSET</span>
          <span style="margin-left:auto;font-family:var(--wx-mono);font-weight:700;font-size:14px">${escapeHtml(data.sun?.set || "")}</span>
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (De Stijl colour blocks)
// ===========================================================
function renderG2(data) {
  const top4 = metricsAvailable(data).slice(0, 4);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="display:flex;gap:4px">
        <div style="flex:1;background:var(--wx-paper);padding:16px 22px;display:flex;flex-direction:column;justify-content:center">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:80px;line-height:.82">${fmtTemp(data.temp)}</span>
          <span style="font-weight:700;font-size:18px;margin-top:4px">${escapeHtml((data.cond || "").toUpperCase())}</span>
          <div style="margin-top:6px">${hiLoFeels(data)}</div>
        </div>
        <div style="width:42%;flex-shrink:0;background:${WX.col("blue")};color:${WX.inkOn("blue")};display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px">
          ${data.rainChance != null ? `
            <span style="font-family:var(--wx-mono);font-size:12px;font-weight:700">${Math.round(data.rainChance)}% RAIN</span>
          ` : ""}
          ${WX.icon(data.icon || "cloud", { size: 92, color: WX.inkOn("blue") })}
        </div>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(4,1fr);gap:4px">
        ${top4.map((m) => `
          <div style="background:${WX.col(m.accent)};color:${WX.inkOn(m.accent)};padding:12px 16px;display:flex;flex-direction:column;justify-content:space-between">
            <div style="display:flex;justify-content:space-between;align-items:flex-start">
              <span style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.04em">${escapeHtml(m.label.toUpperCase())}</span>
              ${WX.icon(m.icon, { size: 18, color: WX.inkOn(m.accent) })}
            </div>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:30px;line-height:.85">${fmtMetricValue(m)}<span style="font-size:14px"> ${escapeHtml(m.unit || "")}</span></span>
          </div>
        `).join("")}
      </div>
      <div style="display:flex;gap:4px">
        <div style="flex:1;background:${WX.col("yellow")};color:var(--wx-ink);padding:7px 16px;font-family:var(--wx-mono);font-size:12px;font-weight:700;display:flex;justify-content:space-between">
          <span>SUNRISE</span><span>${escapeHtml(data.sun?.rise || "")}</span>
        </div>
        <div style="flex:1;background:var(--wx-ink);color:var(--wx-paper);padding:7px 16px;font-family:var(--wx-mono);font-size:12px;font-weight:700;display:flex;justify-content:space-between">
          <span>SUNSET</span><span>${escapeHtml(data.sun?.set || "")}</span>
        </div>
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, whitespace, light numerals)
// ===========================================================
function renderS3(data) {
  const metrics = metricsAvailable(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">${escapeHtml(data.label || "")}</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${nowTime()}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="display:flex;align-items:center;gap:20px">
        <span class="wx-tnum" style="font-size:78px;font-weight:300;line-height:.85;letter-spacing:-.02em">${fmtTemp(data.temp)}</span>
        <div style="flex:1">
          <div style="font-size:16px;font-weight:500">${escapeHtml(data.cond || "")}</div>
          <div style="margin-top:4px">${hiLoFeels(data)}</div>
        </div>
        ${WX.icon(data.icon || "cloud", { size: 64, color: "var(--wx-ink)" })}
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(4,1fr);grid-template-rows:1fr 1fr;margin-top:12px">
        ${metrics.map((m, i) => `
          <div style="border-top:1px solid var(--c-line);border-right:${(i % 4) < 3 ? "1px solid var(--c-line)" : "none"};padding:10px 12px 0;display:flex;flex-direction:column;gap:4px">
            <span style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--wx-ink-60);display:flex;align-items:center;gap:6px">
              <span style="width:6px;height:6px;background:${WX.col(m.accent)};display:inline-block"></span>
              ${escapeHtml(m.label)}
            </span>
            <span class="wx-tnum" style="font-size:24px;font-weight:300">${fmtMetricValue(m)}<span style="font-size:13px;color:var(--wx-ink-60)"> ${escapeHtml(m.unit || "")}</span></span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (sun arc + value bars for top metrics)
// ===========================================================
function renderD4(data) {
  const metrics = metricsAvailable(data);
  // Cap values by their natural max so the bar fill is meaningful;
  // UV scale tops out at 11. Anything not in this map gets capped at
  // 100 (humidity / cloud / rain prob are already %-shaped).
  const max = { Humidity: 100, Rain: 100, Cloud: 100, "UV Index": 11 };
  const bars = metrics.filter((m) => max[m.label] != null).slice(0, 4);
  const sunHas = data.sun && data.sun.riseMin != null && data.sun.setMin != null && data.sun.nowMin != null;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 20px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">${escapeHtml((data.label || "").toUpperCase())} · NOW</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${nowTime()}</span>
      </div>
      <div style="display:flex;gap:24px;flex:1">
        <div style="flex:1;display:flex;flex-direction:column">
          <div style="display:flex;align-items:center;gap:12px">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:64px;line-height:.82">${fmtTemp(data.temp)}</span>
            <div>
              <div style="font-weight:700;font-size:15px">${escapeHtml(data.cond || "")}</div>
              <div style="font-family:var(--wx-mono);font-size:12.5px;color:var(--wx-ink-60);margin-top:3px">H ${fmtTemp(data.high)} · L ${fmtTemp(data.low)} · FL ${fmtTemp(data.feels)}</div>
            </div>
          </div>
          ${sunHas ? `<div style="flex:1;display:flex;align-items:center;justify-content:center">${WX.sunArc({ rise: data.sun.riseMin, set: data.sun.setMin, now: data.sun.nowMin, color: WX.col("yellow"), width: 230, height: 120 })}</div>` : ""}
        </div>
        <div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:14px;border-left:1px solid var(--c-line);padding-left:22px">
          ${bars.map((m) => `
            <div>
              <div style="display:flex;justify-content:space-between;font-family:var(--wx-mono);font-size:12.5px;margin-bottom:5px">
                <span style="color:var(--wx-ink-60);letter-spacing:.04em">${escapeHtml(m.label.toUpperCase())}</span>
                <span style="font-weight:700">${fmtMetricValue(m)} ${escapeHtml(m.unit || "")}</span>
              </div>
              ${WX.barChart({ value: Number(m.value), max: max[m.label], color: WX.col(m.accent) })}
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
    <link rel="stylesheet" href="/plugins/weather_now/client.css">
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
  const units = ctx.cell.options.units === "imperial" ? "imperial" : "metric";
  const windUnit = units === "imperial" ? "mph" : "km/h";
  const variant = ctx.cell.options.variant || "legacy";
  const renderer = VARIANTS[variant] || renderLegacy;

  shadow.innerHTML = renderer(data, size, windUnit);
}
