// ha_camera — Spectra image archetype. Camera snapshot as the hero
// image, meta strip below carries state (idle/recording/streaming) +
// motion when HA exposes it.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const STATE_ACCENT = {
  recording: "var(--accent-1)",
  streaming: "var(--accent-4)",
  idle: "var(--text-muted)",
};

function stateAccent(s) {
  return STATE_ACCENT[(s || "").toLowerCase()] || "var(--text-secondary)";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_camera">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Camera</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  // Server returns ``{label, items: [{image_url, name, state, motion, …}]}``.
  // Single-camera widget renders ``items[0]``; if a future build adds
  // multi-camera support it can read items[1..] for the grid. Fall back
  // to top-level fields when items is absent so older payloads (or a
  // hand-rolled sample) still work.
  const cam = (Array.isArray(data.items) && data.items.length > 0)
    ? data.items[0]
    : data;
  const name = cam.name || data.label || "Camera";
  const url = cam.image_url || "";
  const state = cam.state || "";
  const motion = cam.motion === true;

  const accent = stateAccent(state);

  const heroBody = url
    ? `<img src="${escapeHtml(url)}" alt="${escapeHtml(name)}">`
    : `<i class="ph-bold ph-video-camera-slash"></i>`;

  const stateBits = [];
  if (state) stateBits.push(state);
  if (motion) stateBits.push("motion");
  const sub = stateBits.length ? stateBits.join(" · ") : "no signal";

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_camera">
      <div class="w-title">
        <i class="ph-bold ph-video-camera" style="color:${accent}"></i>
        <h3>${escapeHtml(name)}</h3>
        ${motion ? `<span class="w-title-meta" style="color:var(--accent-1)">MOTION</span>` : ""}
      </div>
      <div class="w-body img-body">
        <div class="img-hero">${heroBody}</div>
        <div class="img-meta">
          <span class="sub" style="color:${accent}">${escapeHtml(sub)}</span>
        </div>
      </div>
    </div>`;
}
