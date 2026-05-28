// spotify_now_playing — current track title / artist / album (+ art + progress).
// Bauhaus shell (widget-bauhaus.css) + cqw-scaled text so it reads at panel size.

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
  <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/spotify_now_playing/client.css">`;

export default async function render(shadow, ctx) {
  const data = ctx.data || {};

  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="wb-root is-error">
        <i class="ph ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  if (data.idle || !data.track) {
    shadow.innerHTML = `${HEAD}
      <div class="wb-root">
        <div class="wb-empty">
          <i class="ph-duotone ph-music-notes" aria-hidden="true"></i>
          <div class="wb-empty-primary">Nothing playing</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const opts = ctx.cell.options || {};
  const showArt = opts.show_art !== false && !!data.album_art;
  const showAlbum = opts.show_album !== false && !!data.album;
  const showProgress = opts.show_progress !== false && data.duration_ms > 0;

  const art = showArt
    ? `<div class="snp-art"><img src="${escapeHtml(data.album_art)}" alt="" aria-hidden="true"></div>`
    : "";

  const pct = showProgress
    ? Math.max(0, Math.min(100, (data.progress_ms / data.duration_ms) * 100))
    : 0;
  const progress = showProgress
    ? `<div class="snp-progress">
         <div class="snp-bar"><span style="width:${pct}%"></span></div>
         <div class="snp-times"><span>${mmss(data.progress_ms)}</span><span>${mmss(data.duration_ms)}</span></div>
       </div>`
    : "";

  const stateIcon = data.is_playing ? "play-circle" : "pause-circle";

  shadow.innerHTML = `${HEAD}
    <div class="wb-root size-${size}">
      <div class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">Now Playing</span>
        <i class="wb-bar-icon ph ph-spotify-logo" aria-hidden="true"></i>
      </div>
      <div class="snp-body${showArt ? " has-art" : ""}">
        ${art}
        <div class="snp-meta">
          <div class="snp-trackrow">
            <i class="ph-fill ph-${stateIcon} snp-state" aria-hidden="true"></i>
            <span class="snp-track">${escapeHtml(data.track)}</span>
          </div>
          <div class="snp-artist">${escapeHtml(data.artist)}</div>
          ${showAlbum ? `<div class="snp-album">${escapeHtml(data.album)}</div>` : ""}
          ${progress}
        </div>
      </div>
    </div>`;
}
