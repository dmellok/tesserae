// picture_apple_album — Spectra full-bleed image. Renders one signed
// Apple Photos shared-album asset filling the cell. No overlay; the
// stream's vibe is the whole point.

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
      <div class="w" data-widget="picture_apple_album">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Album</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="picture_apple_album">
        <div class="bleed-empty">No photos.</div>
      </div>`;
    return;
  }

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="picture_apple_album">
      <img src="${escapeHtml(data.url)}" alt="">
    </div>`;
}
