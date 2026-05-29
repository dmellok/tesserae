// spotify_now_playing — Bauhaus now-playing card. Black header strip,
// a 50/50 hero (track block + square album-art panel), a bold accent
// progress block, and a play/pause state icon. Modelled on the weather
// widgets' colour-block language, not thin rows.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function mmss(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/spotify_now_playing/client.css">`;

export default async function render(shadow, ctx) {
  const data = ctx.data || {};

  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="root error">
        <i class="ph ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  if (data.idle || !data.track) {
    shadow.innerHTML = `${HEAD}
      <div class="root">
        <header class="wb-bar">
          <span class="wb-mark" aria-hidden="true"></span>
          <span class="wb-title">Now Playing</span>
          <i class="wb-bar-icon ph ph-spotify-logo" aria-hidden="true"></i>
        </header>
        <div class="snp-stub">
          <i class="ph-duotone ph-music-notes" aria-hidden="true"></i>
          <div class="snp-stub-primary">Nothing playing</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const opts = ctx.cell.options || {};
  const showArt = opts.show_art !== false && !!data.album_art;
  const showAlbum = opts.show_album !== false && !!data.album;
  const showProgress = opts.show_progress !== false && data.duration_ms > 0;
  const stateIcon = data.is_playing ? "play-circle" : "pause-circle";

  const artPanel = showArt
    ? `<div class="snp-art" aria-hidden="true"><img src="${escapeHtml(data.album_art)}" alt=""></div>`
    : `<div class="snp-art snp-art--placeholder" aria-hidden="true"><i class="ph-bold ph-vinyl-record"></i></div>`;

  const pct = showProgress
    ? Math.max(0, Math.min(100, (data.progress_ms / data.duration_ms) * 100))
    : 0;
  const progress = showProgress
    ? `<section class="snp-progress">
         <div class="snp-bar"><span style="width:${pct}%"></span></div>
         <div class="snp-times">
           <span>${mmss(data.progress_ms)}</span>
           <span>${mmss(data.duration_ms)}</span>
         </div>
       </section>`
    : "";

  shadow.innerHTML = `${HEAD}
    <div class="root size-${size}${showArt ? " has-art" : ""}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">Now Playing</span>
        <i class="wb-bar-icon ph ph-spotify-logo" aria-hidden="true"></i>
      </header>
      <section class="snp-hero">
        <div class="snp-meta">
          <div class="snp-trackrow">
            <i class="ph-fill ph-${stateIcon} snp-state" aria-hidden="true"></i>
            <span class="snp-track">${escapeHtml(data.track)}</span>
          </div>
          <div class="snp-artist"><i class="ph-bold ph-user snp-artist-icon" aria-hidden="true"></i>${escapeHtml(data.artist)}</div>
          ${showAlbum ? `<div class="snp-album"><i class="ph-bold ph-disc snp-album-icon" aria-hidden="true"></i>${escapeHtml(data.album)}</div>` : ""}
        </div>
        ${artPanel}
      </section>
      ${progress}
    </div>`;
}
