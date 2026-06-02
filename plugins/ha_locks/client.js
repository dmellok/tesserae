// ha_locks — Home Assistant door / window / lock security overview.
//
// Brand-new widget, no legacy variant. Four directions, picked per-cell
// via the ``variant`` option:
//
//   r1  Refined    Charcoal header "X SECURED · Y UNLOCKED" + list of
//                  rows with name + icon + state, red accent on any
//                  unsecured row.
//   g2  Geometric  Big colour-blocked tiles — green for secured, red
//                  for unsecured, Archivo Black LOCKED/UNLOCKED/OPEN/
//                  CLOSED labels.
//   s3  Swiss      Hairline list, name + accent dot (green/red) + state
//                  in light tabular.
//   d4  Data       Top alert strip with UNSECURED count (red if any),
//                  then list rows with last-changed times. If
//                  everything's secured, a single "ALL SECURED" panel.
//
// Data shape comes from server.py — `entries[]` of
// `{entity_id, name, kind, state, secured, last_changed}` plus summary
// `{secured, unsecured, total}`, `place`, `time`.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Phosphor glyph for each (kind, secured) pair. Locks get the actual
// lock glyphs; doors flip between the door/door-open pair; windows use
// the filled/dashed rectangle pair; garages share "garage" both ways
// since Phosphor doesn't ship a paired open variant.
function iconFor(entry) {
  const k = entry.kind;
  const secured = !!entry.secured;
  if (k === "lock") return secured ? "lock" : "lock-open";
  if (k === "door") return secured ? "door" : "door-open";
  if (k === "window") return secured ? "rectangle" : "rectangle-dashed";
  if (k === "garage") return "garage";
  return secured ? "lock" : "lock-open";
}

// Human-readable state label, upper-cased for the variants that want
// chunky monospace badges.
function stateLabel(entry) {
  return String(entry.state || "").toUpperCase();
}

