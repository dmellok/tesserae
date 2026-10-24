// picture_natgeo, Spectra image archetype. National Geographic's Photo
// of the Day fills the cell edge to edge (the manifest's full_bleed),
// with the day's date as a solid --accent-1 block: "today's" photo is
// the "now" role. No gradient scrim and no white-on-photo text, a solid
// token block reads on any picture and quantises cleanly.
//
//   xs  photo + date block over its top-left corner.
//   sm  same; the caption won't fit beside a readable photo.
//   md  photo over a slim caption band: date block, caption (2 lines),
//       and an "Image from <show>" chip on --surface-sunken.
//   lg  same band; its type grows with the cell up to a cap, so the
//       photo keeps most of the height.
//
// The show_caption option drops the band at md/lg, leaving the sm look.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// "2026-10-24" -> { day: "24", month: "Oct" } in the render locale. Parsed
// as a local date so the day never slips across a timezone boundary.
function dateParts(iso, locale) {
  const [y, m, d] = String(iso || "").split("-").map(Number);
  if (!Number.isFinite(y) || !Number.isFinite(m) || !Number.isFinite(d)) return null;
  const when = new Date(y, m - 1, d);
  return {
    day: new Intl.DateTimeFormat(locale, { day: "numeric" }).format(when),
    month: new Intl.DateTimeFormat(locale, { month: "short" }).format(when),
  };
}

const LAYOUT = `
  .ng-body {
    position: relative;
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto;
    gap: 0;
    height: 100%;
  }
  .ng-body .img-hero { border-radius: 0; }

  .ng-date {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: var(--space-2) var(--space-3);
    background: var(--accent-1);
    color: var(--on-accent);
    line-height: var(--lh-tight);
    font-variant-numeric: tabular-nums;
  }
  .ng-date .day {
    font-size: var(--fs-value);
    font-weight: var(--fw-black);
  }
  .ng-date .month {
    font-size: var(--fs-label);
    font-weight: var(--fw-bold);
    letter-spacing: var(--ls-label);
    text-transform: var(--label-transform, uppercase);
  }
  /* Over the photo: pinned to the corner, a touch smaller. */
  .ng-date.is-pinned {
    position: absolute;
    top: 0;
    left: 0;
    font-size: clamp(11px, 7cqmin, 22px);
  }

  /* The band keeps to a slim strip so the photo gets the cell. It sets
     its own, smaller type base rather than inheriting the widget's
     (which tops out at 28px on an md cell); every em below scales off
     it, so the date block, caption and chip shrink together. */
  .ng-band {
    display: flex;
    align-items: stretch;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    background: var(--surface);
    min-width: 0;
    font-size: clamp(12px, 4cqmin, 20px);
  }
  .ng-band .ng-date { padding: var(--space-1) var(--space-3); }
  .ng-band .ng-date .day { font-size: var(--fs-lead); }
  .ng-band .img-meta {
    flex: 1 1 auto;
    min-width: 0;
    justify-content: center;
    gap: var(--space-1);
  }
  .ng-caption {
    font-size: var(--fs-body);
    font-weight: var(--fw-semi);
    line-height: var(--lh-snug);
    color: var(--text-primary);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .ng-chip {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    gap: 0.4em;
    max-width: 100%;
    padding: 0.2em 0.6em;
    border-radius: var(--pill-radius, 0);
    background: var(--surface-sunken);
    color: var(--text-secondary);
    font-size: var(--fs-caption);
    font-weight: var(--fw-bold);
    letter-spacing: var(--ls-label);
    text-transform: var(--label-transform, uppercase);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .ng-chip .ph-bold { color: var(--accent-3); flex: 0 0 auto; }

  /* A tall cell (portrait md) has room under a cropped photo for more. */
  @container (min-height: 600px) {
    .ng-caption { -webkit-line-clamp: 3; }
  }

  /* Empty state: one big bold icon on a soft accent block. */
  .ng-empty {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    background: var(--accent-3-soft);
    color: var(--text-secondary);
    font-size: var(--fs-lead);
    font-weight: var(--fw-bold);
    text-align: center;
    padding: var(--pad);
  }
  .ng-empty .ph-bold { font-size: clamp(48px, 30cqmin, 144px); color: var(--accent-3); }
`;

export default function render(shadow, ctx) {
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const size = ctx?.cell?.size || "md";
  const locale = ctx?.locale || "en";
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css"><style>${LAYOUT}</style>`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="picture_natgeo">
        <div class="w-title"><i class="ph-bold ph-warning-circle" style="color:var(--accent-1)"></i><h3>${escapeHtml(t("photo_of_the_day", "Photo of the Day"))}</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="picture_natgeo">
        <div class="ng-empty">
          <i class="ph-bold ph-camera" aria-hidden="true"></i>
          <span>${escapeHtml(t("no_photo_today", "No photo today."))}</span>
        </div>
      </div>`;
    return;
  }

  const parts = dateParts(data.date, locale);
  const showBand = (size === "md" || size === "lg") && opts.show_caption !== false
    && Boolean(data.caption || data.source);

  const dateBlock = parts
    ? `<div class="ng-date${showBand ? "" : " is-pinned"}">
         <span class="day">${escapeHtml(parts.day)}</span>
         <span class="month">${escapeHtml(parts.month)}</span>
       </div>`
    : "";

  const chip = data.source
    ? `<span class="ng-chip"><i class="ph-bold ph-camera" aria-hidden="true"></i>${escapeHtml(t("image_from", "Image from"))} ${escapeHtml(data.source)}</span>`
    : "";

  const band = showBand
    ? `<div class="ng-band">
         ${dateBlock}
         <div class="img-meta">
           ${data.caption ? `<div class="ng-caption">${escapeHtml(data.caption)}</div>` : ""}
           ${chip}
         </div>
       </div>`
    : "";

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed size-${escapeHtml(size)}" data-widget="picture_natgeo">
      <div class="w-body img-body ng-body">
        <div class="img-hero"><img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.caption || t("photo_of_the_day", "Photo of the Day"))}"></div>
        ${band}
        ${showBand ? "" : dateBlock}
      </div>
    </div>`;
}
