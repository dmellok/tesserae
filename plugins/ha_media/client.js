// ha_media — Home Assistant media_player "now playing" tile.
//
// Brand-new widget, no legacy variant. Four directions, picked per-cell
// via the ``variant`` option, all built on the bauhaus / weather_core
// design tokens (Spectra-6 palette + Archivo / Archivo Black / Space
// Grotesk / Space Mono / Helvetica stacks):
//
//   r1  Refined    Charcoal header "NOW PLAYING · <SOURCE>" + album art
//                  square on the left, title/artist/album block on the
//                  right, progress bar at the bottom. Blue accent.
//   g2  Geometric  Album art as a giant coloured block (or solid yellow
//                  block with the album name overlaid if no art); big
//                  Archivo Black title beside it with meta + progress.
//   s3  Swiss      Hairline header, light typography, square art panel
//                  on left, meta in light weights on right, simple
//                  underline as a progress mark.
//   d4  Data       Horizontal timeline + synthetic "waveform" chip
//                  strip (purely decorative — HA doesn't expose audio
//                  data), volume gauge, source label, position /
//                  duration in tabular numerals.
//
// Server hands us: `name`, `state` ("playing"/"paused"/"idle"/"off"),
// `title`, `artist`, `album`, `art_url`, `source`, `volume_pct`,
// `media_position`, `media_duration`, `position_pct`. When state isn't
// "playing" we paint a clean rest state with the player name + status.

import { WX } from "../weather_core/static/wx-common.js";

const DEFAULT_FONT = "var(--wx-grotesk)";

function escapeHtml(s) { return WX.escapeHtml(s); }

// ----- formatting helpers -----

