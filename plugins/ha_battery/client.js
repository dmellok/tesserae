// ha_battery — Home Assistant battery levels across every device.
//
// Server.py auto-discovers entities with device_class=battery, sorts
// low-first, and hands us:
//
//   data.label, data.time
//   data.items[]     {entity_id, name, level, critical, low}
//   data.summary     {count, shown, low, critical, avg, min, max, histogram[10]}
//   data.low_threshold, data.critical_threshold
//
// Four directions pickable per-cell via `variant`:
//
//   r1  Refined    Charcoal header + sorted list of horizontal bars.
//                  Each row: icon + name + bar + percentage.
//   g2  Geometric  Big colour-blocked tiles per battery, Archivo Black
//                  numerals. Grid lays them out 2/3 columns.
//   s3  Swiss      Hairline header, table-like rows with name + tabular
//                  percentage + small accent dot.
//   d4  Data       10-bucket histogram of every battery + the lowest 3
//                  highlighted + overall avg.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Phosphor icon for a battery at this level. Matches HA's own iconography
// (empty → low → medium → full) so the user's mental model of "how full"
// stays consistent with what they see in Lovelace.
function batteryIcon(level) {
  if (level < 25) return "battery-empty";
  if (level < 50) return "battery-low";
  if (level < 75) return "battery-medium";
  return "battery-full";
}

// Status → accent token. Critical takes priority over low.
function statusAccent(item) {
  if (item.critical) return "red";
  if (item.low) return "yellow";
  return "green";
}

function statusLabel(item) {
  if (item.critical) return "CRITICAL";
  if (item.low) return "LOW";
  return "OK";
}

