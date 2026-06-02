// weather_forecast — 5-day forecast card.
//
// Ships five visual directions, picked per-cell via the ``variant``
// option:
//   legacy   The original Bauhaus 5-column card we shipped pre-handoff.
//   r1       Refined — charcoal header, 5 columns split by hairlines,
//            today claims the accent block.
//   g2       Geometric — De Stijl colour blocks, Archivo Black numerals.
//   s3       Swiss — hairline header, light numerals, divider rules.
//   d4       Data — horizontal range bars, day-by-day rows.
//
// All five render from the same ``ctx.data`` shape that server.py
// returns. Legacy paints from the flat day fields (``code``, ``high``,
// ``low``…) and computes its own day labels; the new directions paint
// from the structured ``days[*].day/icon/cond/hi/lo/today`` plus the
// top-level ``rangeLo`` / ``rangeHi`` / ``time``. Both shapes are
// always present so a cell can flip variants without re-fetching.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

// Legacy-only WMO lookup. The new variants get cond/icon already
// resolved server-side so they don't need this.
const WMO = {
  0:  ["sun",             "Clear"],
  1:  ["sun",             "Clear"],
  2:  ["cloud-sun",       "Partly"],
  3:  ["cloud",           "Overcast"],
  45: ["cloud-fog",       "Fog"],
  48: ["cloud-fog",       "Fog"],
  51: ["cloud-rain",      "Drizzle"],
  53: ["cloud-rain",      "Drizzle"],
  55: ["cloud-rain",      "Drizzle"],
  56: ["snowflake",       "Freezing"],
  57: ["snowflake",       "Freezing"],
  61: ["cloud-rain",      "Rain"],
  63: ["cloud-rain",      "Rain"],
  65: ["cloud-rain",      "Heavy rain"],
  66: ["snowflake",       "Freezing"],
  67: ["snowflake",       "Freezing"],
  71: ["snowflake",       "Snow"],
  73: ["snowflake",       "Snow"],
  75: ["snowflake",       "Heavy snow"],
  77: ["snowflake",       "Snow"],
  80: ["cloud-rain",      "Showers"],
  81: ["cloud-rain",      "Showers"],
  82: ["cloud-rain",      "Showers"],
  85: ["snowflake",       "Snow"],
  86: ["snowflake",       "Heavy snow"],
  95: ["cloud-lightning", "Storm"],
  96: ["cloud-lightning", "Storm"],
  99: ["cloud-lightning", "Storm"],
};
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function escapeHtml(s) { return WX.escapeHtml(s); }
function describe(code) { return WMO[code] || ["cloud", "—"]; }
function fmtTemp(v) { return v == null ? "—" : Math.round(v) + "°"; }
function fmtInt(v) { return v == null ? "—" : String(Math.round(v)); }
function dayLabel(idx, position) {
  if (position === 0) return "Today";
  if (position === 1) return "Tom";
  return idx >= 0 && idx < 7 ? DAY_NAMES[idx] : "—";
}
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// ===========================================================
// LEGACY — original Bauhaus card (preserved as-was)
// ===========================================================
function dayTone(position) {
  if (position === 0) return "accent";
  return position % 2 === 1 ? "surface" : "surface2";
}

function computeRangeBars(days) {
  const lows = days.map((d) => (typeof d.low === "number" ? d.low : null)).filter((v) => v != null);
  const highs = days.map((d) => (typeof d.high === "number" ? d.high : null)).filter((v) => v != null);
  if (!lows.length || !highs.length) return days.map(() => null);
  const weekMin = Math.min(...lows);
  const weekMax = Math.max(...highs);
  const span = weekMax - weekMin || 1;
  return days.map((d) => {
    if (typeof d.low !== "number" || typeof d.high !== "number") return null;
    const left = ((d.low - weekMin) / span) * 100;
    const right = 100 - ((d.high - weekMin) / span) * 100;
    return { left: left.toFixed(1), right: right.toFixed(1) };
  });
}

function dayBlock(day, position, showRain, rangeBar) {
  const [icon, label] = describe(day.code);
  const tone = dayTone(position);
  const rainPct = day.rain == null ? null : Math.round(day.rain);
  const rainWet = rainPct != null && rainPct >= 30;
  return `
    <article class="wf-day wf-day--${tone}${position === 0 ? " is-today" : ""}">
      <div class="wf-day-name">${escapeHtml(dayLabel(day.weekday, position))}</div>
      <i class="ph-bold ph-${icon} wf-day-icon" aria-hidden="true"></i>
      <div class="wf-day-cond">${escapeHtml(label)}</div>
      ${rangeBar ? `
      <div class="wf-day-range" aria-hidden="true">
        <span class="wf-day-range-fill" style="left: ${rangeBar.left}%; right: ${rangeBar.right}%"></span>
      </div>` : ""}
      <div class="wf-day-temps">
        <span class="wf-day-high">${fmtTemp(day.high)}</span>
        <span class="wf-day-low">${fmtTemp(day.low)}</span>
      </div>
      ${showRain && rainPct != null ? `
      <div class="wf-day-rain${rainWet ? " is-wet" : ""}">
        <i class="ph-bold ph-drop" aria-hidden="true"></i>
        <span>${rainPct}%</span>
      </div>` : ""}
    </article>
  `;
}

