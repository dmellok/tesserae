// ha_zones — Home Assistant person.* presence map.
//
// Brand-new widget, no legacy variant. Server.py auto-discovers every
// person entity, normalises their zone state, resolves entity_picture
// to a full URL, and hands us:
//
//   data.label, data.time
//   data.items[]    {entity_id, name, state, entity_picture, last_changed, history?}
//   data.summary    {home, away, total}
//
// Four directions pickable per-cell via ``variant``:
//
//   r1  Refined    Charcoal header "HOME · X / Y AT HOME" + grid of
//                  circular avatars with name + state pill beneath.
//   g2  Geometric  Colour-blocked tile per person; green = home,
//                  ink = away, blue = custom zone. Avatar overlay + big
//                  Archivo Black state label.
//   s3  Swiss      Hairline header, list view with name + tabular state
//                  + small accent dot.
//   d4  Data       Top home/away ratio bar + per-person rows with avatar,
//                  state, last-changed time, and an optional 24h history
//                  strip per person.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// ----- presence semantics -----
// HA's person domain emits the canonical "home" / "not_home" strings
// for built-in zones; anything else is the friendly name of a custom
// zone the person is currently inside (e.g. "Work").
function isHome(state) { return String(state || "").toLowerCase() === "home"; }
function isAway(state) { return String(state || "").toLowerCase() === "not_home"; }
function isZone(state) { return !isHome(state) && !isAway(state); }

// Accent per state. Home = green (welcome), away = muted (off), custom
// zone = blue (somewhere known). Falls back to muted for "unknown" /
// blank states.
function stateAccent(state) {
  if (isHome(state)) return "green";
  if (isZone(state)) return "blue";
  return "muted";
}

// Phosphor glyph per state. "house" = home, "house-line" = away,
// "map-pin" = a custom zone (somewhere else known). These match the
// "use these icons" guidance from the design brief.
function stateIcon(state) {
  if (isHome(state)) return "house";
  if (isZone(state)) return "map-pin";
  return "house-line";
}

// Human pill label. We uppercase home/away for the pill but pass a
// custom zone name through unchanged — those are user-defined strings
// like "Work" or "Gym" and look weird shouted.
function stateLabel(state) {
  if (isHome(state)) return "HOME";
  if (isAway(state)) return "AWAY";
  return String(state || "").toUpperCase();
}

// "x minutes ago" relative-time string for the data variant. Falls
// back to "—" if last_changed is missing or unparseable.
function relTime(iso) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const delta = Math.max(0, Date.now() - t);
  const mins = Math.floor(delta / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return mins + "m ago";
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + "h ago";
  const days = Math.floor(hrs / 24);
  return days + "d ago";
}

