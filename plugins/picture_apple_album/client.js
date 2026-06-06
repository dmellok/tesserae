// picture_apple_album — Spectra full-bleed image. Renders one signed
// Apple Photos shared-album asset filling the cell. No overlay; the
// stream's vibe is the whole point.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (typeof iso !== "string" || !iso) return "";
  // Apple gives ISO "2024-05-12T13:45:00.123Z" — slice to date only.
  return iso.slice(0, 10);
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showCaption = opts.show_caption === true; // default false — pictures are the point
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

  const captionBits = [];
  if (showCaption) {
    if (data.date) captionBits.push(fmtDate(data.date));
    if (data.owner) captionBits.push(data.owner);
    if (data.count) captionBits.push(`${data.count} photos`);
  }

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="picture_apple_album">
      <img src="${escapeHtml(data.url)}" alt="">
      ${captionBits.length
        ? `<div class="img-overlay"><span class="sub">${escapeHtml(captionBits.join(" · "))}</span></div>`
        : ""}
    </div>`;
}
