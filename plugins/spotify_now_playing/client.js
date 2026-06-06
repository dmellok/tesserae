// spotify_now_playing, Spectra image archetype. Album art is the
// hero, title bar carries play/pause state + the "Spotify" identifier,
// img-meta stacks the track + artist + album. A deterministic SVG
// waveform glyph under the progress bar paints a track-specific
// silhouette (seeded by artist|album|track) so the cell carries a
// "this is the song" texture beyond the title text itself.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtMmSs(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return "";
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// Stable seeded PRNG so the same string always produces the same
// waveform glyph. xmur3 + sfc32, small, fast, good enough.
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

function waveformSvg({ seed, positionPct, accent }) {
  const W = 280;
  const H = 32;
  const bars = 48;
  const gap = 1.5;
  const barW = (W - gap * (bars - 1)) / bars;
  const rng = seededRand(seed || "spotify");
  const filledTo = Math.max(0, Math.min(bars, Math.round((positionPct / 100) * bars)));
  const cells = [];
  for (let i = 0; i < bars; i++) {
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
  const showArt = opts.show_art !== false;
  const showAlbum = opts.show_album !== false;
  const showProgress = opts.show_progress !== false;
  const showWaveform = opts.show_waveform !== false;
  const layout = opts.layout === "side" ? "side" : "stack";
  const bodyClass = layout === "side" ? "img-body is-side" : "img-body";
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_now_playing">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Spotify</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (data.idle || !data.track) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_now_playing">
        <div class="w-title">
          <i class="ph-bold ph-pause" style="color:var(--text-muted)"></i>
          <h3>Spotify</h3>
        </div>
        <div class="w-body img-body">
          <div class="img-hero"><i class="ph-bold ph-music-notes"></i></div>
          <div class="img-meta">
            <span class="sub">Not playing</span>
          </div>
        </div>
      </div>`;
    return;
  }

  const playing = data.is_playing === true;
  const stateIcon = playing ? "ph-play" : "ph-pause";
  const stateAccent = playing ? "var(--accent-3)" : "var(--accent-2)";
  const stateLabel = playing ? "PLAYING" : "PAUSED";

  const progress = fmtMmSs(data.progress_ms);
  const duration = fmtMmSs(data.duration_ms);
  const timeMeta = (progress && duration) ? `${progress} / ${duration}` : (progress || "");

  const hero = (showArt && data.album_art)
    ? `<div class="img-hero"><img src="${escapeHtml(data.album_art)}" alt="${escapeHtml(data.album || "")}"></div>`
    : (showArt
        ? `<div class="img-hero"><i class="ph-bold ph-music-notes"></i></div>`
        : "");

  const subBits = [data.artist];
  if (showAlbum && data.album) subBits.push(data.album);
  const sub = subBits.filter(Boolean).join(" · ");

  const hasProgress = Number.isFinite(data.progress_ms) && Number.isFinite(data.duration_ms) && data.duration_ms > 0;
  const positionPct = hasProgress ? Math.max(0, Math.min(100, (data.progress_ms / data.duration_ms) * 100)) : 0;

  let waveform = "";
  if (showWaveform) {
    const seed = `${data.artist || ""}|${data.album || ""}|${data.track || ""}`;
    waveform = `<div class="spot-waveform">${waveformSvg({ seed, positionPct, accent: stateAccent })}</div>`;
  }

  let progressBar = "";
  if (showProgress && hasProgress) {
    progressBar = `
      <div class="img-progress">
        <div class="img-progress-track">
          <div class="img-progress-fill" style="width:${positionPct.toFixed(1)}%;background:${stateAccent}"></div>
        </div>
        <div class="img-progress-times">
          <span>${escapeHtml(progress || "0:00")}</span>
          <span>${escapeHtml(duration || "0:00")}</span>
        </div>
      </div>`;
  }

  const layoutCss = `
    .spot-waveform {
      width: 100%;
      height: 2em;
      margin-bottom: var(--space-1);
    }
    .spot-waveform svg {
      width: 100%;
      height: 100%;
      display: block;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layoutCss}</style>
    <div class="w" data-widget="spotify_now_playing">
      <div class="w-title">
        <i class="ph-bold ${stateIcon}" style="color:${stateAccent}"></i>
        <h3>Spotify</h3>
        <span class="w-title-meta" style="color:${stateAccent}">${stateLabel}${!showProgress && timeMeta ? ` · ${escapeHtml(timeMeta)}` : ""}</span>
      </div>
      <div class="w-body ${bodyClass}">
        ${hero}
        <div class="img-meta">
          <span class="title">${escapeHtml(data.track)}</span>
          ${sub ? `<span class="sub">${escapeHtml(sub)}</span>` : ""}
          ${waveform}
          ${progressBar}
        </div>
      </div>
    </div>`;
}
