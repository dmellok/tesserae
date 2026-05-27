// picture_apod — full-bleed NASA Astronomy Picture of the Day.
//
// Image fills the cell. Optional Bauhaus-style caption strip at the
// bottom (accent2 mark + title + copyright/date) when show_caption is
// on. Scale modes mirror the gallery widget (fit / fill / stretch /
// blurred backdrop).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderEmpty(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/picture_apod/client.css">
    <div class="root pa-empty">
      <i class="ph-duotone ph-planet" aria-hidden="true"></i>
      <div class="pa-empty-msg">${escapeHtml(msg)}</div>
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const url = data.url;
  if (!url) {
    shadow.innerHTML = renderEmpty(data.error || "No APOD image.");
    return;
  }

  const size = ctx.cell.size;
  const showCaption = ctx.cell.options.show_caption !== false;
  const scale = ctx.cell.options.scale || "fit";
  const safeUrl = escapeHtml(url);
  const alt = escapeHtml(data.title || "Astronomy Picture of the Day");

  // Blurred mode layers two images: a zoomed/blurred backdrop and a
  // fit-contain foreground. Every other mode is a single <img> with
  // object-fit driven by the CSS scale class.
  const body =
    scale === "blurred"
      ? `<img class="pa-bg" src="${safeUrl}" alt="" aria-hidden="true">
         <img class="pa-fg" src="${safeUrl}" alt="${alt}">`
      : `<img class="pa-img" src="${safeUrl}" alt="${alt}">`;

  const tagParts = [];
  if (data.title)     tagParts.push(escapeHtml(data.title));
  if (data.copyright) tagParts.push(`© ${escapeHtml(data.copyright)}`);
  else if (data.date) tagParts.push(escapeHtml(data.date));
  const caption = showCaption && tagParts.length
    ? `<div class="pa-caption">
         <span class="pa-mark" aria-hidden="true"></span>
         <span class="pa-caption-text">${tagParts.join(" · ")}</span>
       </div>`
    : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/picture_apod/client.css">
    <div class="root size-${size} scale-${scale}">
      ${body}
      ${caption}
    </div>
  `;
}