function fmtPct(level) {
  if (level == null || Number.isNaN(Number(level))) return "—";
  return Math.round(Number(level)) + "%";
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

// Empty-state copy — when HA has zero battery-class entities (or the
// install isn't configured yet), the widget still needs to occupy its
// cell sensibly.
function emptyState(label, accent = "muted") {
  return `
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:var(--wx-ink-60);font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em">
      ${WX.icon("battery-empty", { size: 32, color: WX.col(accent) })}
      <span>NO BATTERIES FOUND</span>
      <span style="font-size:10.5px">${escapeHtml(label || "")}</span>
    </div>
  `;
}

// ===========================================================
// R1 — REFINED (header + sorted list of horizontal bars)
// ===========================================================
function renderR1(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  const headerRight = summary.count != null
    ? `${summary.shown ?? items.length}/${summary.count} · ${escapeHtml(data.time || nowTime())}`
    : escapeHtml(data.time || nowTime());
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: data.label || "BATTERIES", accent: "green", right: headerRight })}
      <div style="flex:1;display:flex;flex-direction:column;border-top:2px solid var(--wx-ink);overflow:hidden">
        ${items.length === 0 ? emptyState(data.label) : items.map((it, i) => {
          const accent = statusAccent(it);
          return `
            <div style="display:flex;align-items:center;gap:10px;padding:7px 14px;${i < items.length - 1 ? "border-bottom:1px solid rgba(27,26,22,.10);" : ""}">
              ${WX.icon(batteryIcon(it.level), { size: 18, color: WX.col(accent) })}
              <span style="flex:0 0 32%;min-width:0;font-family:var(--wx-grotesk);font-size:12.5px;font-weight:600;color:var(--wx-ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(it.name)}</span>
              <div style="flex:1;min-width:24px">
                ${WX.barChart({ value: it.level, max: 100, color: WX.col(accent), height: 8 })}
              </div>
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:14px;min-width:44px;text-align:right">${escapeHtml(fmtPct(it.level))}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-blocked tiles, Archivo Black numerals)
// ===========================================================
function renderG2(data) {
  const items = data.items || [];
  // Pick a column count that keeps tiles square-ish: 1-4 items → 2 cols,
  // 5-9 → 3, 10+ → 4. Bauhaus-grid friendly.
  const cols = items.length <= 4 ? 2 : items.length <= 9 ? 3 : 4;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:3px">
      <div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 14px;display:flex;justify-content:space-between;align-items:baseline;font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700">
        <span>${escapeHtml((data.label || "BATTERIES").toUpperCase())}</span>
        <span style="opacity:.85;font-weight:400">${escapeHtml(data.time || nowTime())}</span>
      </div>
      ${items.length === 0 ? `<div style="flex:1;background:var(--wx-paper);display:flex">${emptyState(data.label)}</div>` : `
        <div style="flex:1;display:grid;grid-template-columns:repeat(${cols},1fr);grid-auto-rows:1fr;gap:3px">
          ${items.map((it) => {
            const accent = statusAccent(it);
            return `
              <div style="background:${WX.col(accent)};color:${WX.inkOn(accent)};padding:10px 12px;display:flex;flex-direction:column;justify-content:space-between;min-width:0">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px">
                  <span style="font-family:var(--wx-mono);font-size:10.5px;font-weight:700;letter-spacing:.04em;line-height:1.1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0">${escapeHtml(it.name)}</span>
                  ${WX.icon(batteryIcon(it.level), { size: 16, color: WX.inkOn(accent) })}
                </div>
                <div>
                  <span class="wx-tnum" style="font-family:var(--wx-black);font-size:36px;line-height:.85;display:block">${escapeHtml(fmtPct(it.level))}</span>
                  <span style="font-family:var(--wx-mono);font-size:9.5px;letter-spacing:.08em;opacity:.85">${escapeHtml(statusLabel(it))}</span>
                </div>
              </div>
            `;
          }).join("")}
        </div>
      `}
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, table-like rows, tiny accent dots)
// ===========================================================
function renderS3(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:18px 24px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">${escapeHtml(data.label || "Batteries")}</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">
          ${summary.count != null ? `${summary.shown ?? items.length}/${summary.count} · ` : ""}${escapeHtml(data.time || nowTime())}
        </span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:10px 0 4px"></div>
      ${items.length === 0 ? emptyState(data.label) : `
        <div style="flex:1;display:flex;flex-direction:column">
          ${items.map((it, i) => {
            const accent = statusAccent(it);
            return `
              <div style="display:flex;align-items:baseline;gap:10px;padding:5px 0;${i < items.length - 1 ? "border-bottom:1px solid rgba(27,26,22,.10);" : ""}">
                <span style="width:7px;height:7px;background:${WX.col(accent)};display:inline-block;flex-shrink:0;align-self:center"></span>
                <span style="flex:1;min-width:0;font-size:12.5px;font-weight:400;color:var(--wx-ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(it.name)}</span>
                <span style="font-size:10px;letter-spacing:.12em;color:var(--wx-ink-60);text-transform:uppercase">${escapeHtml(statusLabel(it))}</span>
                <span class="wx-tnum" style="font-size:18px;font-weight:300;min-width:48px;text-align:right">${escapeHtml(fmtPct(it.level))}</span>
              </div>
            `;
          }).join("")}
        </div>
      `}
    </div>
  `;
}

// ===========================================================
// D4 — DATA (10-bucket histogram + lowest-3 highlight + avg)
// ===========================================================
function renderD4(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  const histogram = Array.isArray(summary.histogram) && summary.histogram.length === 10
    ? summary.histogram
    : [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  const histMax = Math.max(1, ...histogram);
  // Items are already sorted ASC server-side, so the first three are the
  // lowest. We highlight those by name below the chart.
  const lowest3 = items.slice(0, 3);
  // Colour each histogram bar by the bucket's representative level:
  // 0-10 critical, 10-20 low (by default thresholds), 20+ ok. We
  // approximate using the bucket midpoint rather than re-applying the
  // user's threshold — keeps the chart legible even on installs with
  // non-default thresholds.
  function bucketAccent(idx) {
    const mid = idx * 10 + 5;
    if (mid < (data.critical_threshold ?? 10)) return "red";
    if (mid < (data.low_threshold ?? 20)) return "yellow";
    return "green";
  }
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 18px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span style="font-family:var(--wx-black);font-size:16px;letter-spacing:.03em">${escapeHtml((data.label || "BATTERIES").toUpperCase())} · ${summary.count ?? 0}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="display:flex;gap:18px;flex:1;min-height:0">
        <div style="flex:1;display:flex;flex-direction:column;min-width:0">
          <div style="display:flex;align-items:baseline;gap:10px">
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:42px;line-height:.85">${escapeHtml(summary.avg != null ? Math.round(summary.avg) + "%" : "—")}</span>
            <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">AVERAGE · ${summary.count ?? 0} DEVICES${summary.critical ? " · " + summary.critical + " CRIT" : ""}${summary.low ? " · " + summary.low + " LOW" : ""}</span>
          </div>
          <div style="flex:1;display:flex;align-items:flex-end;gap:3px;margin-top:10px;min-height:48px">
            ${histogram.map((count, i) => {
              const h = (count / histMax) * 100;
              return `
                <div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:stretch;gap:3px;min-width:0">
                  <div style="height:${h.toFixed(1)}%;background:${WX.col(bucketAccent(i))};min-height:${count > 0 ? "3px" : "0"}"></div>
                  <span style="font-family:var(--wx-mono);font-size:9px;color:var(--wx-ink-60);text-align:center">${i * 10}</span>
                </div>
              `;
            }).join("")}
          </div>
        </div>
        <div style="width:38%;max-width:200px;flex-shrink:0;border-left:1px solid rgba(27,26,22,.18);padding-left:14px;display:flex;flex-direction:column;gap:6px">
          <span style="font-family:var(--wx-mono);font-size:10.5px;letter-spacing:.08em;color:var(--wx-ink-60)">LOWEST</span>
          ${lowest3.length === 0 ? `<span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">—</span>` : lowest3.map((it) => {
            const accent = statusAccent(it);
            return `
              <div style="display:flex;align-items:center;gap:8px">
                ${WX.icon(batteryIcon(it.level), { size: 16, color: WX.col(accent) })}
                <span style="flex:1;min-width:0;font-family:var(--wx-grotesk);font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(it.name)}</span>
                <span class="wx-tnum" style="font-family:var(--wx-black);font-size:14px">${escapeHtml(fmtPct(it.level))}</span>
              </div>
            `;
          }).join("")}
        </div>
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
    <div class="root error" style="padding:12px;font-family:system-ui,sans-serif;color:#c44a3a;display:flex;align-items:center;gap:8px;height:100%;box-sizing:border-box">
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
