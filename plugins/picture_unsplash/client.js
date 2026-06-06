// picture_unsplash, Spectra full-bleed image with a small photographer-
// credit overlay (Unsplash terms ask for attribution).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showCredit = opts.show_credit !== false; // default true, Unsplash terms
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="picture_unsplash">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Unsplash</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="picture_unsplash">
        <div class="bleed-empty">No photos.</div>
      </div>`;
    return;
  }

  const credit = data.credit_name || "";

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed" data-widget="picture_unsplash">
      <img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.alt || "")}">
      ${showCredit && credit
        ? `<div class="img-overlay">
            <span class="sub">${escapeHtml(credit)} · UNSPLASH</span>
          </div>`
        : ""}
    </div>`;
}
