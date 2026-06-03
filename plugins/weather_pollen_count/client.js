// weather_pollen_count — pollen card.
//
// Ships five visual directions, picked per-cell via the ``variant``
// option:
//   legacy   The original Bauhaus card we shipped pre-handoff.
//   r1       Refined — charcoal header + level word + flower hero +
//            three species rows with hairline bars.
//   g2       Geometric — De Stijl colour blocks; one tile per species,
//            flower hero panel on the right.
//   s3       Swiss — hairline header, light numerals, marks for each
//            species.
//   d4       Data — five-segment bars per species + overall level pill.
//
// All five render from the same ``ctx.data`` shape that server.py
// returns. Legacy paints from the flat fields (``grass``, ``tree``,
// ``weed``, ``grass_label``); the new directions paint from
// ``data.level``, ``data.breakdown`` and ``data.scaleMax``. Both shapes
// always present, so a cell can flip variants without re-fetching.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

const BANDS = [
  { max: 30,  label: "Low",      cls: "pl-band--low"  },
  { max: 100, label: "Moderate", cls: "pl-band--mod"  },
  { max: 300, label: "High",     cls: "pl-band--high" },
  { max: Infinity, label: "Very High", cls: "pl-band--vhigh" },
];

// Map an overall level word to one of the Spectra 6 accents. Aligned
// with the air-quality colour ramp: low=green, moderate=yellow,
// high=red, very high=ink (so the bar still reads on a 6-colour panel).
const LEVEL_ACCENT = {
  "Low": "green",
  "Moderate": "yellow",
  "High": "red",
  "Very High": "ink",
  "Off Season": "muted",
};

function bandForCount(v) {
  if (v == null) return { label: "—", cls: "" };
  return BANDS.find((b) => v <= b.max) || BANDS[BANDS.length - 1];
}
function bandForLabel(label) {
  if (!label) return null;
  const map = {
    "low":       BANDS[0],
    "moderate":  BANDS[1],
    "high":      BANDS[2],
    "very high": BANDS[3],
    "extreme":   BANDS[3],
    "off season": { label: "Off Season", cls: "" },
  };
  return map[label.toLowerCase()] || null;
}

function fmtInt(v) { return v == null ? "—" : String(Math.round(v)); }
function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
function escapeHtml(s) { return WX.escapeHtml(s); }

