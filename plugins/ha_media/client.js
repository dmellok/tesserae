// ha_media — Spectra image archetype. Album art is the hero; meta
// strip below shows title + artist + album lockup. Title bar carries
// a play / pause / stop icon coloured by playback state.

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

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
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

  const heroBody = art
    ? `<img src="${escapeHtml(art)}" alt="">`
    : `<i class="ph-bold ph-music-notes"></i>`;

  // Meta lockup falls back to the entity name when nothing's playing.
  const isPlaying = ["playing", "paused"].includes((state || "").toLowerCase());
  const metaTitle = isPlaying && title ? title : name;
  const metaSubBits = isPlaying
    ? [artist, album].filter(Boolean)
    : [source || state || "idle"];
  const metaSub = metaSubBits.join(" · ");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_media">
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
        </div>
      </div>
    </div>`;
}
