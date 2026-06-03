// ha_energy — Home Assistant energy snapshot.
//
// Brand-new widget, no legacy variant. Four directions, picked per-cell
// via the ``variant`` option:
//
//   r1  Refined    Charcoal header + 4-tile flow grid + battery bar
//                  + solar sparkline
//   g2  Geometric  Colour-blocked panels per flow component
//                  (solar/grid/battery/house), big Archivo Black numerals
//   s3  Swiss      Hairline header, light numerals, accent marks per
//                  flow + a battery dot row
//   d4  Data       Sparkline foregrounded with flow indicators; battery
//                  SoC ring + per-component value chips below
//
// Data shape comes from server.py — `solar_w`, `grid_w`, `battery_w`,
// `house_w` (all watts; positive = into the house / charging),
// `battery_soc` (% or null), `solar_today_kwh` (or null), `flow`
// (dominant source), and a 48-slot `sparkline` of solar (or house) power.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

// ----- formatting helpers -----
function fmtW(w) {
  if (w == null) return "—";
  const n = Math.abs(Number(w));
  if (Number.isNaN(n)) return "—";
  if (n >= 1000) return (n / 1000).toFixed(1) + " kW";
  return Math.round(n) + " W";
}

// Same as fmtW but adds a leading sign so negative flows read as
// "exporting" or "charging" naturally in the variant copy.
function fmtSignedW(w) {
  if (w == null) return "—";
  const n = Number(w);
  if (Number.isNaN(n)) return "—";
  const sign = n < 0 ? "−" : "";
  return sign + fmtW(Math.abs(n));
}

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Accent mapping per flow component. Solar = yellow (sun), grid = ink
// (neutral), battery = green (storage), house = blue (consumption).
const FLOW_ACCENT = {
  solar: "yellow",
  grid: "ink",
  battery: "green",
  house: "blue",
};
const FLOW_ICON = {
  solar: "sun",
  grid: "lightning",
  battery: "battery-charging",
  house: "house",
};

function flowAccent(name) { return FLOW_ACCENT[name] || "ink"; }
function flowIcon(name) { return FLOW_ICON[name] || "lightning"; }

// ----- sparkline SVG -----
// Pre-scaled to a 100×30 viewBox + ``preserveAspectRatio="none"`` so a
// stretchy container looks crisp at any rendered width.
function sparkline({ series, color = "var(--wx-yellow)", fill = "var(--wx-yellow-t)" }) {
  if (!series || !series.length) return "";
  const w = 100;
  const h = 30;
  const max = Math.max(1, ...series);
  const dx = series.length > 1 ? w / (series.length - 1) : w;
  const pts = series.map((v, i) => {
    const y = h - (Math.max(0, v) / max) * (h - 2) - 1;
    return `${(i * dx).toFixed(2)},${y.toFixed(2)}`;
  });
  const fillPath = `M0,${h} L${pts.join(" L")} L${w},${h} Z`;
  const linePath = `M${pts.join(" L")}`;
  return `
    <svg width="100%" height="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <path d="${fillPath}" fill="${fill}" />
      <path d="${linePath}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke" />
    </svg>
  `;
}

