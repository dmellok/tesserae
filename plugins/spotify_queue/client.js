// spotify_queue — Spectra list archetype. Optionally pins the
// currently-playing track at the top (in accent-3) with the upcoming
// queue items as zebra rows below.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function row(item, idx, isCurrent) {
  const accent = isCurrent ? "var(--accent-3)" : "var(--accent-5)";
  const titleStyle = isCurrent ? `color:var(--accent-3);font-weight:var(--fw-black)` : "";
  // Album art thumbnail replaces the leading icon when available so
  // the queue reads like a real player list rather than rows of
  // identical music-note icons. Falls back to ph-music-note / ph-play
  // when Spotify didn't return an image for the track.
  const art = item.album_art_thumb || item.album_art;
  const lead = art
    ? `<img src="${escapeHtml(art)}" alt="" style="width:1.6em;height:1.6em;object-fit:cover;flex:0 0 auto;border-left:var(--stroke-2) solid ${accent}">`
    : `<i class="ph-bold ${isCurrent ? "ph-play" : "ph-music-note"}" style="color:${accent}"></i>`;
  return `
    <div class="list-row ${(idx % 2 && !isCurrent) ? "is-zebra" : ""}">
      <div class="list-lead">
        ${lead}
        <span class="list-title" style="${titleStyle}">${escapeHtml(item.track || item.title || "—")}</span>
      </div>
      <span class="list-meta u-muted" style="font-weight:var(--fw-semi)">${escapeHtml(item.artist || "")}</span>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_queue">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Spotify Queue</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (data.idle) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_queue">
        <div class="w-title">
          <i class="ph-bold ph-queue" style="color:var(--text-muted)"></i>
          <h3>Spotify Queue</h3>
        </div>
        <div class="w-body"><p class="u-muted">Not playing.</p></div>
      </div>`;
    return;
  }

  const queue = Array.isArray(data.queue) ? data.queue : [];
  const current = data.currently_playing || null;
  const total = queue.length + (current ? 1 : 0);

  if (total === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="spotify_queue">
        <div class="w-title">
          <i class="ph-bold ph-queue" style="color:var(--accent-3)"></i>
          <h3>Spotify Queue</h3>
        </div>
        <div class="w-body"><p class="u-muted">Queue empty.</p></div>
      </div>`;
    return;
  }

  const rows = [
    current ? row(current, 0, true) : "",
    ...queue.map((t, i) => row(t, i + (current ? 1 : 0), false)),
  ].join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="spotify_queue">
      <div class="w-title">
        <i class="ph-bold ph-queue" style="color:var(--accent-3)"></i>
        <h3>Spotify Queue</h3>
        <span class="w-title-meta">${queue.length} UP NEXT</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
