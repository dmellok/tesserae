// picture_gallery, Spectra full-bleed image. Pulls a random file
// from a local folder under data/plugins/picture_gallery/. No
// overlay, local photos rarely need captioning.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showFilename = opts.show_filename === true; // default false
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="picture_gallery">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Gallery</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="picture_gallery">
        <div class="bleed-empty">No images.</div>
      </div>`;
    return;
  }

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="picture_gallery">
      <img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.filename || "")}">
      ${showFilename && data.filename
        ? `<div class="img-overlay"><span class="sub">${escapeHtml(data.filename)}</span></div>`
        : ""}
    </div>`;
}