// SoC ring — circular battery-state indicator used by D4. Outer ring is
// the full 100%; the inner arc is filled to the SoC value.
function socRing({ soc, size = 110, color = "var(--wx-green)" }) {
  if (soc == null) return "";
  const r = size / 2 - 7;
  const c = 2 * Math.PI * r;
  const f = Math.max(0, Math.min(1, soc / 100));
  const dash = `${(c * f).toFixed(2)} ${(c * (1 - f)).toFixed(2)}`;
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--c-line)" stroke-width="6" />
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${dash}" transform="rotate(-90 ${size / 2} ${size / 2})" />
      <text x="50%" y="50%" text-anchor="middle" dy="0.36em" style="font-family:var(--wx-black);font-size:${(size * 0.24).toFixed(1)}px;fill:var(--wx-ink)">${Math.round(soc)}%</text>
      <text x="50%" y="${size * 0.72}" text-anchor="middle" style="font-family:var(--wx-mono);font-size:${(size * 0.085).toFixed(1)}px;fill:var(--wx-ink-60);letter-spacing:.06em">BATTERY</text>
    </svg>
  `;
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

// Build the standard 4-tile layout used by R1 / S3. Each tile is one
// flow component with icon, label, value, and a sub-line for context
// (e.g. "EXPORTING" for negative grid).
function flowTiles(d) {
  const items = [
    { name: "solar", label: "SOLAR", value: d.solar_w, sub: d.solar_w > 0 ? "PRODUCING" : "" },
    {
      name: "grid",
      label: "GRID",
      value: Math.abs(d.grid_w),
      sub: d.grid_w < 0 ? "EXPORTING" : d.grid_w > 0 ? "IMPORTING" : "",
    },
    {
      name: "battery",
      label: "BATTERY",
      value: Math.abs(d.battery_w),
      sub: d.battery_w > 0
        ? "CHARGING"
        : d.battery_w < 0
          ? "DISCHARGING"
          : d.battery_soc != null
            ? `${Math.round(d.battery_soc)}%`
            : "IDLE",
    },
    { name: "house", label: "HOUSE", value: d.house_w, sub: "DRAWING" },
  ];
  return items.map((it, i) => ({
    ...it,
    accent: flowAccent(it.name),
    icon: flowIcon(it.name),
    last: i === items.length - 1,
  }));
}

// ===========================================================
// R1 — REFINED (header + 4-tile grid + battery bar + sparkline)
// ===========================================================
function renderR1(data) {
  const tiles = flowTiles(data);
  const hasSpark = Array.isArray(data.sparkline) && data.sparkline.length > 0;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: `${data.place || ""} · ENERGY`, accent: "yellow", right: data.time || nowTime() })}
      <div style="flex:1;display:grid;grid-template-columns:repeat(2,1fr);grid-template-rows:1fr 1fr;border-top:2px solid var(--wx-ink)">
        ${tiles.map((t, i) => `
          <div style="border-right:${i % 2 === 0 ? "1px solid var(--c-line)" : "none"};border-bottom:${i < 2 ? "1px solid var(--c-line)" : "none"};padding:14px 18px;display:flex;flex-direction:column;justify-content:center;gap:6px">
            <div style="display:flex;align-items:center;gap:8px">
              ${WX.icon(t.icon, { size: 22, color: WX.col(t.accent) })}
              <span style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;color:var(--wx-ink-60)">${escapeHtml(t.label)}</span>
            </div>
            <div style="display:flex;align-items:baseline;gap:6px">
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:30px;line-height:.85">${escapeHtml(fmtW(t.value))}</span>
              ${t.sub ? `<span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);margin-left:auto">${escapeHtml(t.sub)}</span>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
      ${hasSpark ? `
        <div style="display:flex;align-items:center;gap:10px;padding:8px 16px;border-top:2px solid var(--wx-ink);height:48px;flex-shrink:0">
          ${WX.icon("sun", { size: 18, color: WX.col("yellow") })}
          <span style="font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.06em;color:var(--wx-ink-60)">SOLAR · 24H</span>
          ${data.solar_today_kwh != null ? `<span style="margin-left:auto;font-family:var(--wx-mono);font-weight:700;font-size:13px">${escapeHtml(data.solar_today_kwh.toFixed(1))} kWh</span>` : ""}
          <div style="flex:1;height:32px;min-width:60px">
            ${sparkline({ series: data.sparkline, color: WX.col("yellow"), fill: WX.tint("yellow") })}
          </div>
        </div>
      ` : ""}
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-blocked panels per component)
// ===========================================================
function renderG2(data) {
  const tiles = flowTiles(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="display:flex;gap:4px;flex:1">
        ${tiles.slice(0, 2).map((t) => `
          <div style="flex:1;background:${WX.col(t.accent)};color:${WX.inkOn(t.accent)};padding:18px 22px;display:flex;flex-direction:column;justify-content:space-between">
            <div style="display:flex;justify-content:space-between;align-items:flex-start">
              <span style="font-family:var(--wx-mono);font-size:13px;font-weight:700;letter-spacing:.04em">${escapeHtml(t.label)}</span>
              ${WX.icon(t.icon, { size: 22, color: WX.inkOn(t.accent) })}
            </div>
            <div>
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:46px;line-height:.85;display:block">${escapeHtml(fmtW(t.value))}</span>
              ${t.sub ? `<span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.06em;opacity:.85">${escapeHtml(t.sub)}</span>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
      <div style="display:flex;gap:4px;flex:1">
        ${tiles.slice(2).map((t) => `
          <div style="flex:1;background:${WX.col(t.accent)};color:${WX.inkOn(t.accent)};padding:18px 22px;display:flex;flex-direction:column;justify-content:space-between">
            <div style="display:flex;justify-content:space-between;align-items:flex-start">
              <span style="font-family:var(--wx-mono);font-size:13px;font-weight:700;letter-spacing:.04em">${escapeHtml(t.label)}</span>
              ${WX.icon(t.icon, { size: 22, color: WX.inkOn(t.accent) })}
            </div>
            <div>
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:46px;line-height:.85;display:block">${escapeHtml(fmtW(t.value))}</span>
              ${t.sub ? `<span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.06em;opacity:.85">${escapeHtml(t.sub)}</span>` : ""}
            </div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, light numerals, accent dots)
// ===========================================================
function renderS3(data) {
  const tiles = flowTiles(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Energy</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(data.place || "")} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(2,1fr);grid-template-rows:1fr 1fr;column-gap:20px;row-gap:14px">
        ${tiles.map((t) => `
          <div style="display:flex;flex-direction:column;gap:4px">
            <span style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--wx-ink-60);display:flex;align-items:center;gap:6px">
              <span style="width:6px;height:6px;background:${WX.col(t.accent)};display:inline-block"></span>${escapeHtml(t.label)}
            </span>
            <span class="wx-tnum" style="font-size:28px;font-weight:300">${escapeHtml(fmtW(t.value))}</span>
            ${t.sub ? `<span style="font-size:11px;color:var(--wx-ink-60)">${escapeHtml(t.sub)}</span>` : ""}
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (sparkline foregrounded + SoC ring + value chips)
// ===========================================================
function renderD4(data) {
  const hasSpark = Array.isArray(data.sparkline) && data.sparkline.length > 0;
  const hasSoc = data.battery_soc != null;
  const tiles = flowTiles(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 20px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">ENERGY · ${escapeHtml((data.place || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="display:flex;gap:24px;flex:1;min-height:0">
        <div style="flex:1;display:flex;flex-direction:column;min-width:0">
          <div style="display:flex;align-items:baseline;gap:10px">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:48px;line-height:.85">${escapeHtml(fmtW(data.solar_w))}</span>
            <span style="font-family:var(--wx-mono);font-size:12px;color:var(--wx-ink-60)">SOLAR · NET ${escapeHtml(fmtSignedW(data.solar_w - data.house_w))}</span>
          </div>
          ${data.solar_today_kwh != null ? `<div style="font-family:var(--wx-mono);font-size:12px;color:var(--wx-ink-60);margin-top:2px">TODAY ${escapeHtml(data.solar_today_kwh.toFixed(1))} kWh</div>` : ""}
          ${hasSpark ? `
            <div style="flex:1;min-height:50px;margin:6px 0 8px">
              ${sparkline({ series: data.sparkline, color: WX.col("yellow"), fill: WX.tint("yellow") })}
            </div>
          ` : `<div style="flex:1"></div>`}
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            ${tiles.filter((t) => t.name !== "solar").map((t) => `
              <span style="display:inline-flex;align-items:center;gap:6px;background:${WX.tint(t.accent)};color:var(--wx-ink);padding:5px 10px;font-family:var(--wx-mono);font-size:11.5px">
                ${WX.icon(t.icon, { size: 14, color: WX.col(t.accent) })}
                <b>${escapeHtml(t.label)}</b>
                <span class="wx-tnum">${escapeHtml(fmtW(t.value))}</span>
              </span>
            `).join("")}
          </div>
        </div>
        ${hasSoc ? `
          <div style="width:140px;flex-shrink:0;display:flex;align-items:center;justify-content:center;border-left:1px solid var(--c-line);padding-left:18px">
            ${socRing({ soc: data.battery_soc, size: 116, color: WX.col("green") })}
          </div>
        ` : ""}
      </div>
    </div>
  `;
}

// ===========================================================
// dispatch
// ===========================================================
const VARIANTS = {
  r1: renderR1,
  g2: renderG2,
  s3: renderS3,
  d4: renderD4,
};

function renderError(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <div class="root error" style="padding:12px;font-family:system-ui,sans-serif;color:var(--c-danger);display:flex;align-items:center;gap:8px;height:100%;box-sizing:border-box">
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
  const variant = ctx.cell.options.variant || "r1";
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data);
}
