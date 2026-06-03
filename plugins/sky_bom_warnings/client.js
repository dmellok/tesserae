// sky_bom_warnings — BoM severe-weather warning list.
//
// Ships five visual directions, picked per-cell via the ``variant``
// option:
//   legacy   The original Bauhaus colour-blocked card list (kept as-is
//            for back-compat).
//   r1       Refined — charcoal header + severity-coded list with
//            tinted highlight for hazard. The "primary" direction.
//   g2       Geometric — colour-blocked panels per severity (each row
//            paints in its severity colour with on-fill text).
//   s3       Swiss — hairline header, severity dot per row, small
//            iconography. Whitespace forward.
//   d4       Data — Hazard/Caution/Advisory count tiles at the top, a
//            compact severity-striped list below.
//
// All five render from the same ``ctx.data`` shape that server.py
// returns. Legacy paints from the flat ``warnings`` list; the new
// directions paint from ``data.items`` + ``data.region`` + per-item
// {tag, severity, icon, area, issued, highlight}.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function ago(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function issuedHHMM(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// ===========================================================
// LEGACY — original Bauhaus card list (preserved as-was)
// ===========================================================
const TYPE_ICON = {
  flood_warning: "ph-drop",
  flood_watch: "ph-drop-half",
  severe_thunderstorm_warning: "ph-lightning",
  severe_weather_warning: "ph-wind",
  bushfire: "ph-fire",
  total_fire_ban: "ph-fire",
  fire_weather_warning: "ph-fire",
  frost_warning: "ph-snowflake",
  marine_wind_warning: "ph-waves",
  coastal: "ph-waves",
  tropical_cyclone_advice: "ph-tornado",
  tropical_cyclone_warning: "ph-tornado",
  sheep_graziers_warning: "ph-thermometer-cold",
  damaging_winds: "ph-wind",
  heat: "ph-thermometer-hot",
};
function iconFor(type) {
  return TYPE_ICON[type] || "ph-warning-octagon";
}

function legacyShell(size, body) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/sky_bom_warnings/client.css">
    <div class="root size-${size}">${body}</div>
  `;
}

function renderLegacy(data, size) {
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const stateLabel = data.state === "ALL" ? "Australia" : (data.state || "—");

  const bar = `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="bw-title">BoM warnings · ${escapeHtml(stateLabel)}</span>
      <i class="ph-bold ph-warning-octagon wb-bar-icon"></i>
    </header>
  `;

  if (!warnings.length) {
    return legacyShell(size, `
      ${bar}
      <div class="bw-empty">
        <i class="ph-duotone ph-shield-check" aria-hidden="true"></i>
        <div class="bw-empty-primary">All clear</div>
        <div class="bw-empty-secondary">No active warnings for ${escapeHtml(stateLabel)}.</div>
      </div>
    `);
  }

  const rows = warnings.map((w) => {
    const major = (w.group || "").toLowerCase() === "major";
    const phase = (w.phase || "").toUpperCase();
    const phaseCls = phase === "NEW" ? "is-new" : phase === "CANCELLED" ? "is-cancelled" : "is-update";
    return `
      <article class="bw-card ${major ? 'is-major' : 'is-minor'} ${phaseCls}">
        <div class="bw-card-icon" aria-hidden="true">
          <i class="ph-bold ${iconFor(w.type)}"></i>
        </div>
        <div class="bw-card-body">
          <div class="bw-card-head">
            <span class="bw-card-kind">${escapeHtml(w.short_title)}</span>
            <span class="bw-phase">${escapeHtml(phase)}</span>
          </div>
          <div class="bw-card-title">${escapeHtml(w.title)}</div>
          <div class="bw-card-meta">
            <span><i class="ph-bold ph-flag"></i>${escapeHtml((w.states || []).join(" · ") || w.state || "—")}</span>
            <span><i class="ph-bold ph-clock"></i>Issued ${escapeHtml(ago(w.issued))}</span>
          </div>
        </div>
      </article>
    `;
  }).join("");

  const summary = data.total > data.shown
    ? `<footer class="bw-foot">+${data.total - data.shown} more</footer>`
    : "";

  return legacyShell(size, `
    ${bar}
    <section class="bw-list">${rows}</section>
    ${summary}
  `);
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
    <link rel="stylesheet" href="/plugins/sky_bom_warnings/client.css">
  `;
}

function itemsAvailable(data) {
  return Array.isArray(data.items) ? data.items
    : Array.isArray(data.warnings) ? data.warnings
    : [];
}

function regionLabel(data) {
  return data.region || (data.state === "ALL" ? "Australia" : (data.state || "—"));
}

