// ha_media, Spectra image archetype. Album art lives in two
// layers: a blurred-bleed backdrop that fills the body, and the
// crisp hero on top. The meta lockup sits below with title +
// artist, the progress bar carries elapsed / remaining stamps, and
// a static SVG audio-waveform deterministically derived from the
// track title sits underneath the bar (e-ink can't show a live
// waveform anyway, but a stable per-track waveform-glyph reads as
// "this is the song" + decorative texture in one element).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const STATE_ICON = {
  playing: "ph-play",
  paused: "ph-pause",
  idle: "ph-circle",
  off: "ph-power",
  unavailable: "ph-warning-circle",
};

const STATE_ACCENT = {
  playing: "var(--accent-3)",
  paused: "var(--accent-2)",
  idle: "var(--text-muted)",
  off: "var(--text-muted)",
  unavailable: "var(--accent-1)",
};

function stateIcon(s) {
  return STATE_ICON[(s || "").toLowerCase()] || "ph-music-notes";
}

function stateAccent(s) {
  return STATE_ACCENT[(s || "").toLowerCase()] || "var(--text-secondary)";
}

function fmtMmSs(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// Stable seeded PRNG so the same string always produces the same
// waveform glyph. Implements xmur3 + sfc32, small, fast, good enough
// for visual jitter.
function seededRand(seedStr) {
  let h = 1779033703 ^ String(seedStr || "").length;
  for (let i = 0; i < String(seedStr || "").length; i++) {
    h = Math.imul(h ^ String(seedStr).charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  let a = h >>> 0;
  let b = (h ^ 0x9e3779b9) >>> 0;
  let c = (h ^ 0x243f6a88) >>> 0;
  let d = (h ^ 0xb7e15162) >>> 0;
  return function () {
    a |= 0; b |= 0; c |= 0; d |= 0;
    const t = (((a + b) | 0) + d) | 0;
    d = (d + 1) | 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) | 0;
    c = (c << 21) | (c >>> 11);
    c = (c + t) | 0;
    return (t >>> 0) / 4294967296;
  };
}

// Audio waveform glyph. Deterministic per track title: 56 vertical
// bars whose heights are seeded from the seed string. The portion
// to the left of the play cursor (positionPct) renders in the
// state accent at full opacity; the portion to the right renders
// at 28% so the elapsed-vs-remaining boundary reads visually.
function waveformSvg({ seed, positionPct, accent }) {
  const W = 280;
  const H = 36;
  const bars = 56;
  const gap = 1.5;
  const barW = (W - gap * (bars - 1)) / bars;
  const rng = seededRand(seed || "ha_media");
  const filledTo = Math.max(0, Math.min(bars, Math.round((positionPct / 100) * bars)));
  const cells = [];
  for (let i = 0; i < bars; i++) {
    // Each bar gets two stacked random values + a mid-band bias so
    // the silhouette reads as "audio waveform" rather than "barcode".
    const a = rng();
    const b = rng();
    const mag = 0.3 + Math.abs(a - 0.5) * 1.4 + Math.abs(b - 0.5) * 0.6;
    const norm = Math.max(0.15, Math.min(1, mag));
    const barH = norm * H;
    const x = i * (barW + gap);
    const y = (H - barH) / 2;
    const elapsed = i < filledTo;
    cells.push(`
      <rect x="${x.toFixed(2)}" y="${y.toFixed(2)}"
            width="${barW.toFixed(2)}" height="${barH.toFixed(2)}"
            rx="${(barW / 2).toFixed(2)}"
            fill="${accent}" opacity="${elapsed ? 1 : 0.3}"/>`);
  }
  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"
         width="100%" height="100%" aria-hidden="true">
      ${cells.join("")}
    </svg>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showProgress = opts.show_progress !== false;
  const showBleed = opts.show_art_bleed !== false;
  const showWaveform = opts.show_waveform !== false;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_media">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Media</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const name = data.name || "Media";
  const state = data.state || "";
  const title = data.title || "";
  const artist = data.artist || "";
  const album = data.album || "";
  const source = data.source || "";
  const art = data.art_url || "";

  const accent = stateAccent(state);
  const ph = stateIcon(state);
  const isPlaying = ["playing", "paused"].includes((state || "").toLowerCase());
  const metaTitle = isPlaying && title ? title : name;
  const metaSubBits = isPlaying
    ? [artist, album].filter(Boolean)
    : [source || state || "idle"];
  const metaSub = metaSubBits.join(" · ");

  const heroBody = art
    ? `<img src="${escapeHtml(art)}" alt="">`
    : `<i class="ph-bold ph-music-notes"></i>`;

  // Album-art bleed, a stretched, blurred copy of the same image
  // sitting behind the body content. Adds atmospheric warmth to the
  // tile so the album palette informs the whole cell, not just the
  // small hero square. Skipped when there's no art or the user has
  // turned it off.
  const bleedLayer = (showBleed && art)
    ? `<div class="media-bleed" aria-hidden="true">
        <img src="${escapeHtml(art)}" alt="">
      </div>`
    : "";

  // Build the progress bar. We need this BEFORE building the waveform
  // because the waveform's fill cursor uses position_pct.
  const hasProgress = Number.isFinite(data.position_pct)
    && Number.isFinite(data.media_position)
    && Number.isFinite(data.media_duration)
    && data.media_duration > 0;
  const positionPct = hasProgress ? Math.max(0, Math.min(100, data.position_pct)) : 0;

  let waveform = "";
  if (showWaveform && isPlaying) {
    const seed = `${artist}|${album}|${title}` || name;
    waveform = `<div class="media-waveform">${waveformSvg({ seed, positionPct, accent })}</div>`;
  }

  let progressBar = "";
  if (showProgress && hasProgress) {
    progressBar = `
      <div class="img-progress">
        <div class="img-progress-track">
          <div class="img-progress-fill" style="width:${positionPct.toFixed(1)}%;background:${accent}"></div>
        </div>
        <div class="img-progress-times">
          <span>${escapeHtml(fmtMmSs(data.media_position))}</span>
          <span>${escapeHtml(fmtMmSs(data.media_duration))}</span>
        </div>
      </div>`;
  }

  const layout = `
    .w[data-widget="ha_media"] {
      position: relative;
      overflow: hidden;
    }
    /* Album-art bleed. Stretched + blurred + dimmed copy of the
       album art behind everything. Sits at z-index 0; .w-body
       sits at z-index 1 so its content shows on top. The blur
       softens the source so the tile keeps its bauhaus calm and
       the art's palette tints the whole cell. */
    .media-bleed {
      position: absolute;
      inset: 0;
      z-index: 0;
      overflow: hidden;
      pointer-events: none;
    }
    .media-bleed img {
      width: 110%;
      height: 110%;
      position: absolute;
      top: -5%;
      left: -5%;
      object-fit: cover;
      filter: blur(28px) saturate(1.1);
      opacity: 0.45;
    }
    /* Soft surface-tinted overlay so text contrast stays legible
       on top of the blurred art (otherwise white-on-bright-art
       fails). */
    .media-bleed::after {
      content: "";
      position: absolute;
      inset: 0;
      background: color-mix(in oklab, var(--surface) 55%, transparent);
    }
    .w[data-widget="ha_media"] .w-title,
    .w[data-widget="ha_media"] .w-body {
      position: relative;
      z-index: 1;
    }
    .media-waveform {
      width: 100%;
      height: 2.2em;
      margin-bottom: var(--space-1);
    }
    .media-waveform svg {
      width: 100%;
      height: 100%;
      display: block;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_media">
      ${bleedLayer}
      <div class="w-title">
        <i class="ph-bold ${ph}" style="color:${accent}"></i>
        <h3>${escapeHtml(name)}</h3>
        ${state ? `<span class="w-title-meta" style="color:${accent}">${escapeHtml(state)}</span>` : ""}
      </div>
      <div class="w-body img-body">
        <div class="img-hero">${heroBody}</div>
        <div class="img-meta">
          <span class="title">${escapeHtml(metaTitle)}</span>
          <span class="sub">${escapeHtml(metaSub)}</span>
          ${waveform}
          ${progressBar}
        </div>
      </div>
    </div>`;
}