function renderLegacy(data, size) {
  const days = Array.isArray(data.days) ? data.days : [];
  if (!days.length) return renderError("no forecast data");
  const showRain = size === "md" || size === "lg";
  const rangeBars = computeRangeBars(days);
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_forecast/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wf-title">${data.label ? escapeHtml(data.label) + " · " : ""}5-day forecast</span>
        <span class="wf-time">${nowTime()}</span>
      </header>
      <section class="wf-days">
        ${days.map((d, i) => dayBlock(d, i, showRain, rangeBars[i])).join("")}
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
    <link rel="stylesheet" href="/plugins/weather_forecast/client.css">
  `;
}

// Each "structured" day comes from server.py already shaped for these
// variants (day/icon/cond/hi/lo/today). Anything missing — e.g. an
// older cached payload — falls back to the legacy field next door.
function shapeDay(day, position) {
  const code = day.code;
  const [legacyIcon, legacyLabel] = describe(code);
  return {
    day: day.day || dayLabel(day.weekday, position),
    icon: day.icon || legacyIcon,
    cond: day.cond || legacyLabel,
    hi: day.hi != null ? day.hi : (day.high != null ? Math.round(day.high) : null),
    lo: day.lo != null ? day.lo : (day.low != null ? Math.round(day.low) : null),
    rain: day.rain == null ? null : Math.round(day.rain),
    today: day.today === true || position === 0,
  };
}

function rangeBounds(data, structuredDays) {
  let lo = data.rangeLo;
  let hi = data.rangeHi;
  if (lo == null || hi == null) {
    const los = structuredDays.map((d) => d.lo).filter((v) => typeof v === "number");
    const his = structuredDays.map((d) => d.hi).filter((v) => typeof v === "number");
    if (los.length) lo = Math.min(...los);
    if (his.length) hi = Math.max(...his);
  }
  return { lo, hi };
}

function segLeftPct(d, lo, hi) {
  if (lo == null || hi == null || d.lo == null) return 0;
  const span = (hi - lo) || 1;
  return ((d.lo - lo) / span) * 100;
}
function segWidthPct(d, lo, hi) {
  if (lo == null || hi == null || d.lo == null || d.hi == null) return 0;
  const span = (hi - lo) || 1;
  return ((d.hi - d.lo) / span) * 100;
}

// ===========================================================
// R1 — REFINED
// Charcoal header + 5 columns separated by hairlines. Today claims
// a solid-blue block; other columns stay on paper.
// ===========================================================
function renderR1(data) {
  const days = (Array.isArray(data.days) ? data.days : []).map(shapeDay);
  if (!days.length) return renderError("no forecast data");
  const { lo, hi } = rangeBounds(data, days);
  const place = (data.place || data.label || "—").toUpperCase();
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: place + " · 5-DAY FORECAST", accent: "blue", right: data.time || nowTime() })}
      <div style="flex:1;display:grid;grid-template-columns:repeat(${days.length},1fr)">
        ${days.map((d, i) => {
          const isToday = d.today;
          const colorOn = isToday ? "#fff" : "var(--wx-ink)";
          const bg = isToday ? WX.col("blue") : "transparent";
          const track = isToday ? "rgba(255,255,255,.3)" : "rgba(27,26,22,.14)";
          const fill = isToday ? "#fff" : "var(--wx-ink)";
          const segL = segLeftPct(d, lo, hi);
          const segW = segWidthPct(d, lo, hi);
          return `
            <div style="border-right:${i < days.length - 1 ? "1px solid var(--wx-ink)" : "none"};background:${bg};color:${colorOn};display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:14px 12px;gap:10px">
              <span style="font-family:var(--wx-black);font-size:15px;letter-spacing:.04em">${escapeHtml(d.day)}</span>
              ${WX.icon(d.icon, { size: 48, color: colorOn })}
              <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.06em;opacity:.85">${escapeHtml((d.cond || "").toUpperCase())}</span>
              <div style="width:100%;height:4px;background:${track};position:relative">
                <div style="position:absolute;left:${segL.toFixed(1)}%;width:${segW.toFixed(1)}%;height:100%;background:${fill}"></div>
              </div>
              <div style="display:flex;align-items:baseline;gap:8px">
                <span class="wx-tnum" style="font-family:var(--wx-black);font-size:26px">${fmtInt(d.hi)}°</span>
                <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:14px;opacity:.7">${fmtInt(d.lo)}°</span>
              </div>
              <span style="display:flex;align-items:center;gap:5px;font-family:var(--wx-mono);font-size:12px;opacity:.85">
                ${WX.icon("drop", { size: 12, color: isToday ? "#fff" : WX.col("blue") })}
                ${d.rain == null ? "—" : d.rain + "%"}
              </span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (De Stijl colour blocks)
// Solid-blue today tile, paper tiles for the other days, charcoal
// gutters between them.
// ===========================================================
function renderG2(data) {
  const days = (Array.isArray(data.days) ? data.days : []).map(shapeDay);
  if (!days.length) return renderError("no forecast data");
  const place = (data.place || data.label || "—").toUpperCase();
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 16px;display:flex;align-items:center;gap:10px;flex-shrink:0">
        <span style="width:13px;height:13px;background:${WX.col("blue")}"></span>
        <span style="font-family:var(--wx-black);font-size:15px">${escapeHtml(place)} · 5-DAY</span>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(${days.length},1fr);gap:4px">
        ${days.map((d) => {
          const solid = d.today;
          const bg = solid ? WX.col("blue") : "var(--wx-paper)";
          const fg = solid ? "#fff" : "var(--wx-ink)";
          return `
            <div style="background:${bg};color:${fg};display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:14px 10px;gap:10px">
              <span style="font-family:var(--wx-black);font-size:16px">${escapeHtml(d.day)}</span>
              ${WX.icon(d.icon, { size: 46, color: fg })}
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:30px">${fmtInt(d.hi)}°</span>
              <span style="font-family:var(--wx-mono);font-size:12px">${fmtInt(d.lo)}° · ${d.rain == null ? "—" : d.rain + "%"}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, whitespace, light numerals)
// ===========================================================
function renderS3(data) {
  const days = (Array.isArray(data.days) ? data.days : []).map(shapeDay);
  if (!days.length) return renderError("no forecast data");
  const place = data.place || data.label || "";
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:18px 24px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">5-Day Forecast</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(place)}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0 0"></div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(${days.length},1fr)">
        ${days.map((d, i) => `
          <div style="border-right:${i < days.length - 1 ? "1px solid rgba(27,26,22,.16)" : "none"};display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:16px 10px;gap:10px">
            <span style="font-size:11px;letter-spacing:.12em;color:${d.today ? "var(--wx-ink)" : "var(--wx-ink-60)"};font-weight:${d.today ? 700 : 400}">${escapeHtml((d.day || "").toUpperCase())}</span>
            ${WX.icon(d.icon, { size: 42, color: "var(--wx-ink)" })}
            <div style="display:flex;align-items:baseline;gap:7px">
              <span class="wx-tnum" style="font-size:28px;font-weight:300">${fmtInt(d.hi)}°</span>
              <span class="wx-tnum" style="font-size:15px;font-weight:300;color:var(--wx-ink-60)">${fmtInt(d.lo)}°</span>
            </div>
            <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${d.rain == null ? "—" : d.rain + "%"}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (horizontal range bars per day)
// ===========================================================
function renderD4(data) {
  const days = (Array.isArray(data.days) ? data.days : []).map(shapeDay);
  if (!days.length) return renderError("no forecast data");
  const { lo, hi } = rangeBounds(data, days);
  const rangeLabel = lo != null && hi != null ? `${fmtInt(lo)}° – ${fmtInt(hi)}° RANGE` : "";
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 20px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">5-DAY FORECAST</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(rangeLabel)}</span>
      </div>
      <div style="flex:1;display:flex;flex-direction:column">
        ${days.map((d) => {
          const segL = segLeftPct(d, lo, hi);
          const segW = segWidthPct(d, lo, hi);
          const dayColor = d.today ? WX.col("blue") : "var(--wx-ink)";
          const barColor = d.today ? WX.col("blue") : WX.col("ink");
          return `
            <div style="flex:1;display:flex;align-items:center;gap:14px;border-top:1px solid rgba(27,26,22,.14);padding:0 2px">
              <span style="width:56px;font-family:var(--wx-black);font-size:14px;color:${dayColor}">${escapeHtml((d.day || "").toUpperCase())}</span>
              ${WX.icon(d.icon, { size: 22, color: "var(--wx-ink)" })}
              <span class="wx-tnum" style="width:40px;text-align:right;font-family:var(--wx-mono);font-size:13px;color:var(--wx-ink-60)">${fmtInt(d.lo)}°</span>
              <div style="flex:1;height:8px;background:rgba(27,26,22,.1);position:relative">
                <div style="position:absolute;left:${segL.toFixed(1)}%;width:${segW.toFixed(1)}%;height:100%;background:${barColor}"></div>
              </div>
              <span class="wx-tnum" style="width:40px;font-family:var(--wx-black);font-size:16px">${fmtInt(d.hi)}°</span>
              <span style="width:64px;display:flex;align-items:center;justify-content:flex-end;gap:5px;font-family:var(--wx-mono);font-size:12px;color:var(--wx-ink-60)">
                ${WX.icon("drop", { size: 11, color: WX.col("blue") })}
                ${d.rain == null ? "—" : d.rain + "%"}
              </span>
            </div>
          `;
        }).join("")}
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
    <link rel="stylesheet" href="/plugins/weather_forecast/client.css">
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