// The handoff variants assume at least one warning. When the BoM is
// quiet we render an "all clear" body using the same artboard so
// switching variants doesn't dump the user into a different layout.
function renderAllClear(region, body) {
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:24px;gap:10px">
      ${WX.icon("warning", { size: 56, color: WX.col("green") })}
      <div style="font-family:var(--wx-black);font-size:22px;letter-spacing:.04em;text-transform:uppercase">All clear</div>
      <div style="font-family:var(--wx-mono);font-size:12px;color:var(--wx-ink-60);letter-spacing:.04em">${body}</div>
    </div>
  `;
}

// ===========================================================
// R1 — REFINED (BOM-style list with severity stripes)
// ===========================================================
function renderR1(data) {
  const items = itemsAvailable(data);
  const region = regionLabel(data);
  if (!items.length) {
    return renderAllClear(region, `NO ACTIVE WARNINGS · ${escapeHtml(region.toUpperCase())}`);
  }
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({
        title: "BOM WARNINGS · " + region,
        accent: "red",
        right: nowTime(),
      })}
      <div style="flex:1;display:flex;flex-direction:column;min-height:0">
        ${items.map((w, i) => {
          const sev = w.severity || "yellow";
          const icon = w.icon || "warning";
          const bg = w.highlight ? WX.tint(sev) : "transparent";
          return `
            <div style="flex:1;display:flex;gap:14px;align-items:flex-start;padding:12px 18px;min-height:0;background:${bg};border-top:${i ? "1px solid var(--c-line)" : "none"}">
              <div style="display:flex;align-items:flex-start;gap:10px;flex-shrink:0">
                <span style="width:4px;align-self:stretch;background:${WX.col(sev)};flex-shrink:0"></span>
                <div style="display:flex;justify-content:center;padding-top:2px">
                  ${WX.icon(icon, { size: 28, color: WX.col(sev) })}
                </div>
              </div>
              <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;flex-wrap:wrap">
                  <span style="font-family:var(--wx-black);font-size:18px;letter-spacing:.01em">${escapeHtml((w.short_title || w.title || "Warning").toUpperCase())}</span>
                  <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700;letter-spacing:.06em;background:var(--wx-ink);color:var(--wx-paper);padding:2px 7px;flex-shrink:0">${escapeHtml(w.tag || "ALERT")}</span>
                </div>
                <div style="font-weight:600;font-size:13px;line-height:1.3;color:var(--wx-ink);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${escapeHtml(w.area || w.title || "")}</div>
                <div style="display:flex;align-items:center;gap:14px;margin-top:5px;font-family:var(--wx-mono);font-size:11px;font-weight:700;color:var(--wx-ink-60);flex-wrap:wrap">
                  <span style="display:flex;align-items:center;gap:5px">${WX.icon("flag", { size: 12, color: "var(--wx-ink-60)" })}${escapeHtml(((w.states || []).join(" · ")) || w.state || "—")}</span>
                  <span style="display:flex;align-items:center;gap:5px">${WX.icon("clock", { size: 12, color: "var(--wx-ink-60)" })}ISSUED ${escapeHtml(ago(w.issued).toUpperCase() || issuedHHMM(w.issued))}</span>
                </div>
              </div>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-blocked panels per severity)
