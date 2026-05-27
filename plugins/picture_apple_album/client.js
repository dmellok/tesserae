// picture_apple_album — full-bleed iCloud Shared Album rotation.
// Same shape vocabulary as picture_apod / picture_unsplash /
// picture_gallery — single image with optional Bauhaus caption strip.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

function renderEmpty(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/picture_apple_album/client.css">
    <div class="root pa-empty">
      <i class="ph-duotone ph-cloud-arrow-down" aria-hidden="true"></i>
      <div class="pa-empty-msg">${escapeHtml(msg)}</div>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const url = data.url;
  if (!url) {
    shadow.innerHTML = renderEmpty(data.error || "No photo loaded.");
    return;
  }

  const size = ctx.cell.size;
  const showCaption = ctx.cell.options.show_caption !== false;
  const scale = ctx.cell.options.scale || "fit";
  const safeUrl = escapeHtml(url);
  const alt = escapeHtml(data.stream || "iCloud Shared Album photo");

  const body =
    scale === "blurred"
      ? `<img class="pa-bg" src="${safeUrl}" alt="" aria-hidden="true">
         <img class="pa-fg" src="${safeUrl}" alt="${alt}">`
      : `<img class="pa-img" src="${safeUrl}" alt="${alt}">`;

  const parts = [];
  if (data.stream) parts.push(escapeHtml(data.stream));
  const dateText = fmtDate(data.date);
  if (dateText) parts.push(escapeHtml(dateText));
  const caption = showCaption && parts.length
    ? `<div class="pa-caption">
         <span class="pa-mark" aria-hidden="true"></span>
         <i class="ph-bold ph-cloud-arrow-down pa-caption-icon" aria-hidden="true"></i>
         <span class="pa-caption-text">${parts.join(" · ")}</span>
       </div>`
    : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/picture_apple_album/client.css">
    <div class="root size-${size} scale-${scale}">
      ${body}
      ${caption}
    </div>
  `;
}
