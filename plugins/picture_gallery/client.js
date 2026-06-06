// picture_gallery — Spectra full-bleed image. Pulls a random file
// from a local folder under data/plugins/picture_gallery/. Optional
// folder-name chip + photo count indicator overlay so the cell
// can broadcast "10 photos from /trip-2024" without crowding the
// image.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showFilename = opts.show_filename === true; // default false
  const showFolderChip = opts.show_folder_chip !== false; // default true
  const showCount = opts.show_count !== false; // default true
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

  // Folder chip — the server's `folder` (or last path segment of
  // filename) renders as a small overlay pill so a multi-folder
  // setup can identify which set the current image came from.
  const folderName = data.folder || (data.filename ? String(data.filename).split("/")[0] : "");
  const folderChip = showFolderChip && folderName
    ? `<div class="gallery-folder-chip">
        <i class="ph-bold ph-folder"></i>${escapeHtml(folderName)}
      </div>`
    : "";

  // Photo count indicator (top-right) — when the server reports the
  // total number of images in the folder, the cell quietly broadcasts
  // "1 of 47" so you know roughly how often it cycles.
  let countChip = "";
  if (showCount && Number.isFinite(data.count) && data.count > 1) {
    countChip = `
      <div class="gallery-count">
        <i class="ph-bold ph-images-square"></i>${data.count}
      </div>`;
  }

  const layout = `
    .gallery-folder-chip, .gallery-count {
      position: absolute;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px var(--space-2);
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.55);
      color: white;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      pointer-events: none;
      font-variant-numeric: tabular-nums;
    }
    .gallery-folder-chip {
      top: var(--space-2);
      left: var(--space-2);
      text-transform: uppercase;
      max-width: 60%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .gallery-count {
      top: var(--space-2);
      right: var(--space-2);
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w is-bleed" data-widget="picture_gallery">
      <img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.filename || "")}">
      ${folderChip}
      ${countChip}
      ${showFilename && data.filename
        ? `<div class="img-overlay"><span class="sub">${escapeHtml(data.filename)}</span></div>`
        : ""}
    </div>`;
}