// ===========================================================
function renderG2(data) {
  const items = itemsAvailable(data);
  const region = regionLabel(data);
  const count = data.count != null ? data.count : items.length;
  if (!items.length) {
    return renderAllClear(region, `0 ACTIVE · ${escapeHtml(region.toUpperCase())}`);
  }
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;flex-direction:column;background:var(--wx-ink);gap:3px">
      <div style="display:flex;justify-content:space-between;align-items:center;background:var(--wx-ink);color:var(--wx-paper);padding:9px 16px;flex-shrink:0">
        <span style="font-family:var(--wx-black);font-size:15px;letter-spacing:.03em;display:flex;align-items:center;gap:9px">
          <span style="width:11px;height:11px;background:${WX.col("red")};flex-shrink:0"></span>
          BOM WARNINGS · ${escapeHtml(region.toUpperCase())}
        </span>
        <span style="font-family:var(--wx-mono);font-size:12px;font-weight:700">${count} ACTIVE</span>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;gap:3px;min-height:0">
        ${items.map((w) => {
          const sev = w.severity || "yellow";
          const fg = WX.inkOn(sev);
          const icon = w.icon || "warning";
          return `
            <div style="flex:1;background:${WX.col(sev)};color:${fg};padding:10px 18px;display:flex;align-items:center;gap:14px;min-height:0">
              ${WX.icon(icon, { size: 28, color: fg })}
              <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                  <span style="font-family:var(--wx-black);font-size:17px">${escapeHtml((w.short_title || w.title || "Warning").toUpperCase())}</span>
                  <span style="font-family:var(--wx-mono);font-size:10px;font-weight:700;border:1.5px solid ${fg};padding:1px 6px;flex-shrink:0">${escapeHtml(w.tag || "ALERT")}</span>
                </div>
                <div style="font-family:var(--wx-mono);font-size:11.5px;opacity:.9;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(w.area || w.title || "")}</div>
              </div>
              <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700;flex-shrink:0">${escapeHtml((ago(w.issued) || issuedHHMM(w.issued) || "").toUpperCase())}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, severity dot per row)
// ===========================================================
function renderS3(data) {
  const items = itemsAvailable(data);
  const region = regionLabel(data);
  const count = data.count != null ? data.count : items.length;
  if (!items.length) {
    return renderAllClear(region, `NO ACTIVE WARNINGS · ${escapeHtml(region.toUpperCase())}`);
  }
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:18px 22px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;flex-shrink:0">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Warnings · ${escapeHtml(region)}</span>
        <span style="font-size:11px;letter-spacing:.16em;color:var(--wx-ink-60)">${count} active · ${nowTime()}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:10px 0;flex-shrink:0"></div>
      <div style="flex:1;display:flex;flex-direction:column;min-height:0">
        ${items.map((w, i) => {
          const sev = w.severity || "yellow";
          const icon = w.icon || "warning";
          return `
            <div style="flex:1;display:flex;align-items:center;gap:14px;padding:6px 0;border-top:${i ? "1px solid var(--c-line)" : "none"};min-height:0">
              <span style="width:9px;height:9px;background:${WX.col(sev)};flex-shrink:0"></span>
              <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:baseline;gap:10px">
                  <span style="font-size:16px;font-weight:600">${escapeHtml(w.short_title || w.title || "Warning")}</span>
                  <span style="font-size:11px;letter-spacing:.16em;color:var(--wx-ink-60);font-weight:700">${escapeHtml(w.tag || "ALERT")}</span>
                </div>
                <div style="font-size:12px;color:var(--wx-ink-60);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(w.area || w.title || "")}</div>
              </div>
              ${WX.icon(icon, { size: 22, color: "var(--wx-ink)", weight: "regular" })}
              <span style="width:70px;text-align:right;font-size:10.5px;letter-spacing:.08em;color:var(--wx-ink-60);flex-shrink:0">${escapeHtml(ago(w.issued) || issuedHHMM(w.issued))}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (severity count tiles + compact striped list)
// ===========================================================
function renderD4(data) {
  const items = itemsAvailable(data);
  const region = regionLabel(data);
  if (!items.length) {
    return renderAllClear(region, `NO ACTIVE WARNINGS · ${escapeHtml(region.toUpperCase())}`);
  }
  const order = ["red", "yellow", "blue"];
  const sevLabel = { red: "Hazard", yellow: "Caution", blue: "Advisory" };
  const counts = order
    .map((s) => ({ s, n: items.filter((w) => (w.severity || "yellow") === s).length }))
    .filter((c) => c.n);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:12px 18px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;flex-shrink:0">
        <span style="font-family:var(--wx-black);font-size:16px;letter-spacing:.03em">WARNINGS · ${escapeHtml(region.toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:10px;flex-shrink:0">
        ${counts.map((c) => `
          <div style="flex:1;background:${WX.col(c.s)};color:${WX.inkOn(c.s)};padding:8px 14px;display:flex;align-items:center;justify-content:space-between">
            <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700;letter-spacing:.04em">${sevLabel[c.s].toUpperCase()}</span>
            <span class="wx-tnum" style="font-family:var(--wx-black);font-size:26px">${c.n}</span>
          </div>
        `).join("")}
      </div>
      <div style="flex:1;display:flex;flex-direction:column;min-height:0">
        ${items.map((w, i) => {
          const sev = w.severity || "yellow";
          const icon = w.icon || "warning";
          return `
            <div style="flex:1;display:flex;align-items:center;gap:12px;padding:0 2px;border-top:${i ? "1px solid var(--c-line)" : "1px solid var(--c-line)"};min-height:0">
              <span style="width:4px;align-self:stretch;background:${WX.col(sev)};flex-shrink:0;margin:6px 0"></span>
              ${WX.icon(icon, { size: 20, color: WX.col(sev) })}
              <div style="flex:1;min-width:0;display:flex;align-items:baseline;gap:9px;overflow:hidden">
                <span style="font-family:var(--wx-black);font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(w.short_title || w.title || "Warning")}</span>
                <span style="font-family:var(--wx-mono);font-size:10.5px;color:var(--wx-ink-60);flex-shrink:0">${escapeHtml(w.tag || "ALERT")}</span>
              </div>
              <span style="font-family:var(--wx-mono);font-size:10.5px;color:var(--wx-ink-60);flex-shrink:0">${escapeHtml(ago(w.issued) || issuedHHMM(w.issued))}</span>
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
    <link rel="stylesheet" href="/plugins/sky_bom_warnings/client.css">
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
