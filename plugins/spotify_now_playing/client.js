// spotify_now_playing — Spectra image archetype with track meta.
// Same shape as ha_media: album art is the hero, w-title carries
// a play/pause state icon + the "Spotify" identifier, img-meta
// stacks the track + artist + album.

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

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
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

  // Progress chip — "1:23 / 3:45" when both fields are present.
  const progress = fmtMmSs(data.progress_ms);
  const duration = fmtMmSs(data.duration_ms);
  const timeMeta = (progress && duration) ? `${progress} / ${duration}` : (progress || "");

  const heroBody = data.album_art
    ? `<img src="${escapeHtml(data.album_art)}" alt="${escapeHtml(data.album || "")}">`
    : `<i class="ph-bold ph-music-notes"></i>`;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="spotify_now_playing">
      <div class="w-title">
        <i class="ph-bold ${stateIcon}" style="color:${stateAccent}"></i>
        <h3>Spotify</h3>
        <span class="w-title-meta" style="color:${stateAccent}">${stateLabel}${timeMeta ? ` · ${escapeHtml(timeMeta)}` : ""}</span>
      </div>
      <div class="w-body img-body">
        <div class="img-hero">${heroBody}</div>
        <div class="img-meta">
          <span class="title">${escapeHtml(data.track)}</span>
          <span class="sub">${escapeHtml([data.artist, data.album].filter(Boolean).join(" · "))}</span>
        </div>
      </div>
    </div>`;
}
