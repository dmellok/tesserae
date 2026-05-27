// picture_unsplash — full-bleed random Unsplash photo with optional
// Bauhaus-style photographer credit strip.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function renderEmpty(msg) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/picture_unsplash/client.css">
    <div class="root pu-empty">
      <i class="ph-duotone ph-camera" aria-hidden="true"></i>
      <div class="pu-empty-msg">${escapeHtml(msg)}</div>
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
  const showCredit = ctx.cell.options.show_credit !== false;
  const scale = ctx.cell.options.scale || "fill";
  const safeUrl = escapeHtml(url);
  const alt = escapeHtml(data.alt || "Unsplash photo");

  const body =
    scale === "blurred"
      ? `<img class="pu-bg" src="${safeUrl}" alt="" aria-hidden="true">
         <img class="pu-fg" src="${safeUrl}" alt="${alt}">`
      : `<img class="pu-img" src="${safeUrl}" alt="${alt}">`;

  const credit = showCredit && data.credit_name
    ? `<div class="pu-credit">
         <span class="pu-mark" aria-hidden="true"></span>
         <i class="ph-bold ph-camera pu-credit-icon" aria-hidden="true"></i>
         <span class="pu-credit-text">${escapeHtml(data.credit_name)} · Unsplash</span>
       </div>`
    : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/picture_unsplash/client.css">
    <div class="root size-${size} scale-${scale}">
      ${body}
      ${credit}
    </div>
  `;
}
