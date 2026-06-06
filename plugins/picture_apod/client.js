// picture_apod, Spectra full-bleed image. NASA's Astronomy Picture
// of the Day fills the cell; a subtle bottom-gradient overlay
// surfaces the title + date so a passer-by can read what the picture
// is without crowding the photo.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showCaption = opts.show_caption !== false;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="picture_apod">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>APOD</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="picture_apod">
        <div class="bleed-empty">No picture today.</div>
      </div>`;
    return;
  }

  const subBits = [data.date, data.copyright ? `© ${data.copyright}` : ""].filter(Boolean);

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="picture_apod">
      <img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.title)}">
      ${showCaption && (data.title || subBits.length)
        ? `<div class="img-overlay">
            ${data.title ? `<span class="title">${escapeHtml(data.title)}</span>` : ""}
            ${subBits.length ? `<span class="sub">${escapeHtml(subBits.join(" · "))}</span>` : ""}
          </div>`
        : ""}
    </div>`;
}
