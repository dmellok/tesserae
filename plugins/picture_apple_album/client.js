// picture_apple_album — Spectra full-bleed image. Renders one
// signed Apple Photos shared-album asset filling the cell. Optional
// caption surfaces date / owner / total count; a sequence indicator
// (3/12) sits in the top-right corner when the server reports the
// asset's index within the album.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (typeof iso !== "string" || !iso) return "";
  return iso.slice(0, 10);
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showCaption = opts.show_caption === true; // default false — pictures are the point
  const showSequence = opts.show_sequence !== false; // default true
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
    if (data.location) captionBits.push(data.location);
  }

  // Sequence indicator — "3 / 12" pill in the top-right corner when
  // the server reports asset index + total count. 1-indexed for the
  // display.
  let sequence = "";
  if (showSequence && Number.isFinite(data.index) && Number.isFinite(data.count) && data.count > 1) {
    const oneBased = data.index + 1;
    sequence = `
      <div class="apple-seq">
        <span class="apple-seq-num">${oneBased}</span>
        <span class="apple-seq-sep">/</span>
        <span class="apple-seq-total">${data.count}</span>
      </div>`;
  }

  const layout = `
    .apple-seq {
      position: absolute;
      top: var(--space-2);
      right: var(--space-2);
      display: inline-flex;
      align-items: baseline;
      gap: 2px;
      padding: 3px var(--space-2);
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.55);
      color: white;
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      letter-spacing: var(--ls-label);
      font-size: var(--fs-caption);
      pointer-events: none;
    }
    .apple-seq-sep, .apple-seq-total {
      opacity: 0.65;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w is-bleed" data-widget="picture_apple_album">
      <img src="${escapeHtml(data.url)}" alt="">
      ${sequence}
      ${captionBits.length
        ? `<div class="img-overlay"><span class="sub">${escapeHtml(captionBits.join(" · "))}</span></div>`
        : ""}
    </div>`;
}