function mmss(secs) {
  if (secs == null || Number.isNaN(Number(secs))) return "—";
  const total = Math.max(0, Math.floor(Number(secs)));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

// Pick a coloured fallback block when the entity has no album art. We
// hash the track / album name so the same track reliably gets the same
// colour rather than flickering between renders.
const FALLBACK_ACCENTS = ["blue", "yellow", "green", "red"];
function fallbackAccent(seed) {
  const s = String(seed || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return FALLBACK_ACCENTS[Math.abs(h) % FALLBACK_ACCENTS.length];
}

// Phosphor glyph that matches the player's current state. Used by all
// variants so the rest-state copy reads at a glance.
function stateIcon(state) {
  if (state === "playing") return "play";
  if (state === "paused") return "pause";
  if (state === "off" || state === "unavailable") return "speaker-simple-slash";
  return "speaker-simple-low"; // idle
}

function stateLabel(state) {
  if (state === "playing") return "PLAYING";
  if (state === "paused") return "PAUSED";
  if (state === "idle") return "IDLE";
  if (state === "off") return "OFF";
  return "UNAVAILABLE";
}

function isResting(d) {
  // Treat anything that isn't actively playing AND has no track
  // metadata as a rest state. Some players hold onto the last track
  // info while paused — those still render as a normal "paused" card.
  if (d.state === "playing" || d.state === "paused") return !d.title;
  return true;
}

function styleBlock() {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus-wx.css">
    <link rel="stylesheet" href="/plugins/ha_media/client.css">
  `;
}

// Album art element — img tag if we have a URL, otherwise a coloured
// block with the album initial + a music-notes glyph. ``shape`` lets
// the variant pick a square or wider rectangle; ``accent`` overrides
// the hashed fallback colour (used by G2).
function artBlock(d, { size = "100%", radius = 0, accent = null } = {}) {
  const seed = d.album || d.title || d.name || "";
  const a = accent || fallbackAccent(seed);
  const sizeStyle = typeof size === "number" ? `${size}px` : size;
  // Aspect-ratio: 1/1 forces the frame square even when ``size`` is a
  // percentage — ``height:30%`` of a wide cell ≠ ``width:30%`` so the
  // pre-aspect-ratio version came out 130×120 in a 640×400 cell. Now
  // width drives, height follows.
  if (d.art_url) {
    return `
      <div class="hm-art" style="width:${sizeStyle};aspect-ratio:1/1;border-radius:${radius}px;background:var(--wx-paper-3);overflow:hidden;flex-shrink:0">
        <img src="${escapeHtml(d.art_url)}" alt="" style="width:100%;height:100%;object-fit:cover;display:block" />
      </div>
    `;
  }
  const initial = (seed.trim()[0] || "?").toUpperCase();
  return `
    <div class="hm-art hm-art--placeholder" style="width:${sizeStyle};aspect-ratio:1/1;border-radius:${radius}px;background:${WX.col(a)};color:${WX.inkOn(a)};display:flex;align-items:center;justify-content:center;flex-shrink:0;position:relative">
      <span style="font-family:var(--wx-black);font-size:clamp(28px, 30%, 96px);line-height:1">${escapeHtml(initial)}</span>
      <i class="ph-bold ph-music-notes" style="position:absolute;bottom:8px;right:10px;font-size:18px;opacity:.7" aria-hidden="true"></i>
    </div>
  `;
}

// Standard progress bar — track + filled section + optional position /
// duration row underneath.
function progressBar(d, { accent = "blue", showTimes = true } = {}) {
  const pct = d.position_pct == null ? 0 : Math.max(0, Math.min(100, d.position_pct));
  return `
    <div style="display:flex;flex-direction:column;gap:4px">
      <div style="height:4px;background:var(--c-line);position:relative">
        <div style="height:100%;width:${pct.toFixed(1)}%;background:${WX.col(accent)}"></div>
      </div>
      ${showTimes ? `
        <div class="wx-tnum" style="display:flex;justify-content:space-between;font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">
          <span>${escapeHtml(mmss(d.media_position))}</span>
          <span>${escapeHtml(mmss(d.media_duration))}</span>
        </div>
      ` : ""}
    </div>
  `;
}

// ===========================================================
// R1 — REFINED (art panel on accent + meta on tint + progress)
// ===========================================================
function renderR1(data) {
  const showProgress = data.show_progress !== false;
  const headerRight = data.state === "playing" || data.state === "paused"
    ? `${WX.icon(stateIcon(data.state), { size: 14, color: "var(--wx-paper)" })}  ${stateLabel(data.state)}`
    : nowTime();
  const sourceTag = data.source ? ` · ${data.source.toUpperCase()}` : "";
  if (isResting(data)) {
    return restState(data, "r1");
  }
  return `
    ${styleBlock()}
    <style>
      .hm-r1-body { flex:1; display:grid; grid-template-columns:auto 1fr; gap:0; padding:0; min-height:0; border-top:3px solid var(--c-accent); }
      .hm-r1-art { background:var(--c-accent); padding:clamp(10px, 2.4cqw, 18px); display:flex; align-items:center; justify-content:center; min-width:0; }
      .hm-r1-art > div, .hm-r1-art > img { background:var(--wx-red-fg) !important; }
      .hm-r1-meta { background:var(--wx-tint); padding:clamp(12px, 2.6cqw, 18px) clamp(14px, 3cqw, 20px); display:flex; flex-direction:column; justify-content:center; gap:6px; min-width:0; }
      .hm-r1-title { font-family:var(--wx-black); font-size:clamp(16px, 4cqw, 26px); line-height:1.05; color:var(--c-accent); overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
      .hm-r1-line { display:flex; align-items:center; gap:6px; color:var(--wx-ink-60); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .hm-r1-progress { padding:8px 16px 12px; background:var(--wx-paper); border-top:3px solid var(--c-accent); }

      @container (max-width: 360px) {
        .hm-r1-body { grid-template-columns:1fr; }
      }
    </style>
    <div class="wx-art" style="font-family:${DEFAULT_FONT};display:flex;flex-direction:column">
      ${WX.darkHeader({
        title: `NOW PLAYING${sourceTag}`,
        accent: "red",
        right: headerRight,
      })}
      <div class="hm-r1-body">
        <div class="hm-r1-art">
          ${artBlock(data, { size: "clamp(70px, 22cqw, 130px)" })}
        </div>
        <div class="hm-r1-meta">
          <div class="hm-r1-title">${escapeHtml(data.title || "—")}</div>
          ${data.artist ? `<div class="hm-r1-line" style="font-size:13px">${WX.icon("user", { size: 13, color: "var(--c-accent)" })}<span style="overflow:hidden;text-overflow:ellipsis">${escapeHtml(data.artist)}</span></div>` : ""}
          ${data.album ? `<div class="hm-r1-line" style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.04em">${WX.icon("disc", { size: 12, color: "var(--c-accent)" })}<span style="overflow:hidden;text-overflow:ellipsis">${escapeHtml(data.album)}</span></div>` : ""}
        </div>
      </div>
      ${showProgress ? `
        <div class="hm-r1-progress">
          ${progressBar(data, { accent: "red" })}
        </div>
      ` : ""}
    </div>
  `;
}

// ===========================================================
// G2 — GEOMETRIC (giant colour block + Archivo Black title)
// ===========================================================
function renderG2(data) {
  const showProgress = data.show_progress !== false;
  if (isResting(data)) return restState(data, "g2");
  const accent = fallbackAccent(data.album || data.title || data.name);
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-geo);display:flex;background:var(--wx-ink);gap:4px">
      <div style="width:42%;background:${data.art_url ? "var(--wx-paper-3)" : WX.col(accent)};position:relative;display:flex;align-items:flex-end;justify-content:flex-start;color:${WX.inkOn(accent)};overflow:hidden">
        ${data.art_url
          ? `<img src="${escapeHtml(data.art_url)}" alt="" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"/>`
          : `<span style="font-family:var(--wx-black);font-size:clamp(80px, 24vw, 180px);line-height:.85;padding:0 8px;color:${WX.inkOn(accent)};opacity:.95">${escapeHtml((data.album || data.title || "?").trim()[0].toUpperCase())}</span>`
        }
        ${data.album && !data.art_url ? `<span style="position:absolute;top:14px;left:14px;font-family:var(--wx-mono);font-size:11px;letter-spacing:.08em;font-weight:700;color:${WX.inkOn(accent)};opacity:.95">${escapeHtml(data.album.toUpperCase())}</span>` : ""}
      </div>
      <div style="flex:1;background:var(--wx-paper);padding:16px 18px;display:flex;flex-direction:column;justify-content:space-between;min-width:0">
        <div style="display:flex;align-items:center;gap:8px">
          ${WX.icon(stateIcon(data.state), { size: 16, color: "var(--wx-ink)" })}
          <span style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.08em;font-weight:700">${stateLabel(data.state)}${data.source ? ` · ${escapeHtml(data.source.toUpperCase())}` : ""}</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;min-width:0">
          <div style="font-family:var(--wx-black);font-size:clamp(22px, 6vw, 36px);line-height:.95;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical">${escapeHtml(data.title || "—")}</div>
          ${data.artist ? `<div style="font-family:var(--wx-grotesk);font-size:14px;font-weight:500;color:var(--wx-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(data.artist)}</div>` : ""}
        </div>
        ${showProgress ? `
          <div style="display:flex;flex-direction:column;gap:4px">
            ${progressBar(data, { accent })}
          </div>
        ` : ""}
      </div>
    </div>
  `;
}

// ===========================================================
// S3 — SWISS (hairline header, light typography, accent rule)
// ===========================================================
function renderS3(data) {
  const showProgress = data.show_progress !== false;
  if (isResting(data)) return restState(data, "s3");
  const pct = data.position_pct == null ? 0 : Math.max(0, Math.min(100, data.position_pct));
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:var(--wx-swiss);padding:20px 26px;display:flex;flex-direction:column">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:12px;letter-spacing:.2em;font-weight:700;text-transform:uppercase">Now Playing</span>
        <span style="font-size:10.5px;letter-spacing:.16em;color:var(--wx-ink-60)">${escapeHtml((data.source || data.name || "").toUpperCase())} · ${escapeHtml(nowTime())}</span>
      </div>
      <div style="height:2px;background:var(--wx-ink);margin:12px 0"></div>
      <div style="flex:1;display:flex;gap:18px;min-height:0">
        ${artBlock(data, { size: "clamp(72px, 28%, 120px)" })}
        <div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:8px;min-width:0">
          <div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--wx-ink-60)">${stateLabel(data.state)}</div>
          <div style="font-size:clamp(20px, 5.5vw, 30px);font-weight:300;line-height:1.05;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">${escapeHtml(data.title || "—")}</div>
          ${data.artist ? `<div style="font-size:13px;font-weight:400;color:var(--wx-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(data.artist)}</div>` : ""}
          ${data.album ? `<div style="font-size:11px;font-weight:300;color:var(--wx-ink-60);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(data.album)}</div>` : ""}
        </div>
      </div>
      ${showProgress ? `
        <div style="margin-top:14px;display:flex;flex-direction:column;gap:6px">
          <div style="position:relative;height:1px;background:var(--c-line)">
            <div style="position:absolute;left:0;top:-1px;height:3px;width:${pct.toFixed(1)}%;background:var(--wx-ink)"></div>
          </div>
          <div class="wx-tnum" style="display:flex;justify-content:space-between;font-size:10.5px;letter-spacing:.1em;color:var(--wx-ink-60)">
            <span>${escapeHtml(mmss(data.media_position))}</span>
            <span>${escapeHtml(mmss(data.media_duration))}</span>
          </div>
        </div>
      ` : ""}
    </div>
  `;
}

// ===========================================================
// D4 — DATA (timeline + synthetic waveform + volume + tabular meta)
// ===========================================================
// We don't have actual audio data from HA, so the "waveform" is purely
// decorative — a deterministic random strip of bars seeded by the
// track title so the same track always renders the same shape. This
// is honest about being a visual flourish, not a misleading signal.
function waveform(seed, { bars = 48 } = {}) {
  const s = String(seed || "ha_media");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 33 + s.charCodeAt(i)) | 0;
  const rng = () => {
    h = (h * 1103515245 + 12345) & 0x7fffffff;
    return (h % 1000) / 1000;
  };
  const out = [];
  for (let i = 0; i < bars; i += 1) {
    // Bias toward a centre-heavy envelope so it reads as a "track"
    // rather than uniform noise.
    const env = 0.35 + 0.65 * Math.sin((i / bars) * Math.PI);
    out.push(Math.max(0.08, env * (0.5 + 0.5 * rng())));
  }
  return out;
}

function waveformSvg(seed, { color = "var(--wx-ink)", height = 36 } = {}) {
  const bars = waveform(seed);
  const w = 100;
  const gap = 0.5;
  const bw = (w - gap * (bars.length - 1)) / bars.length;
  const rects = bars.map((v, i) => {
    const x = i * (bw + gap);
    const bh = v * (height - 2);
    const y = (height - bh) / 2;
    return `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${bw.toFixed(2)}" height="${bh.toFixed(2)}" fill="${color}" />`;
  }).join("");
  return `<svg width="100%" height="${height}" viewBox="0 0 ${w} ${height}" preserveAspectRatio="none" aria-hidden="true">${rects}</svg>`;
}

function renderD4(data) {
  const showProgress = data.show_progress !== false;
  if (isResting(data)) return restState(data, "d4");
  const pct = data.position_pct == null ? 0 : Math.max(0, Math.min(100, data.position_pct));
  const vol = data.volume_pct;
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${DEFAULT_FONT};padding:14px 18px;display:flex;flex-direction:column;gap:10px">
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-family:var(--wx-black);font-size:15px;letter-spacing:.04em">MEDIA · ${escapeHtml((data.source || data.name || "").toUpperCase())}</span>
        <span style="display:inline-flex;align-items:center;gap:6px;font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">
          ${WX.icon(stateIcon(data.state), { size: 13, color: "var(--wx-ink-60)" })}
          ${stateLabel(data.state)}
        </span>
      </div>
      <div style="display:flex;align-items:baseline;gap:10px;min-width:0">
        <span style="font-family:var(--wx-black);font-size:clamp(20px, 5vw, 28px);line-height:.9;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">${escapeHtml(data.title || "—")}</span>
      </div>
      ${data.artist || data.album ? `
        <div style="font-family:var(--wx-mono);font-size:11px;letter-spacing:.04em;color:var(--wx-ink-60);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          ${escapeHtml(data.artist)}${data.artist && data.album ? " · " : ""}${escapeHtml(data.album)}
        </div>
      ` : ""}
      <div style="flex:1;min-height:32px;display:flex;align-items:center">
        ${waveformSvg(data.title || data.album || data.name, { color: "var(--wx-ink)", height: 40 })}
      </div>
      ${showProgress ? `
        <div style="display:flex;flex-direction:column;gap:4px">
          <div style="height:6px;background:var(--c-line);position:relative">
            <div style="height:100%;width:${pct.toFixed(1)}%;background:var(--wx-blue)"></div>
          </div>
          <div class="wx-tnum" style="display:flex;justify-content:space-between;font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60)">
            <span>${escapeHtml(mmss(data.media_position))}</span>
            <span>${escapeHtml(mmss(data.media_duration))}</span>
          </div>
        </div>
      ` : ""}
      <div style="display:flex;align-items:center;gap:10px">
        ${WX.icon("speaker-high", { size: 14, color: "var(--wx-ink-60)" })}
        <div style="flex:1;height:4px;background:var(--c-line);position:relative">
          <div style="height:100%;width:${(vol == null ? 0 : vol).toFixed(1)}%;background:var(--wx-ink)"></div>
        </div>
        <span class="wx-tnum" style="font-family:var(--wx-mono);font-size:11px;color:var(--wx-ink-60);min-width:30px;text-align:right">${vol == null ? "—" : Math.round(vol) + "%"}</span>
      </div>
    </div>
  `;
}

// ===========================================================
// REST STATE (idle / off / paused-with-no-metadata)
// ===========================================================
// One shared rest layout — variant-specific tweaks are minimal because
// the point is "this player isn't doing much". For "off" / unavailable
// we use speaker-simple-slash to make the muted state obvious.
function restState(data, variant) {
  const off = data.state === "off" || data.state === "unavailable";
  const icon = off ? "speaker-simple-slash" : stateIcon(data.state);
  const sub = off
    ? stateLabel(data.state)
    : data.state === "paused" && data.media_position != null
      ? `PAUSED · ${mmss(data.media_position)}`
      : stateLabel(data.state);
  const accent = off ? "muted" : "blue";
  const familyMap = {
    r1: DEFAULT_FONT,
    g2: "var(--wx-geo)",
    s3: "var(--wx-swiss)",
    d4: DEFAULT_FONT,
  };
  return `
    ${styleBlock()}
    <div class="wx-art" style="font-family:${familyMap[variant] || DEFAULT_FONT};display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:18px;text-align:center">
      <div style="display:flex;align-items:center;justify-content:center;width:64px;height:64px;border-radius:50%;background:${WX.tint(accent)};color:${WX.col(accent)}">
        ${WX.icon(icon, { size: 32, color: WX.col(accent) })}
      </div>
      <div style="font-family:var(--wx-black);font-size:18px;line-height:1.1">${escapeHtml(data.name || data.entity_id || "Media")}</div>
      <div style="font-family:var(--wx-mono);font-size:11.5px;letter-spacing:.1em;color:var(--wx-ink-60)">${escapeHtml(sub)}</div>
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
  // Pass the user's show_progress option through so each variant can
  // strip the progress UI without us threading it through every call.
  data.show_progress = ctx.cell.options.show_progress !== false;
  const variant = ctx.cell.options.variant || "r1";
  const renderer = VARIANTS[variant] || renderR1;
  shadow.innerHTML = renderer(data);
}
