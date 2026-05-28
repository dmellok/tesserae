// spotify_album_art — full-bleed cover art of the current track.
// Image is bare/full-bleed; empty + error states use the shared bauhaus shell.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/spotify_album_art/client.css">`;

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
  if (data.idle || !data.album_art) {
    shadow.innerHTML = `${HEAD}
      <div class="wb-root">
        <div class="wb-empty">
          <i class="ph-duotone ph-vinyl-record" aria-hidden="true"></i>
          <div class="wb-empty-primary">Nothing playing</div>
        </div>
      </div>`;
    return;
  }

  const scale = ctx.cell.options.scale === "contain" ? "contain" : "cover";
  const dim = ctx.cell.options.dim_when_paused !== false && data.is_playing === false;
  const alt = escapeHtml(
    data.track ? `${data.track}${data.artist ? " — " + data.artist : ""}` : "Album art",
  );

  shadow.innerHTML = `${HEAD}
    <div class="sa-wrap scale-${scale}${dim ? " is-paused" : ""}">
      <img class="sa-art" src="${escapeHtml(data.album_art)}" alt="${alt}">
    </div>`;
}