// Format the HA last_changed ISO string as a friendly "HH:MM" or a
// short relative form for older changes. Returns "" when we couldn't
// parse it so call sites can skip rendering.
function fmtLastChanged(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const now = Date.now();
  const diff = Math.max(0, now - t);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  // Fall back to wall-clock time once we're past a week.
  try {
    return new Date(t).toLocaleDateString([], { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

// Empty/error shell — used when HA has nothing matching our filters so
// the cell never blanks out.
function emptyShell(body) {
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;align-items:center;justify-content:center;padding:18px;text-align:center">
      <div>
        ${WX.icon("lock", { size: 28, color: "var(--wx-ink-60)", weight: "regular" })}
        <div style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;color:var(--wx-ink-60);margin-top:8px">${escapeHtml(body)}</div>
      </div>
    </div>
  `;
}

// ===========================================================
// R1 — REFINED (charcoal header + list with name/icon/state)
// ===========================================================
function renderR1(data) {
  const entries = data.entries || [];
  const summary = data.summary || { secured: 0, unsecured: 0, total: 0 };
  const accent = summary.unsecured > 0 ? "red" : "green";
  const headerRight = `${summary.secured} SECURED · ${summary.unsecured} UNLOCKED`;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({
        title: `${data.place || ""} · SECURITY`,
        accent,
        right: `${headerRight} · ${data.time || nowTime()}`,
      })}
      <div style="flex:1;display:flex;flex-direction:column;border-top:2px solid var(--wx-ink);overflow:hidden">
        ${entries.map((e, i) => {
          const unsecured = !e.secured;
          const accentCol = unsecured ? WX.col("red") : "var(--wx-ink-60)";
          const rowBg = unsecured ? WX.tint("red") : "var(--wx-paper)";
          const stateBg = unsecured ? WX.col("red") : "rgba(27,26,22,.08)";
          const stateFg = unsecured ? WX.inkOn("red") : "var(--wx-ink-60)";
          return `
            <div style="display:flex;align-items:center;gap:10px;padding:9px 16px;background:${rowBg};${i > 0 ? "border-top:1px solid rgba(27,26,22,.12);" : ""}min-width:0">
              ${WX.icon(iconFor(e), { size: 20, color: accentCol, weight: unsecured ? "fill" : "bold" })}
              <span style="font-family:var(--wx-mono);font-size:12px;letter-spacing:.04em;text-transform:uppercase;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${unsecured ? "" : "color:var(--wx-ink)"}">${escapeHtml(e.name || e.entity_id)}</span>
              <span style="display:inline-block;background:${stateBg};color:${stateFg};font-family:var(--wx-mono);font-weight:700;font-size:11px;padding:3px 8px;letter-spacing:.06em">${escapeHtml(stateLabel(e))}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-blocked tiles, green = secured / red = unsecured)
// ===========================================================
function renderG2(data) {
  const entries = data.entries || [];
  // Same column ramp as ha_lights so a grid of 4 / 9 / 16 looks balanced.
  const cols = entries.length <= 4 ? 2 : entries.length <= 9 ? 3 : 4;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:4px;padding:4px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;padding:6px 10px 8px;color:var(--wx-paper);font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700">
        <span>${escapeHtml((data.place || "").toUpperCase())} · SECURITY</span>
        <span class="wx-tnum">${data.summary?.secured || 0} / ${data.summary?.total || 0} SECURED</span>
      </div>
      <div style="flex:1;display:grid;grid-template-columns:repeat(${cols},1fr);gap:4px">
        ${entries.map((e) => {
          const accent = e.secured ? "green" : "red";
          const bg = WX.col(accent);
          const fg = WX.inkOn(accent);
          return `
            <div style="background:${bg};color:${fg};padding:14px 16px;display:flex;flex-direction:column;justify-content:space-between;min-height:90px">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:6px">
                <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;opacity:.9">${escapeHtml(e.name || e.entity_id)}</span>
                ${WX.icon(iconFor(e), { size: 18, color: fg, weight: e.secured ? "bold" : "fill" })}
              </div>
              <div>
                <span style="font-family:var(--wx-black);font-size:24px;line-height:.85;display:block">${escapeHtml(stateLabel(e))}</span>
              </div>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, list + accent dot + light tabular state)
// ===========================================================
function renderS3(data) {
  const entries = data.entries || [];
  const summary = data.summary || { secured: 0, unsecured: 0, total: 0 };
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Security</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml(data.place || "")} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-size:11px;letter-spacing:.14em;color:var(--wx-ink-60);text-transform:uppercase">${summary.secured} of ${summary.total} secured</span>
        ${summary.unsecured > 0 ? `<span style="font-size:11px;letter-spacing:.14em;color:${WX.col("red")};text-transform:uppercase;font-weight:700">${summary.unsecured} unsecured</span>` : ""}
      </div>
      <div style="flex:1;display:flex;flex-direction:column;gap:10px;overflow:hidden">
        ${entries.map((e) => {
          const dotCol = e.secured ? WX.col("green") : WX.col("red");
          const nameCol = e.secured ? "" : `color:${WX.col("red")};font-weight:700`;
          const stateCol = e.secured ? "color:var(--wx-ink-60)" : `color:${WX.col("red")};font-weight:700`;
          return `
            <div style="display:flex;align-items:baseline;gap:12px">
              <span style="width:8px;height:8px;border-radius:50%;background:${dotCol};flex-shrink:0;align-self:center"></span>
              <span style="font-size:14px;font-weight:400;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${nameCol}">${escapeHtml(e.name || e.entity_id)}</span>
              <span class="wx-tnum" style="font-size:14px;font-weight:300;${stateCol}">${escapeHtml(e.state || "")}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (alert strip + list w/ last-changed times, or ALL SECURED panel)
// ===========================================================
function renderD4(data) {
  const entries = data.entries || [];
  const summary = data.summary || { secured: 0, unsecured: 0, total: 0 };
  // Short-circuit: if everything's secured, show the big "ALL SECURED"
  // panel so the cell reads as a single calm green statement.
  if (summary.unsecured === 0 && summary.total > 0) {
    return `
      ${styleBlock()}
      <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:${WX.col("green")};color:${WX.inkOn("green")}">
        <div style="display:flex;justify-content:space-between;align-items:baseline;padding:12px 20px 0;font-family:var(--wx-mono);font-size:11px;letter-spacing:.06em;font-weight:700;opacity:.9">
          <span>${escapeHtml((data.place || "").toUpperCase())}</span>
          <span>${escapeHtml(data.time || nowTime())}</span>
        </div>
        <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;text-align:center;padding:18px">
          ${WX.icon("check-circle", { size: 56, color: WX.inkOn("green"), weight: "fill" })}
          <span style="font-family:var(--wx-black);font-size:34px;line-height:.9;letter-spacing:.03em">ALL SECURED</span>
          <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.08em;opacity:.85">${summary.total} ENTR${summary.total === 1 ? "Y" : "IES"} · ${summary.secured} LOCKED / CLOSED</span>
        </div>
      </div>
    `;
  }

  const unsecured = entries.filter((e) => !e.secured);
  const secured = entries.filter((e) => e.secured);

  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${summary.unsecured > 0
        ? `<div style="background:${WX.col("red")};color:${WX.inkOn("red")};padding:10px 18px;display:flex;align-items:center;gap:10px;font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em;font-weight:700">
            ${WX.icon("warning", { size: 18, color: WX.inkOn("red"), weight: "fill" })}
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:18px;line-height:.9">${summary.unsecured}</span>
            <span>UNSECURED</span>
            <span style="margin-left:auto;opacity:.85">${escapeHtml(data.time || nowTime())}</span>
          </div>`
        : `<div style="background:var(--wx-ink);color:var(--wx-paper);padding:8px 18px;display:flex;align-items:center;gap:10px;font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.06em;font-weight:700">
            <span>${escapeHtml((data.place || "").toUpperCase())} · SECURITY</span>
            <span style="margin-left:auto;opacity:.85">${escapeHtml(data.time || nowTime())}</span>
          </div>`}
      <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
        ${unsecured.length > 0 ? `
          <div style="font-family:var(--wx-mono);font-size:10.5px;letter-spacing:.08em;color:var(--wx-ink-60);padding:8px 18px 4px">NEEDS ATTENTION</div>
          ${unsecured.map((e) => `
            <div style="display:flex;align-items:center;gap:10px;padding:6px 18px;background:${WX.tint("red")};border-top:1px solid rgba(27,26,22,.08);min-width:0">
              ${WX.icon(iconFor(e), { size: 18, color: WX.col("red"), weight: "fill" })}
              <span style="font-family:var(--wx-mono);font-size:11.5px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700">${escapeHtml(e.name || e.entity_id)}</span>
              <span class="wx-tnum" style="font-family:var(--wx-mono);font-weight:700;font-size:11px;color:${WX.col("red")};letter-spacing:.06em">${escapeHtml(stateLabel(e))}</span>
              <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10.5px;color:var(--wx-ink-60);min-width:54px;text-align:right">${escapeHtml(fmtLastChanged(e.last_changed))}</span>
            </div>
          `).join("")}
        ` : ""}
        ${secured.length > 0 ? `
          <div style="font-family:var(--wx-mono);font-size:10.5px;letter-spacing:.08em;color:var(--wx-ink-60);padding:8px 18px 4px">SECURED · ${secured.length}</div>
          ${secured.map((e) => `
            <div style="display:flex;align-items:center;gap:10px;padding:5px 18px;border-top:1px solid rgba(27,26,22,.08);min-width:0">
              ${WX.icon(iconFor(e), { size: 16, color: WX.col("green"), weight: "bold" })}
              <span style="font-family:var(--wx-mono);font-size:11px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--wx-ink-60)">${escapeHtml(e.name || e.entity_id)}</span>
              <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10.5px;color:var(--wx-ink-60);letter-spacing:.06em">${escapeHtml(stateLabel(e))}</span>
              <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:10.5px;color:var(--wx-ink-60);min-width:54px;text-align:right">${escapeHtml(fmtLastChanged(e.last_changed))}</span>
            </div>
          `).join("")}
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
  const entries = data.entries || [];
  if (entries.length === 0) {
    shadow.innerHTML = emptyShell("No locks or door sensors found");
    return;
  }
  const variant = ctx.cell.options.variant || "r1";
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data);
}
