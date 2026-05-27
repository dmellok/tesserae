// picture_gallery — full-bleed image rotator. Server picks an image
// (random or sequential) per render; the client just paints it with
// the chosen scale mode + optional filename caption.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderEmpty(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/picture_gallery/client.css">
    <div class="root pg-empty">
      <i class="ph-duotone ph-images" aria-hidden="true"></i>
      <div class="pg-empty-msg">${escapeHtml(msg)}</div>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const url = data.url;
  if (!url) {
    shadow.innerHTML = renderEmpty(data.error || "No image.");
    return;
  }

  const size = ctx.cell.size;
  const scale = ctx.cell.options.scale || "fit";
  const showFilename = ctx.cell.options.show_filename === true;
  const safeUrl = escapeHtml(url);
  const alt = escapeHtml(data.filename || "Gallery image");

  const body =
    scale === "blurred"
      ? `<img class="pg-bg" src="${safeUrl}" alt="" aria-hidden="true">
         <img class="pg-fg" src="${safeUrl}" alt="${alt}">`
      : `<img class="pg-img" src="${safeUrl}" alt="${alt}">`;

  const caption = showFilename && data.filename
    ? `<div class="pg-caption">
         <span class="pg-mark" aria-hidden="true"></span>
         <i class="ph-bold ph-image pg-caption-icon" aria-hidden="true"></i>
         <span class="pg-caption-text">${escapeHtml(data.filename)}</span>
       </div>`
    : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/picture_gallery/client.css">
    <div class="root size-${size} scale-${scale}">
      ${body}
      ${caption}
    </div>
  `;
}
