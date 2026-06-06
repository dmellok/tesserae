// picture_unsplash — Spectra full-bleed image. Photographer credit
// (per Unsplash terms) renders as a chip with a small avatar circle
// in the bottom-left corner; an optional location overlay sits in
// the top-right when the photo's metadata includes one.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function initials(name) {
  if (typeof name !== "string" || !name) return "?";
  const parts = name.split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((p) => p[0]).join("").toUpperCase() || "?";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showCredit = opts.show_credit !== false; // default true — Unsplash terms
  const showLocation = opts.show_location !== false; // default true
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
  const avatarUrl = data.credit_avatar || data.user_profile_image || "";

  let creditChip = "";
  if (showCredit && credit) {
    const avatar = avatarUrl
      ? `<img class="us-credit-avatar" src="${escapeHtml(avatarUrl)}" alt="">`
      : `<span class="us-credit-avatar us-credit-avatar-fb">${escapeHtml(initials(credit))}</span>`;
    creditChip = `
      <div class="us-credit-chip">
        ${avatar}
        <span class="us-credit-name">${escapeHtml(credit)}</span>
        <span class="us-credit-source">Unsplash</span>
      </div>`;
  }

  let locationChip = "";
  if (showLocation && data.location) {
    locationChip = `
      <div class="us-location-chip">
        <i class="ph-bold ph-map-pin"></i>${escapeHtml(data.location)}
      </div>`;
  }

  const layout = `
    .us-credit-chip {
      position: absolute;
      bottom: var(--space-2);
      left: var(--space-2);
      display: inline-flex;
      align-items: center;
      gap: var(--space-1);
      padding: 3px var(--space-2) 3px 3px;
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.55);
      color: white;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      pointer-events: none;
      max-width: calc(100% - var(--space-4));
    }
    .us-credit-avatar {
      width: 1.4em;
      height: 1.4em;
      border-radius: 50%;
      object-fit: cover;
      flex: 0 0 auto;
      background: rgba(255, 255, 255, 0.18);
    }
    .us-credit-avatar-fb {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: .7em;
      font-weight: var(--fw-black);
      color: white;
    }
    .us-credit-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 12em;
    }
    .us-credit-source {
      opacity: 0.65;
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
      font-size: .85em;
    }
    .us-location-chip {
      position: absolute;
      top: var(--space-2);
      right: var(--space-2);
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px var(--space-2);
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.55);
      color: white;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      pointer-events: none;
      max-width: 60%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .us-location-chip i {
      font-size: .9em;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w is-bleed" data-widget="picture_unsplash">
      <img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.alt || "")}">
      ${creditChip}
      ${locationChip}
    </div>`;
}
