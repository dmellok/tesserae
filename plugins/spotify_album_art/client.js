// spotify_album_art — Spectra full-bleed image. Just the current
// album art at full size; a tiny bottom-overlay surfaces the track
// + artist when something is playing, fades to a "Not playing"
// placeholder when idle.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_album_art">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Spotify</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (data.idle || !data.album_art) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="spotify_album_art">
        <div class="bleed-empty">Not playing.</div>
      </div>`;
    return;
  }

  const subBits = [data.track, data.artist].filter(Boolean);

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="spotify_album_art">
      <img src="${escapeHtml(data.album_art)}" alt="${escapeHtml(data.track || "")}">
      ${subBits.length
        ? `<div class="img-overlay">
            ${data.track ? `<span class="title">${escapeHtml(data.track)}</span>` : ""}
            ${data.artist ? `<span class="sub">${escapeHtml(data.artist)}</span>` : ""}
          </div>`
        : ""}
    </div>`;
}
