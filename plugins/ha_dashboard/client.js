// ha_dashboard, full-bleed frame of an existing Home Assistant dashboard.
// The server does the work (headless Chromium signed in with the ha_core
// token, sidebar + header stripped, capture at the cell's size) and hands
// the client a PNG data URL. All this file does is put that image on the
// cell, or say why there isn't one yet.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;
  const title = t("dashboard", "Dashboard");

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_dashboard">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>${escapeHtml(title)}</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.frame) {
    // First render is still in flight: the server kicked it off and will
    // have a frame by the next refresh. Say so rather than showing blank.
    shadow.innerHTML = `
      ${css}
      <style>
        .pending { width:100%; height:100%; display:flex; flex-direction:column;
                   align-items:center; justify-content:center; gap:var(--space-2);
                   color:var(--text-muted); text-align:center; padding:var(--space-3); box-sizing:border-box; }
        .pending i { font-size:2.4em; }
        .pending .path { font-family:var(--font-mono, monospace); font-size:.85em; }
      </style>
      <div class="pending" data-widget="ha_dashboard">
        <i class="ph-bold ph-layout"></i>
        <span>${escapeHtml(t("rendering", "Rendering dashboard…"))}</span>
        ${data.path ? `<span class="path">${escapeHtml(data.path)}</span>` : ""}
      </div>`;
    return;
  }

  // The frame was captured at the cell's own pixel size, so it fills the
  // cell 1:1. object-fit:contain covers the editor showing a cell at a
  // size the frame was not rendered for (a resize between refreshes).
  shadow.innerHTML = `
    <style>
      .shell { position:relative; width:100%; height:100%; overflow:hidden; background:#fff; }
      .shell > img { position:absolute; inset:0; width:100%; height:100%; display:block; object-fit:contain; }
    </style>
    <div class="shell" data-widget="ha_dashboard">
      <img src="${escapeHtml(data.frame)}" alt="${escapeHtml(data.path || title)}">
    </div>`;
}