// ===========================================================
// LEGACY — original Bauhaus card (preserved as-was)
// ===========================================================
function renderLegacy(data, size) {
  // Prefer the scraped label when the source is text-only (MPC has no
  // grains count), fall back to the count-driven band otherwise.
  const band = bandForLabel(data.grass_label) || bandForCount(data.grass);
  const showCount = data.grass != null && !data.grass_label;

  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/weather_pollen_count/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="pl-place">${escapeHtml(data.label || "—")}</span>
        <span class="pl-time">${nowTime()}</span>
      </header>
      <section class="pl-hero ${band.cls}">
        <div class="pl-hero-text">
          <div class="pl-level">${escapeHtml(band.label)}</div>
          <div class="pl-headline">Grass Pollen</div>
          ${showCount ? `<div class="pl-count">${fmtInt(data.grass)}<small>grains/m³</small></div>` : ""}
          ${data.source ? `<div class="pl-source">via ${escapeHtml(data.source)}</div>` : ""}
        </div>
        <div class="pl-hero-icon" aria-hidden="true">
          <i class="ph-bold ph-flower-tulip"></i>
        </div>
      </section>
      <section class="pl-stats">
        <div class="pl-stat pl-stat--accent">
          <i class="ph-bold ph-tree pl-stat-icon" aria-hidden="true"></i>
          <span class="pl-stat-label">Tree</span>
          <span class="pl-stat-value">${fmtInt(data.tree)}</span>
        </div>
        <div class="pl-stat pl-stat--accent2">
          <i class="ph-bold ph-plant pl-stat-icon" aria-hidden="true"></i>
          <span class="pl-stat-label">Grass</span>
          <span class="pl-stat-value">${fmtInt(data.grass)}</span>
        </div>
        <div class="pl-stat pl-stat--accent3">
          <i class="ph-bold ph-leaf pl-stat-icon" aria-hidden="true"></i>
          <span class="pl-stat-label">Weed</span>
          <span class="pl-stat-value">${fmtInt(data.weed)}</span>
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
    <link rel="stylesheet" href="/plugins/weather_pollen_count/client.css">
  `;
}

function breakdownRows(data) {
  return Array.isArray(data.breakdown) ? data.breakdown : [];
}

function levelAccent(level) {
  return LEVEL_ACCENT[level] || "green";
}

function sourceText(data) {
  return data.source ? String(data.source).toUpperCase() : "POLLEN";
}

// ===========================================================
// R1 — REFINED
// Charcoal header + level word + flower hero + species bars.
// ===========================================================
function renderR1(data) {
  const rows = breakdownRows(data);
  const accent = levelAccent(data.level);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: (data.place || data.label || "—"), accent: "green", right: data.time || nowTime() })}
      <div style="flex:1;display:flex">
        <div style="flex:1;padding:18px 24px;display:flex;flex-direction:column;justify-content:center">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:64px;line-height:.82;color:${WX.col(accent)}">${escapeHtml(data.level || "—")}</span>
          <span style="font-weight:800;font-size:18px;letter-spacing:.03em;margin-top:8px">${escapeHtml((data.type || "Mixed").toUpperCase())}</span>
          <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);margin-top:6px;letter-spacing:.04em">${escapeHtml(sourceText(data))}</span>
        </div>
        <div style="width:38%;flex-shrink:0;background:${WX.tint("green")};display:flex;align-items:center;justify-content:center">
          ${WX.icon("flower", { size: 110, color: WX.col("green") })}
        </div>
      </div>
      <div style="display:flex;border-top:2px solid var(--wx-ink)">
        ${rows.map((b, i) => `
          <div style="flex:1;padding:11px 16px;border-right:${i < rows.length - 1 ? "1px solid var(--c-line)" : "none"};display:flex;flex-direction:column;gap:6px">
            <div style="display:flex;align-items:center;justify-content:space-between">
              <span style="display:flex;align-items:center;gap:8px;font-family:var(--wx-mono);font-size:11px;letter-spacing:.08em;color:var(--wx-ink-60)">
                ${WX.icon(b.icon, { size: 14, color: WX.col(b.accent) })}
                ${escapeHtml(b.label.toUpperCase())}
              </span>
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:20px">${escapeHtml(String(b.value))}</span>
            </div>
            <div style="height:5px;background:var(--c-line)">
              <div style="width:${Number(b.level || 0).toFixed(1)}%;height:100%;background:${WX.col(b.accent)}"></div>
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
  const rows = breakdownRows(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px">
      <div style="flex:1;display:flex;gap:4px">
        <div style="flex:1;background:${WX.col("green")};color:${WX.inkOn("green")};padding:20px 24px;display:flex;flex-direction:column;justify-content:center">
          <span class="wx-tnum" style="font-family:var(--wx-black);font-size:68px;line-height:.8">${escapeHtml(data.level || "—")}</span>
          <span style="font-weight:700;font-size:17px;margin-top:8px">${escapeHtml((data.type || "Mixed").toUpperCase())}</span>
          <span style="font-family:var(--wx-mono);font-size:11px;opacity:.85;margin-top:6px">${escapeHtml(sourceText(data))}</span>
        </div>
        <div style="width:38%;flex-shrink:0;background:var(--wx-paper);display:flex;align-items:center;justify-content:center">
          ${WX.icon("flower", { size: 108, color: WX.col("green") })}
        </div>
      </div>
      <div style="display:flex;gap:4px">
        ${rows.map((b) => `
          <div style="flex:1;background:${WX.col(b.accent)};color:${WX.inkOn(b.accent)};padding:11px 16px;display:flex;align-items:center;justify-content:space-between">
            <span style="display:flex;align-items:center;gap:8px;font-family:var(--wx-mono);font-size:12px;font-weight:700">
              ${WX.icon(b.icon, { size: 16, color: WX.inkOn(b.accent) })}
              ${escapeHtml(b.label.toUpperCase())}
            </span>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:26px">${escapeHtml(String(b.value))}</span>
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
  const rows = breakdownRows(data);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Pollen</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(data.place || data.label || "")} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="display:flex;align-items:center;gap:20px">
        <span class="wx-tnum" style="font-size:58px;font-weight:300;line-height:.85;letter-spacing:-.01em">${escapeHtml(data.level || "—")}</span>
        <div style="flex:1">
          <div style="font-size:15px;letter-spacing:.04em;text-transform:uppercase">${escapeHtml(data.type || "Mixed")}</div>
          <div style="font-size:11px;color:var(--wx-ink-60);margin-top:3px">${escapeHtml(data.source || "")}</div>
        </div>
        ${WX.icon("flower", { size: 52, color: "var(--wx-ink)", weight: "regular" })}
      </div>
      <div style="flex:1;display:flex;align-items:flex-end;gap:44px;padding-bottom:4px;margin-top:8px">
        ${rows.map((b) => `
          <div style="display:flex;flex-direction:column;gap:5px">
            <span style="font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--wx-ink-60);display:flex;align-items:center;gap:6px">
              <span style="width:6px;height:6px;background:${WX.col(b.accent)};display:inline-block"></span>
              ${escapeHtml(b.label)}
            </span>
            <span class="wx-tnum" style="font-size:28px;font-weight:300">${escapeHtml(String(b.value))}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (overall level pill + segmented species bars)
// ===========================================================
function renderD4(data) {
  const rows = breakdownRows(data);
  const accent = levelAccent(data.level);
  const segs = 5;
  // Same banding as the JSX reference: <20=Low, <50=Mod, <80=High,
  // else Extreme. Scaled to the `level` 0–100 normalised position the
  // server hands us, so the bar reads "how full" rather than raw count.
  const SCALE = ["None", "Low", "Mod", "High", "Extreme"];
  function activeFor(level) {
    const l = Number(level || 0);
    if (l <= 0) return 0;
    if (l < 20) return 1;
    if (l < 50) return 2;
    if (l < 80) return 3;
    return 4;
  }
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 22px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">POLLEN · ${escapeHtml((data.place || data.label || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(sourceText(data))}</span>
      </div>
      <div style="display:flex;align-items:baseline;gap:12px">
        <span class="wx-tnum" style="font-family:var(--wx-black);font-size:40px;color:${WX.col(accent)}">${escapeHtml(data.level || "—")}</span>
        <span style="font-family:var(--wx-mono);font-size:12.5px;color:var(--wx-ink-60)">OVERALL · ${escapeHtml((data.type || "Mixed").toUpperCase())}</span>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:14px">
        ${rows.map((b) => {
          const active = activeFor(b.level);
          return `
            <div style="display:flex;align-items:center;gap:16px">
              <span style="width:72px;display:flex;align-items:center;gap:8px;font-family:var(--wx-mono);font-size:12px;color:var(--wx-ink-60);letter-spacing:.04em">
                ${WX.icon(b.icon, { size: 16, color: WX.col(b.accent) })}
                ${escapeHtml(b.label.toUpperCase())}
              </span>
              <div style="flex:1;display:flex;gap:4px">
                ${Array.from({ length: segs }).map((_, k) => `
                  <div style="flex:1;height:14px;background:${k < active ? WX.col(b.accent) : "var(--c-line)"}"></div>
                `).join("")}
              </div>
              <span style="width:96px;text-align:right;font-family:var(--wx-black);font-size:17px" class="wx-tnum">${escapeHtml(String(b.value))}<span style="font-family:var(--wx-mono);font-size:10px;color:var(--wx-ink-60);font-weight:400"> ${escapeHtml(SCALE[active])}</span></span>
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
    <link rel="stylesheet" href="/plugins/weather_pollen_count/client.css">
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
