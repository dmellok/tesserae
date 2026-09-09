// picture_gallery, Spectra full-bleed image. Pulls a random file
// from a local folder under data/plugins/picture_gallery/. No
// overlay, local photos rarely need captioning.
//
// The Scale option maps onto object-fit (issue #296). The shared
// .is-bleed rule defaults the <img> to cover, which is "Fill"; every
// other mode is set inline here so the dropdown actually does something.
// "Fit with blurred background" paints a blurred, cover-scaled copy of
// the same image behind the contained one.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const OBJECT_FIT = {
  fit: "contain",
  fill: "cover",
  stretch: "fill",
  center: "none",
  blurred: "contain",
};

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showFilename = opts.show_filename === true; // default false
  const scale = OBJECT_FIT[String(opts.scale || "fit")] ? String(opts.scale || "fit") : "fit";
  const objectFit = OBJECT_FIT[scale];
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

  const src = escapeHtml(data.url);
  const alt = escapeHtml(data.filename || "");
  const backdrop = scale === "blurred"
    ? `<img src="${src}" alt="" aria-hidden="true" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;filter:blur(18px);transform:scale(1.1);">`
    : "";
  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="picture_gallery" data-scale="${scale}" style="overflow:hidden;">
      ${backdrop}
      <img src="${src}" alt="${alt}" style="position:relative;object-fit:${objectFit};">
      ${showFilename && data.filename
        ? `<div class="img-overlay"><span class="sub">${escapeHtml(data.filename)}</span></div>`
        : ""}
    </div>`;
}
