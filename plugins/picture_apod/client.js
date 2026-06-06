// picture_apod — Spectra full-bleed image. NASA's Astronomy Picture
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

  // Day-of-month badge — chunky number in the top-right corner so
  // the cell quietly broadcasts the date without a full title bar.
  // Parsed from `data.date` which is the API's YYYY-MM-DD string.
  let dayBadge = "";
  if (opts.show_day_badge !== false && typeof data.date === "string") {
    const m = data.date.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) {
      const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
      const dayNum = parseInt(m[3], 10);
      const monthLabel = months[parseInt(m[2], 10) - 1];
      dayBadge = `
        <div class="apod-day-badge">
          <span class="apod-day-num">${dayNum}</span>
          <span class="apod-day-mon">${monthLabel}</span>
        </div>`;
    }
  }

  const layout = `
    .apod-day-badge {
      position: absolute;
      top: var(--space-2);
      right: var(--space-2);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0;
      padding: var(--space-1) var(--space-2);
      border-radius: var(--radius-1);
      background: rgba(0, 0, 0, 0.55);
      color: white;
      line-height: 1;
      pointer-events: none;
    }
    .apod-day-num {
      font-size: var(--fs-headline);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
    }
    .apod-day-mon {
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      margin-top: 2px;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w is-bleed" data-widget="picture_apod">
      <img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.title)}">
      ${dayBadge}
      ${showCaption && (data.title || subBits.length)
        ? `<div class="img-overlay">
            ${data.title ? `<span class="title">${escapeHtml(data.title)}</span>` : ""}
            ${subBits.length ? `<span class="sub">${escapeHtml(subBits.join(" · "))}</span>` : ""}
          </div>`
        : ""}
    </div>`;
}
