// ha_camera — Home Assistant camera snapshot.
//
// Brand-new widget, no legacy variant. Four directions, picked per-cell
// via the ``variant`` option:
//
//   r1  Refined    Charcoal header + image as the dominant content + a
//                  small meta strip below (entity name + last-updated).
//                  Multi-entity mode lays out as a 2×2 grid.
//   g2  Geometric  Full-bleed image with an ink panel overlay in the
//                  bottom corner (name + timestamp). Single entity only.
//   s3  Swiss      Hairline header, thin-bordered image, small caption
//                  row underneath.
//   d4  Data       Image + chip strip below carrying last-changed,
//                  state, motion-detected.
//
// Data shape comes from server.py — `label` and `items[]` where each
// item is `{entity_id, name, image_url, last_updated, last_changed,
// state, motion}`. Missing/offline cameras render a camera-slash
// placeholder instead of a broken <img>.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

// ----- formatting helpers -----

// Localised HH:MM from an ISO timestamp — HA emits UTC, the cell wants
// wall-clock. Returns "" for falsy/unparseable inputs so the variants
// can elide the row entirely.
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Compact "5m ago" style relative time. Used in the data variant's
// chip strip where space is tight.
function fmtRelative(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const sec = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
  `;
}

// Rendered when an entity has no entity_picture (offline, no auth,
// unsupported integration). Centred camera-slash glyph on a paper
// background so the cell still composes cleanly.
function imagePlaceholder() {
  return `
    <div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:var(--wx-paper-2);color:var(--wx-ink-60)">
      ${WX.icon("camera-slash", { size: 48 })}
    </div>
  `;
}

// Either an <img> with cover-fit, or the placeholder. Wrapped in a
// fixed-size box so the variants can flex it freely.
function imageOrPlaceholder(url, alt) {
  if (!url) return imagePlaceholder();
  return `
    <img src="${escapeHtml(url)}" alt="${escapeHtml(alt || "")}"
         style="object-fit:cover;width:100%;height:100%;display:block"
         onerror="this.replaceWith(Object.assign(document.createElement('div'),{innerHTML:''}))" />
  `;
}

// ===========================================================
// R1 — REFINED
// Charcoal header + image dominant + meta strip below. Multi-entity
// items grid as 2×2 with each frame's name below it.
// ===========================================================
function renderR1(data) {
  const items = data.items || [];
  const multi = items.length > 1;
  const headerRight = items[0] ? fmtTime(items[0].last_updated) || nowTime() : nowTime();
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: `${data.label || "Camera"}`, accent: "blue", right: headerRight })}
      ${multi ? `
        <div style="flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:6px;padding:6px;background:var(--wx-ink);min-height:0">
          ${items.slice(0, 4).map((it) => `
            <div style="background:var(--wx-paper);display:flex;flex-direction:column;min-height:0">
              <div style="flex:1;min-height:0;overflow:hidden">
                ${imageOrPlaceholder(it.image_url, it.name)}
              </div>
              <div style="padding:4px 8px;font-family:var(--wx-mono);font-size:10.5px;letter-spacing:.04em;color:var(--wx-ink-60);display:flex;justify-content:space-between;gap:8px;border-top:1px solid var(--c-line)">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml((it.name || "").toUpperCase())}</span>
                <span>${escapeHtml(fmtTime(it.last_updated))}</span>
              </div>
            </div>
          `).join("")}
        </div>
      ` : `
        <div style="flex:1;min-height:0;overflow:hidden;border-top:2px solid var(--wx-ink)">
          ${imageOrPlaceholder((items[0] || {}).image_url, (items[0] || {}).name)}
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 16px;border-top:2px solid var(--wx-ink);height:42px;flex-shrink:0;font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.06em;color:var(--wx-ink-60)">
          <span style="display:flex;align-items:center;gap:8px">
            ${WX.icon("video-camera", { size: 16, color: WX.col("blue") })}
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(((items[0] || {}).name || "").toUpperCase())}</span>
          </span>
          <span>${escapeHtml(fmtTime((items[0] || {}).last_updated))}</span>
        </div>
      `}
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC
// Full-bleed image with an ink panel overlay carrying name +
// timestamp. Single entity only — the dramatic full-bleed effect
// doesn't survive sub-dividing.
// ===========================================================
function renderG2(data) {
  const it = (data.items || [])[0] || {};
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);position:relative;background:var(--wx-ink);overflow:hidden">
      <div style="position:absolute;inset:0">
        ${imageOrPlaceholder(it.image_url, it.name)}
      </div>
      <div style="position:absolute;left:0;bottom:0;background:var(--wx-ink);color:var(--wx-paper);padding:12px 18px;display:flex;flex-direction:column;gap:2px;max-width:78%">
        <span style="font-family:var(--wx-mono);font-size:11px;font-weight:700;letter-spacing:.1em;opacity:.7">${escapeHtml((data.label || "CAMERA").toUpperCase())}</span>
        <span style="font-family:var(--wx-black);font-size:20px;letter-spacing:.02em;line-height:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml((it.name || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.06em;opacity:.75;margin-top:2px">${escapeHtml(fmtTime(it.last_updated) || nowTime())}</span>
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS
// Hairline header, image with thin border, small caption row.
// Single entity only — the Swiss aesthetic wants a single hero shot.
// ===========================================================
function renderS3(data) {
  const it = (data.items || [])[0] || {};
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:18px 22px;display:flex;flex-direction:column;gap:10px">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">${escapeHtml(data.label || "Camera")}</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml((it.name || "").toUpperCase())}</span>
      </div>
      <div style="height:1px;background:var(--wx-ink)"></div>
      <div style="flex:1;min-height:0;border:1px solid var(--c-line);overflow:hidden">
        ${imageOrPlaceholder(it.image_url, it.name)}
      </div>
      <div style="display:flex;justify-content:space-between;font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--wx-ink-60)">
        <span>${escapeHtml(it.state || "—")}</span>
        <span>${escapeHtml(fmtTime(it.last_updated) || nowTime())}</span>
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA
// Image + a chip strip below with metadata: last-changed, state,
// motion-detected if HA exposes a motion attribute.
// ===========================================================
function renderD4(data) {
  const it = (data.items || [])[0] || {};
  const chips = [];
  chips.push({
    icon: "clock",
    label: "LAST",
    value: fmtRelative(it.last_changed || it.last_updated) || "—",
    accent: "ink",
  });
  chips.push({
    icon: "circle",
    label: "STATE",
    value: (it.state || "—").toUpperCase(),
    accent: "blue",
  });
  if (it.motion === true) {
    chips.push({ icon: "person-simple-run", label: "MOTION", value: "DETECTED", accent: "red" });
  } else if (it.motion === false) {
    chips.push({ icon: "person-simple-run", label: "MOTION", value: "CLEAR", accent: "green" });
  }
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:12px 16px;display:flex;flex-direction:column;gap:10px">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-family:var(--wx-black);font-size:16px;letter-spacing:.03em">${escapeHtml((data.label || "CAMERA").toUpperCase())} · ${escapeHtml((it.name || "").toUpperCase())}</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(fmtTime(it.last_updated) || nowTime())}</span>
      </div>
      <div style="flex:1;min-height:0;overflow:hidden;border:1px solid var(--c-line)">
        ${imageOrPlaceholder(it.image_url, it.name)}
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        ${chips.map((c) => `
          <span style="display:inline-flex;align-items:center;gap:6px;background:${WX.tint(c.accent)};color:var(--wx-ink);padding:5px 10px;font-family:var(--wx-mono);font-size:11.5px">
            ${WX.icon(c.icon, { size: 14, color: WX.col(c.accent) })}
            <b>${escapeHtml(c.label)}</b>
            <span class="wx-tnum">${escapeHtml(c.value)}</span>
          </span>
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
  const variant = ctx.cell.options.variant || "r1";
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data);
}