// ----- avatar rendering -----
// HA's entity_picture might be missing (no avatar set), in which case
// we drop in a Phosphor person silhouette. Otherwise it's a regular
// <img> sized to the requested diameter, circular, cover-cropped so
// non-square avatars still tile cleanly.
function avatar(item, size = 40) {
  if (item.entity_picture) {
    return `<img src="${escapeHtml(item.entity_picture)}" alt="" style="width:${size}px;height:${size}px;object-fit:cover;border-radius:50%;display:block;flex-shrink:0">`;
  }
  // Fallback — circle with a person silhouette inside.
  return `
    <span style="width:${size}px;height:${size}px;border-radius:50%;background:var(--wx-paper-3);display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">
      ${WX.icon("user", { size: Math.round(size * 0.55), color: "var(--wx-ink-60)" })}
    </span>
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

// Empty-state copy — when HA has zero person entities (or the install
// isn't configured yet), the widget still needs to occupy its cell
// sensibly.
function emptyState(label) {
  return `
    <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:var(--wx-ink-60);font-family:var(--wx-mono);font-size:12px;letter-spacing:.06em">
      ${WX.icon("house-line", { size: 32, color: WX.col("muted") })}
      <span>NO PEOPLE FOUND</span>
      <span style="font-size:10.5px">${escapeHtml(label || "")}</span>
    </div>
  `;
}

// ===========================================================
// R1 — REFINED (hero 2-up HOME/AWAY + avatar grid)
// ===========================================================
function renderR1(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  const home = summary.home ?? 0;
  const total = summary.total ?? items.length;
  const away = Math.max(0, total - home);
  const headerRight = `${home} / ${total} AT HOME`;
  return `
    ${styleBlock()}
    <style>
      .hz-r1-hero { display:grid; grid-template-columns:1fr 1fr; border-top:3px solid var(--c-accent); }
      .hz-r1-stat { padding:clamp(8px, 1.6cqh, 14px) clamp(12px, 2.4cqw, 20px); display:flex; flex-direction:column; gap:2px; min-width:0; }
      .hz-r1-stat--home { background:var(--c-accent); color:var(--wx-red-fg); }
      .hz-r1-stat--away { background:var(--wx-tint); color:var(--c-text); }
      .hz-r1-stat-label { font-family:var(--wx-mono); font-size:11px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; opacity:.9; }
      .hz-r1-stat-num { font-family:var(--wx-black); font-size:clamp(28px, 6cqw, 44px); line-height:1; }
      .hz-r1-grid { flex:1; border-top:3px solid var(--c-accent); padding:14px; display:grid; grid-template-columns:repeat(auto-fill,minmax(96px,1fr)); gap:14px 12px; align-content:start; overflow:hidden; background:var(--wx-paper); }

      @container (max-height: 240px) {
        .hz-r1-hero { display:none; }
      }
    </style>
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({ title: (data.label || "HOME"), accent: "red", right: headerRight })}
      <div class="hz-r1-hero">
        <div class="hz-r1-stat hz-r1-stat--home">
          <span class="hz-r1-stat-label">Home</span>
          <span class="wx-tnum hz-r1-stat-num">${home}</span>
        </div>
        <div class="hz-r1-stat hz-r1-stat--away">
          <span class="hz-r1-stat-label" style="color:var(--wx-ink-60)">Away</span>
          <span class="wx-tnum hz-r1-stat-num" style="color:var(--c-accent)">${away}</span>
        </div>
      </div>
      <div class="hz-r1-grid">
        ${items.length === 0 ? emptyState(data.label) : items.map((it) => {
          const accent = stateAccent(it.state);
          return `
            <div style="display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0">
              ${avatar(it, 52)}
              <span style="font-family:var(--wx-grotesk);font-size:12px;font-weight:600;color:var(--c-text);text-align:center;line-height:1.15;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(it.name)}</span>
              <span style="display:inline-flex;align-items:center;gap:4px;background:${WX.tint(accent)};color:var(--c-text);padding:2px 7px;border-radius:999px;font-family:var(--wx-mono);font-size:10px;letter-spacing:.06em;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                <span style="width:5px;height:5px;border-radius:50%;background:${WX.col(accent)};display:inline-block;flex-shrink:0"></span>
                ${escapeHtml(stateLabel(it.state))}
              </span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (colour-blocked tile per person)
// ===========================================================
function renderG2(data) {
  const items = data.items || [];
  if (items.length === 0) {
    return `
      ${styleBlock()}
      <div class="wx-art" style="font-family:var(--wx-geo);display:flex;align-items:center;justify-content:center;background:var(--wx-paper);height:100%">
        ${emptyState(data.label)}
      </div>
    `;
  }
  // 2 columns scales nicely up to 8 tiles; falls back to 3 cols past
  // that so the larger households still fit in the cell.
  const cols = items.length > 6 ? 3 : items.length > 1 ? 2 : 1;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:grid;grid-template-columns:repeat(${cols},1fr);gap:4px;background:var(--wx-ink);height:100%">
      ${items.map((it) => {
        const accent = stateAccent(it.state);
        return `
          <div style="background:${WX.col(accent)};color:${WX.inkOn(accent)};padding:14px 16px;display:flex;flex-direction:column;justify-content:space-between;min-width:0;overflow:hidden">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
              ${avatar(it, 40)}
              ${WX.icon(stateIcon(it.state), { size: 20, color: WX.inkOn(accent) })}
            </div>
            <div style="min-width:0">
              <span class="wx-tnum" style="font-family:var(--wx-black);font-size:26px;line-height:.9;display:block;text-transform:uppercase;letter-spacing:.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(stateLabel(it.state))}</span>
              <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.06em;opacity:.85;display:block;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(it.name)}</span>
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, list view with accent dot)
// ===========================================================
function renderS3(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">${escapeHtml(data.label || "Home")}</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${summary.home ?? 0} / ${summary.total ?? items.length} · ${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
        ${items.length === 0 ? emptyState(data.label) : items.map((it, i) => {
          const accent = stateAccent(it.state);
          return `
            <div style="display:flex;align-items:center;gap:10px;padding:7px 0;${i < items.length - 1 ? "border-bottom:1px solid var(--c-line);" : ""}">
              <span style="width:8px;height:8px;border-radius:50%;background:${WX.col(accent)};display:inline-block;flex-shrink:0"></span>
              <span style="font-size:13px;color:var(--wx-ink);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(it.name)}</span>
              <span class="wx-tnum" style="font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--wx-ink-60);font-weight:600">${escapeHtml(stateLabel(it.state))}</span>
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// D4 — DATA (ratio bar + per-person rows + optional history strip)
// ===========================================================
// Render the optional 24h history strip for one person. We bucket the
// raw state samples into 48 equal slots (one every 30 minutes) and
// paint each bucket as a coloured cell — green = home, blue = custom
// zone, paper-3 = away. Empty buckets inherit "away" so a person who's
// been out all night reads as one continuous muted strip.
function historyStrip(history) {
  if (!Array.isArray(history) || history.length === 0) return "";
  const slots = 48;
  const n = history.length;
  const cells = [];
  for (let i = 0; i < slots; i++) {
    const lo = Math.floor((i * n) / slots);
    const hi = Math.max(lo + 1, Math.floor(((i + 1) * n) / slots));
    // Use the bucket's last sample — closer to the rising edge than an
    // average would be for the discrete state values we're dealing with.
    const sample = history[Math.min(n - 1, hi - 1)];
    let bg = "var(--wx-paper-3)";
    if (isHome(sample)) bg = WX.col("green");
    else if (isZone(sample)) bg = WX.col("blue");
    cells.push(`<span style="flex:1;background:${bg}"></span>`);
  }
  return `
    <div style="display:flex;gap:1px;height:6px;background:var(--wx-paper-3);margin-top:4px">
      ${cells.join("")}
    </div>
  `;
}

function renderD4(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  const total = summary.total || items.length || 1;
  const homePct = ((summary.home || 0) / total) * 100;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 20px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
        <span style="font-family:var(--wx-black);font-size:17px;letter-spacing:.03em">${escapeHtml((data.label || "HOME").toUpperCase())} · PRESENCE</span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">${escapeHtml(data.time || nowTime())}</span>
      </div>
      <div style="display:flex;align-items:baseline;gap:10px">
        <span class="wx-tnum" style="font-family:var(--wx-black);font-size:36px;line-height:.85">${summary.home ?? 0}<span style="color:var(--wx-ink-60);font-size:24px"> / ${summary.total ?? items.length}</span></span>
        <span style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);letter-spacing:.08em">AT HOME · ${summary.away ?? 0} AWAY</span>
      </div>
      <div style="display:flex;height:8px;margin:8px 0 12px;background:var(--wx-paper-3);overflow:hidden">
        <div style="width:${homePct.toFixed(1)}%;background:${WX.col("green")}"></div>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;gap:8px;overflow:hidden">
        ${items.length === 0 ? emptyState(data.label) : items.map((it) => {
          const accent = stateAccent(it.state);
          return `
            <div style="display:flex;flex-direction:column;gap:2px">
              <div style="display:flex;align-items:center;gap:10px;min-width:0">
                ${avatar(it, 32)}
                <span style="flex:1;font-size:13px;font-weight:600;color:var(--wx-ink);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(it.name)}</span>
                <span style="display:inline-flex;align-items:center;gap:5px;font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink)">
                  <span style="width:6px;height:6px;border-radius:50%;background:${WX.col(accent)};display:inline-block"></span>
                  <b style="letter-spacing:.06em">${escapeHtml(stateLabel(it.state))}</b>
                </span>
                <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);min-width:54px;text-align:right">${escapeHtml(relTime(it.last_changed))}</span>
              </div>
              ${historyStrip(it.history)}
            </div>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

// ===========================================================
// LEGACY — quiet paper avatar grid, no solid accent panels.
// ===========================================================
function renderLegacy(data) {
  const items = data.items || [];
  const summary = data.summary || {};
  const headerRight = `${summary.home ?? 0} / ${summary.total ?? items.length} AT HOME`;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column;background:var(--wx-paper)">
      ${WX.darkHeader({ title: (data.label || "HOME"), accent: "ink", right: headerRight })}
      <div style="flex:1;border-top:2px solid var(--wx-ink);padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:14px 12px;align-content:start;overflow:hidden">
        ${items.length === 0 ? emptyState(data.label) : items.map((it) => {
          const accent = stateAccent(it.state);
          return `
            <div style="display:flex;flex-direction:column;align-items:center;gap:6px;min-width:0">
              ${avatar(it, 52)}
              <span style="font-family:var(--wx-grotesk);font-size:12px;font-weight:600;color:var(--wx-ink);text-align:center;line-height:1.15;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(it.name)}</span>
              <span style="display:inline-flex;align-items:center;gap:4px;background:${WX.tint(accent)};color:var(--wx-ink);padding:2px 7px;border-radius:999px;font-family:var(--wx-mono);font-size:10px;letter-spacing:.06em;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                <span style="width:5px;height:5px;border-radius:50%;background:${WX.col(accent)};display:inline-block;flex-shrink:0"></span>
                ${escapeHtml(stateLabel(it.state))}
              </span>
            </div>
          `;
        }).join("")}
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
  legacy: renderLegacy,
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
