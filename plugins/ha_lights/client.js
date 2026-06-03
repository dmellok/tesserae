// ha_lights — Home Assistant lights overview.
//
// Brand-new widget, no legacy variant. Four directions, picked per-cell
// via the ``variant`` option:
//
//   r1  Refined    Charcoal header + grid of light tiles (icon + name
//                  + ON/brightness % pill or muted OFF pill)
//   g2  Geometric  Colour-blocked tiles — ON = yellow with ink-on
//                  numerals; OFF = ink with paper text
//   s3  Swiss      Hairline header, list rows with name + accent dot +
//                  light-weight tabular percent
//   d4  Data       Top "X / Y ON" bar showing % of lights lit, plus
//                  per-light brightness bars (ON lights only)
//
// Data shape comes from server.py — `lights[]` of
// `{entity_id, name, on, brightness_pct, domain_icon, missing}` plus
// summary `on_count`, `total`, `place`, `time`.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Brightness label — "84%" when on, em-dash otherwise.
function pct(value) {
  if (value == null) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  return Math.round(n) + "%";
}

// Some HA icon hints (e.g. "ceiling-light", "track-light") aren't in
// the Phosphor map; the variants all want the bulb anyway, so we let
// each renderer pass an explicit name and use the hint only as a
// fallback signal.
function bulbIcon({ on, color, size = 22 }) {
  return WX.icon("lightbulb", {
    size,
    color,
    weight: on ? "fill" : "regular",
  });
}

// Style block — note the fill stylesheet is required because R1/G2/D4
// render filled bulbs via ``weight: "fill"``; without it the glyph
// silently falls back to an empty box.
function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

// Empty-state shell shown when no entities are configured, so the cell
// still draws something instead of blanking out.
function emptyShell(data, body) {
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;align-items:center;justify-content:center;padding:18px;text-align:center">
      <div>
        ${WX.icon("lightbulb", { size: 28, color: "var(--wx-ink-60)", weight: "regular" })}
        <div style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;color:var(--wx-ink-60);margin-top:8px">${escapeHtml(body)}</div>
      </div>
    </div>
  `;
}

// ===========================================================
// R1 — REFINED (charcoal header + tile grid)
// ===========================================================
function renderR1(data) {
  const lights = data.lights || [];
  // Decide how many columns based on count, so 4 lights look balanced
  // and 9 lights tile nicely too.
  const cols = lights.length <= 4 ? 2 : lights.length <= 9 ? 3 : 4;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({
        title: `${data.place || ""} · LIGHTS`,
        accent: "yellow",
        right: `${data.on_count || 0}/${data.total || 0} ON · ${data.time || nowTime()}`,
      })}
      <div style="flex:1;display:grid;grid-template-columns:repeat(${cols},1fr);gap:1px;background:var(--c-line);border-top:2px solid var(--wx-ink)">
        ${lights.map((l) => `
          <div style="background:var(--wx-paper);padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0">
            <div style="display:flex;align-items:center;gap:8px;min-width:0">
              ${bulbIcon({ on: l.on, color: l.on ? WX.col("yellow") : "var(--wx-ink-60)", size: 20 })}
              <span style="font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.04em;color:${l.on ? "var(--wx-ink)" : "var(--wx-ink-60)"};text-transform:uppercase;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(l.name || l.entity_id)}</span>
            </div>
            <div>
              ${l.on
                ? `<span style="display:inline-block;background:${WX.col("yellow")};color:${WX.inkOn("yellow")};font-family:var(--wx-mono);font-weight:700;font-size:11.5px;padding:3px 8px;letter-spacing:.04em">ON · <span class="wx-tnum">${escapeHtml(pct(l.brightness_pct))}</span></span>`
                : `<span style="display:inline-block;background:var(--c-line);color:var(--wx-ink-60);font-family:var(--wx-mono);font-weight:700;font-size:11.5px;padding:3px 8px;letter-spacing:.04em">${l.missing ? "MISSING" : "OFF"}</span>`}
            </div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-blocked tiles, ON = yellow)
