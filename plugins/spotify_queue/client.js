// spotify_queue — Bauhaus shape: refined dark header ("Up Next" +
// Spotify mark), a now-playing lede with album art, then a numbered
// list of the next few tracks in the queue. Same shadow-DOM contract
// as the other widgets — replaces shadow.innerHTML, no animations.

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
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/spotify_queue/client.css">`;

function header(count) {
  const meta = count > 0
    ? `<span class="wb-bar-meta">${count} UP NEXT</span>`
    : "";
  return `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="wb-title">Queue</span>
      ${meta}
      <i class="wb-bar-icon ph ph-spotify-logo" aria-hidden="true"></i>
    </header>`;
}

function art(url, cls) {
  if (url) {
    return `<div class="${cls}" aria-hidden="true"><img src="${escapeHtml(url)}" alt=""></div>`;
  }
  return `<div class="${cls} sq-art--placeholder" aria-hidden="true"><i class="ph-bold ph-vinyl-record"></i></div>`;
}

function ledeBlock(track) {
  if (!track) return "";
  return `
    <article class="sq-lede">
      ${art(track.album_art || track.album_art_thumb, "sq-lede-art")}
      <div class="sq-lede-body">
        <div class="sq-lede-eyebrow">
          <i class="ph-bold ph-play sq-lede-icon" aria-hidden="true"></i>
          NOW PLAYING
        </div>
        <h3 class="sq-lede-title" title="${escapeHtml(track.track)}">${escapeHtml(track.track)}</h3>
        <div class="sq-lede-artist" title="${escapeHtml(track.artist)}">${escapeHtml(track.artist)}</div>
        <div class="sq-lede-album" title="${escapeHtml(track.album)}">${escapeHtml(track.album)}</div>
      </div>
    </article>`;
}

function queueRow(track, idx) {
  const n = String(idx + 1).padStart(2, "0");
  return `
    <article class="sq-row">
      <span class="sq-num">${n}</span>
      ${art(track.album_art_thumb || track.album_art, "sq-row-art")}
      <div class="sq-row-body">
        <div class="sq-row-title" title="${escapeHtml(track.track)}">${escapeHtml(track.track)}</div>
        <div class="sq-row-meta">
          <span class="sq-row-artist" title="${escapeHtml(track.artist)}">${escapeHtml(track.artist)}</span>
          <span class="sq-row-dur">${mmss(track.duration_ms)}</span>
        </div>
      </div>
    </article>`;
}

function emptyBody(message, icon = "ph-music-notes") {
  return `
    <div class="wb-empty">
      <i class="ph-duotone ${icon}" aria-hidden="true"></i>
      <div class="wb-empty-primary">${escapeHtml(message)}</div>
    </div>`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const size = ctx.cell.size;

  // Error state — surface the core's message verbatim. The wb-error
  // shape is shared with every other bauhaus widget.
  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="wb-root is-error">
        <i class="ph-bold ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }

  const currently = data.currently_playing || null;
  const queue = Array.isArray(data.queue) ? data.queue : [];

  // Idle (nothing playing) — bauhaus empty shell.
  if (data.idle || (!currently && queue.length === 0)) {
    shadow.innerHTML = `${HEAD}
      <div class="root size-${escapeHtml(size)}">
        ${header(0)}
        ${emptyBody("Spotify is idle.", "ph-pause-circle")}
      </div>`;
    return;
  }

  shadow.innerHTML = `${HEAD}
    <div class="root size-${escapeHtml(size)}">
      ${header(queue.length)}
      ${ledeBlock(currently)}
      <section class="sq-list">
        ${queue.map((t, i) => queueRow(t, i)).join("")}
        ${queue.length === 0 ? `<div class="sq-empty">Queue empty.</div>` : ""}
      </section>
    </div>`;
}
