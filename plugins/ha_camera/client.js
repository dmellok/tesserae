// ha_camera, Spectra image archetype with single-camera + grid
// rendering. Each tile carries a bottom overlay with name + state +
// motion plus a corner timestamp chip ("just now", "2m ago") so the
// frame's freshness is obvious without reading attributes. When the
// cell holds more than one camera, the body switches to a responsive
// CSS grid that auto-fits each tile into the available width.

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

function fmtAgo(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function tileImage(cam) {
  return cam.image_url
    ? `<img src="${escapeHtml(cam.image_url)}" alt="${escapeHtml(cam.name || "")}" loading="lazy">`
    : `<div class="cam-empty"><i class="ph-bold ph-video-camera-slash"></i></div>`;
}

function tile(cam, opts = {}) {
  const accent = stateAccent(cam.state);
  const motion = cam.motion === true;
  const ago = fmtAgo(cam.last_updated || cam.last_changed);
  const showName = opts.showName !== false;
  const subBits = [];
  if (cam.state) subBits.push(cam.state);
  if (motion) subBits.push("motion");
  const sub = subBits.join(" · ");
  return `
    <div class="cam-tile">
      ${tileImage(cam)}
      ${ago ? `<span class="cam-ts" title="${escapeHtml(cam.last_updated || "")}"><i class="ph-bold ph-clock" style="font-size:.85em"></i>${escapeHtml(ago)}</span>` : ""}
      ${motion ? `<span class="cam-motion-pip"><i class="ph-bold ph-circle-notch"></i>MOTION</span>` : ""}
      <div class="cam-overlay">
        ${showName ? `<span class="cam-name">${escapeHtml(cam.name || "")}</span>` : ""}
        ${sub ? `<span class="cam-sub" style="color:${accent === "var(--text-secondary)" ? "rgba(255,255,255,0.85)" : accent}">${escapeHtml(sub)}</span>` : ""}
      </div>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const fullBleed = opts.full_bleed === true;
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

  const items = Array.isArray(data.items) ? data.items : [];
  const isGrid = items.length > 1;

  const layout = `
    .cam-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: var(--space-2);
      width: 100%;
      flex: 1 1 auto;
      min-height: 0;
    }
    .cam-tile {
      position: relative;
      border-radius: var(--radius-1);
      overflow: hidden;
      background: var(--surface-sunken);
      aspect-ratio: 16 / 9;
      min-height: 0;
    }
    .cam-tile img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .cam-empty {
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-muted);
      font-size: 2em;
    }
    .cam-overlay {
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      padding: var(--space-1) var(--space-2);
      background: linear-gradient(to top, rgba(0, 0, 0, 0.62), rgba(0, 0, 0, 0));
      color: white;
      display: flex;
      flex-direction: column;
      gap: 0;
      line-height: 1.1;
      pointer-events: none;
    }
    .cam-name {
      font-weight: var(--fw-black);
      font-size: var(--fs-caption);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
    }
    .cam-sub {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
    }
    /* Timestamp chip, top-left corner, soft black backdrop so it
       stays legible against any frame. Uses a pure black tint rather
       than --surface so it works on both light + dark themes against
       a real-world camera image. */
    .cam-ts {
      position: absolute;
      top: 6px;
      left: 6px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 2px 6px;
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.55);
      color: white;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      letter-spacing: 0;
      pointer-events: none;
    }
    .cam-motion-pip {
      position: absolute;
      top: 6px;
      right: 6px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 2px 6px;
      border-radius: 999px;
      background: color-mix(in oklab, var(--accent-1) 90%, white 10%);
      color: white;
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
    }
    .cam-motion-pip i {
      font-size: .75em;
    }
  `;

  // Full-bleed mode, only meaningful with a single camera. Falls
  // back to the framed grid if multiple cameras are configured.
  if (fullBleed && !isGrid) {
    const cam = items[0] || data;
    const name = cam.name || data.label || "Camera";
    const state = cam.state || "";
    const motion = cam.motion === true;
    const accent = stateAccent(state);
    const sub = [state, motion ? "motion" : ""].filter(Boolean).join(" · ") || "no signal";
    const ago = fmtAgo(cam.last_updated || cam.last_changed);
    if (cam.image_url) {
      shadow.innerHTML = `
        ${css}
        <style>${layout}</style>
        <div class="w is-bleed" data-widget="ha_camera">
          <img src="${escapeHtml(cam.image_url)}" alt="${escapeHtml(name)}">
          ${ago ? `<span class="cam-ts" title="${escapeHtml(cam.last_updated || "")}"><i class="ph-bold ph-clock" style="font-size:.85em"></i>${escapeHtml(ago)}</span>` : ""}
          ${motion ? `<span class="cam-motion-pip"><i class="ph-bold ph-circle-notch"></i>MOTION</span>` : ""}
          <div class="img-overlay">
            <span class="title">${escapeHtml(name)}</span>
            <span class="sub" style="color:${accent === "var(--text-secondary)" ? "rgba(255,255,255,0.85)" : accent}">${escapeHtml(sub)}</span>
          </div>
        </div>`;
    } else {
      shadow.innerHTML = `
        ${css}
        <div class="w is-bleed" data-widget="ha_camera">
          <div class="bleed-empty">
            <i class="ph-bold ph-video-camera-slash" style="font-size:2em;margin-bottom:0.3em"></i>
            ${escapeHtml(name)}
          </div>
        </div>`;
    }
    return;
  }

  // Multi-camera grid, render every item as a tile with corner
  // timestamp + bottom overlay. The title bar carries an aggregated
  // motion count so the cell announces "2 cameras seeing motion"
  // without you having to scan every tile.
  if (isGrid) {
    const motionCount = items.filter((c) => c.motion === true).length;
    const title = data.label || "Cameras";
    const meta = motionCount > 0
      ? `<span class="w-title-meta" style="color:var(--accent-1)">${motionCount} MOTION</span>`
      : `<span class="w-title-meta">${items.length}</span>`;
    shadow.innerHTML = `
      ${css}
      <style>${layout}</style>
      <div class="w" data-widget="ha_camera">
        <div class="w-title">
          <i class="ph-bold ph-video-camera" style="color:var(--accent-4)"></i>
          <h3>${escapeHtml(title)}</h3>
          ${meta}
        </div>
        <div class="w-body" style="padding:var(--space-2)">
          <div class="cam-grid">${items.map((c) => tile(c, { showName: true })).join("")}</div>
        </div>
      </div>`;
    return;
  }

  // Single-camera framed (non-bleed) view: use the existing img-hero
  // layout but add the corner timestamp + a motion pip so it matches
  // the grid tiles' affordances.
  const cam = items[0] || data;
  const name = cam.name || data.label || "Camera";
  const url = cam.image_url || "";
  const state = cam.state || "";
  const motion = cam.motion === true;
  const accent = stateAccent(state);
  const subBits = [state, motion ? "motion" : ""].filter(Boolean);
  const sub = subBits.length ? subBits.join(" · ") : "no signal";
  const ago = fmtAgo(cam.last_updated || cam.last_changed);

  const heroBody = url
    ? `<img src="${escapeHtml(url)}" alt="${escapeHtml(name)}">`
    : `<i class="ph-bold ph-video-camera-slash"></i>`;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_camera">
      <div class="w-title">
        <i class="ph-bold ph-video-camera" style="color:${accent}"></i>
        <h3>${escapeHtml(name)}</h3>
        ${motion ? `<span class="w-title-meta" style="color:var(--accent-1)">MOTION</span>` : ""}
      </div>
      <div class="w-body img-body">
        <div class="img-hero" style="position:relative">
          ${heroBody}
          ${ago && url ? `<span class="cam-ts" title="${escapeHtml(cam.last_updated || "")}"><i class="ph-bold ph-clock" style="font-size:.85em"></i>${escapeHtml(ago)}</span>` : ""}
          ${motion && url ? `<span class="cam-motion-pip"><i class="ph-bold ph-circle-notch"></i>MOTION</span>` : ""}
        </div>
        <div class="img-meta">
          <span class="sub" style="color:${accent}">${escapeHtml(sub)}</span>
        </div>
      </div>
    </div>`;
}