// ===========================================================
function renderG2(data) {
  const lights = data.lights || [];
  const cols = lights.length <= 4 ? 2 : lights.length <= 9 ? 3 : 4;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px;padding:4px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;padding:6px 10px 8px;color:var(--wx-paper);font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700">
        <span>${escapeHtml((data.place || "").toUpperCase())} · LIGHTS</span>
        <span class="wx-tnum">${data.on_count || 0} / ${data.total || 0} ON</span>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(${cols},1fr);gap:4px">
        ${lights.map((l) => {
          const onBg = WX.col("yellow");
          const onFg = WX.inkOn("yellow");
          const offBg = "var(--wx-ink)";
          const offFg = "var(--wx-paper)";
          const bg = l.on ? onBg : offBg;
          const fg = l.on ? onFg : offFg;
          return `
            <div style="background:${bg};color:${fg};padding:14px 16px;display:flex;flex-direction:column;justify-content:space-between;min-height:90px">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px">
                <span style="font-family:var(--wx-mono);font-size:11.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">${escapeHtml(l.name || l.entity_id)}</span>
                ${bulbIcon({ on: l.on, color: fg, size: 18 })}
              </div>
              <div>
                ${l.on
                  ? `<span class="wx-tnum" style="font-family:var(--wx-black);font-size:36px;line-height:.85;display:block">${escapeHtml(pct(l.brightness_pct))}</span>`
                  : `<span style="font-family:var(--wx-black);font-size:22px;line-height:.9;display:block;opacity:.7">${l.missing ? "MISSING" : "OFF"}</span>`}
              </div>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, light numerals, accent dot)
// ===========================================================
function renderS3(data) {
  const lights = data.lights || [];
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Lights</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(data.place || "")} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-size:11px;letter-spacing:.14em;color:var(--wx-ink-60);text-transform:uppercase">${data.on_count || 0} of ${data.total || 0} lit</span>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;gap:10px;overflow:hidden">
        ${lights.map((l) => `
          <div style="display:flex;align-items:baseline;gap:12px">
            <span style="width:8px;height:8px;border-radius:50%;background:${l.on ? WX.col("yellow") : "var(--c-line)"};flex-shrink:0;align-self:center"></span>
            <span style="font-size:14px;font-weight:400;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${l.on ? "" : "color:var(--wx-ink-60)"}">${escapeHtml(l.name || l.entity_id)}</span>
            <span class="wx-tnum" style="font-size:18px;font-weight:300;${l.on ? "" : "color:var(--wx-ink-60)"}">${l.on ? escapeHtml(pct(l.brightness_pct)) : (l.missing ? "—" : "off")}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (house-lit bar + per-light brightness bars)
// ===========================================================
function renderD4(data) {
  const lights = data.lights || [];
  const onLights = lights.filter((l) => l.on);
  const total = data.total || 0;
  const onCount = data.on_count || 0;
  const housePct = total > 0 ? Math.round((onCount / total) * 100) : 0;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 20px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span style="font-family:var(--wx-black);font-size:16px;letter-spacing:.03em">LIGHTS · ${escapeHtml((data.place || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px">
        <span class="wx-tnum" style="font-family:var(--wx-black);font-size:36px;line-height:.85">${onCount} / ${total}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.06em;color:var(--wx-ink-60)">ON · ${housePct}% OF HOUSE</span>
      </div>
      <div style="margin-bottom:14px">
        ${WX.barChart({ value: housePct, max: 100, color: WX.col("yellow"), height: 8 })}
      </div>
      <div style="font-family:var(--wx-mono);font-size:10.5px;letter-spacing:.08em;color:var(--wx-ink-60);margin-bottom:6px">BRIGHTNESS · LIT ONLY</div>
      <div style="flex:1;display:flex;flex-direction:column;gap:8px;overflow:hidden">
        ${onLights.length === 0
          ? `<div style="font-family:var(--wx-mono);font-size:12px;color:var(--wx-ink-60);padding:8px 0">No lights on.</div>`
          : onLights.map((l) => `
            <div style="display:flex;flex-direction:column;gap:3px">
              <div style="display:flex;justify-content:space-between;align-items:baseline">
                <span style="display:flex;align-items:center;gap:6px;font-family:var(--wx-mono);font-size:11.5px;min-width:0">
                  ${bulbIcon({ on: true, color: WX.col("yellow"), size: 14 })}
                  <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(l.name || l.entity_id)}</span>
                </span>
                <span class="wx-tnum" style="font-family:var(--wx-mono);font-weight:700;font-size:11.5px">${escapeHtml(pct(l.brightness_pct))}</span>
              </div>
              ${WX.barChart({ value: l.brightness_pct ?? 0, max: 100, color: WX.col("yellow"), height: 6 })}
            </div>
          `).join("")}
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
  if (data.empty || !data.lights || data.lights.length === 0) {
    shadow.innerHTML = emptyShell(data, "No light entities configured");
    return;
  }
  const variant = ctx.cell.options.variant || "r1";
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data);
}
