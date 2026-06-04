// webpage — Spectra full-bleed iframe. The cell hosts an external
// URL inside a sandboxed iframe; ``scale`` shrinks the page's logical
// pixels so a desktop layout fits in a small cell, and a fixed
// ``viewport_w`` keeps responsive sites from collapsing to a mobile
// breakpoint. The composer's headless render screenshots whatever
// the iframe lands on.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const url = String(opts.url || "").trim();
  const scaleOpt = String(opts.scale || "fit");
  const viewportW = Math.max(200, Math.min(4096, Number(opts.viewport_w) || 1280));
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (!url || !url.startsWith("http")) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="webpage">
        <div class="w-title"><i class="ph-bold ph-globe" style="color:var(--text-muted)"></i><h3>Webpage</h3></div>
        <div class="w-body"><p class="u-muted">Set a URL in the cell options (must start with http/https).</p></div>
      </div>`;
    return;
  }

  // Compute a target scale. "fit" sizes the iframe to viewport_w
  // logical pixels and lets CSS scale it down to fit the cell;
  // numeric options (25/50/75/100) are pinned.
  const cellW = Number(ctx?.cell?.w) || 1;
  const scale = scaleOpt === "fit"
    ? Math.min(1, cellW / viewportW)
    : Math.max(0.1, Math.min(1, Number(scaleOpt) / 100));
  const sizedW = `${(100 / scale).toFixed(2)}%`;
  const sizedH = `${(100 / scale).toFixed(2)}%`;

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="webpage">
      <iframe
        src="${escapeHtml(url)}"
        sandbox="allow-same-origin allow-scripts"
        style="border:0;width:${sizedW};height:${sizedH};transform-origin:top left;transform:scale(${scale.toFixed(3)});display:block"
        loading="eager"
        referrerpolicy="no-referrer"></iframe>
    </div>`;
}
