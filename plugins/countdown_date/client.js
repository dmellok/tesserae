// countdown_date, Spectra stat archetype. A glanceable "N days until X"
// hero. Pure client-side: takes a target date from cell options and
// computes the delta against the panel's clock. Size-tiered:
//
//   xs  number only.
//   sm  number + units suffix (days / hours).
//   md  number + suffix + label kicker.
//   lg  number + suffix + label kicker + a subtle progress block
//       showing how close the date is, anchored at the time the
//       widget was first seen on the panel (a stable per-render
//       reference, see anchorBaseline below).

const ACCENTS = {
  terracotta: "var(--accent-1)",
  ochre:      "var(--accent-2)",
  moss:       "var(--accent-3)",
  teal:       "var(--accent-4)",
  slate:      "var(--accent-5)",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Parse a YYYY-MM-DD string at the panel's local midnight so the
// "days remaining" number doesn't flip a day early at the user's
// 22:00 evening glance. Returns null if the option is empty or
// malformed; the renderer falls back to a neutral "set a date"
// placeholder rather than throwing.
function parseTargetDate(s) {
  if (!s) return null;
  const m = String(s).trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]) - 1;
  const day = Number(m[3]);
  const d = new Date(year, month, day, 0, 0, 0, 0);
  if (Number.isNaN(d.getTime())) return null;
  return d;
}

// Whole days between two dates, ignoring time-of-day. Local-midnight
// of both sides so DST transitions don't ever push the count off by
// one. Returns the signed integer (future = positive, past = negative).
function wholeDaysBetween(target, now) {
  const a = new Date(target.getFullYear(), target.getMonth(), target.getDate());
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((a - b) / 86400000);
}

// Hour delta when we're within today's window of the target. Used
// for the "show hours when less than a day" branch. Always rounded
// down so 23h59m reads as 23h, not 24h.
function wholeHoursBetween(target, now) {
  return Math.floor((target - now) / 3600000);
}

function formatBigNumber(n) {
  const abs = Math.abs(n);
  if (abs >= 1000) return abs.toLocaleString();
  return String(abs);
}

function suffixFor(unit, value, isPast) {
  const plural = Math.abs(value) === 1 ? "" : "s";
  if (isPast) return `${unit}${plural} ago`;
  return `${unit}${plural} to go`;
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const target = parseTargetDate(opts.target_date);
  const accent = ACCENTS[opts.accent] || ACCENTS.terracotta;
  const labelText = (opts.label || "").trim();
  const showHoursUnderDay = opts.show_hours_under_a_day !== false;
  const showPast = opts.show_past !== false;
  const now = new Date();

  // Empty / malformed config: render a calm placeholder rather than
  // either nothing or a broken NaN hero. Matches what other widgets
  // do when their data source is unconfigured.
  if (target === null) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/style/spectra-widgets.css">
      <div class="w" data-widget="countdown_date">
        <div class="w-title">
          <i class="ph-bold ph-calendar-blank" style="color:${accent}"></i>
          <h3>Countdown</h3>
        </div>
        <div class="w-body"><p class="u-muted">Set a target date in cell options.</p></div>
      </div>`;
    return;
  }

  const days = wholeDaysBetween(target, now);
  const isPast = days < 0;
  if (isPast && !showPast) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/style/spectra-widgets.css">
      <div class="w" data-widget="countdown_date">
        <div class="w-title">
          <i class="ph-bold ph-calendar-check" style="color:${accent}"></i>
          <h3>${escapeHtml(labelText || "Countdown")}</h3>
        </div>
        <div class="w-body"><p class="u-muted">Date has passed.</p></div>
      </div>`;
    return;
  }

  // Pick the unit to display. The "show hours when |days| < 1"
  // branch only kicks in for the future side; if a date already
  // passed and the user wants past tracking, "X days ago" reads
  // better than "23 hours ago".
  let heroNumber;
  let heroSuffix;
  let kicker;
  if (!isPast && days === 0 && showHoursUnderDay) {
    const hours = Math.max(0, wholeHoursBetween(target, now));
    heroNumber = formatBigNumber(hours);
    heroSuffix = suffixFor("hour", hours, false);
    kicker = "today";
  } else if (!isPast && days === 0) {
    heroNumber = "Today";
    heroSuffix = "";
    kicker = "";
  } else {
    heroNumber = formatBigNumber(days);
    heroSuffix = suffixFor("day", days, isPast);
    kicker = "";
  }

  // Progress strip on the lg layout. Anchors at "first day the user
  // could glance at this widget" by inferring a window of |target -
  // 365d| at maximum so the bar isn't a vanishingly small sliver for
  // a date 3 years away. For past dates we mirror the same window
  // backwards so "ago" still reads against a meaningful track. The
  // strip is decorative; it does not change with time within a
  // single render. The panel refreshes on cadence so the next pull
  // will re-paint it with the new position naturally.
  const windowDays = Math.max(7, Math.min(Math.abs(days) + 30, 365));
  const elapsedDays = isPast
    ? Math.min(windowDays, Math.abs(days))
    : Math.max(0, windowDays - days);
  const pctFilled = Math.round((elapsedDays / windowDays) * 100);
  const pctClamped = Math.max(0, Math.min(100, pctFilled));

  const layout = `
    .w[data-widget="countdown_date"] .w-body {
      justify-content: center;
      align-items: flex-start;
      gap: var(--space-3);
    }
    .cd-kicker {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
      color: var(--text-secondary);
    }
    .cd-hero {
      display: flex;
      align-items: baseline;
      gap: var(--space-3);
      min-width: 0;
    }
    .cd-hero .num {
      font-size: var(--fs-jumbo);
      font-weight: var(--fw-black);
      line-height: var(--lh-tight);
      letter-spacing: var(--ls-tight);
      color: ${accent};
    }
    .cd-hero .suf {
      font-size: var(--fs-body);
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
    }
    .cd-label {
      display: inline-flex;
      align-items: center;
      gap: var(--space-2);
      font-size: var(--fs-lead);
      font-weight: var(--fw-bold);
      color: var(--text-primary);
    }
    .cd-label i {
      font-size: 1.1em;
      color: ${accent};
    }
    .cd-progress {
      width: 100%;
      height: 10px;
      background: color-mix(in oklab, ${accent} 14%, var(--surface));
      border-radius: 999px;
      overflow: hidden;
      position: relative;
    }
    .cd-progress > span {
      display: block;
      width: ${pctClamped}%;
      height: 100%;
      background: ${accent};
      border-radius: inherit;
    }
    .cd-meta {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      font-size: var(--fs-caption);
      color: var(--text-muted);
      font-weight: var(--fw-semi);
    }
    .cd-meta i { font-size: 1em; color: ${accent}; }
    @container w (max-width: 260px) {
      .cd-hero .suf, .cd-label, .cd-progress, .cd-meta { display: none; }
    }
    @container w (min-width: 261px) and (max-width: 360px) {
      .cd-label, .cd-progress, .cd-meta { display: none; }
    }
    @container w (max-width: 460px) {
      .cd-progress, .cd-meta { display: none; }
    }
  `;

  const kickerEl = kicker
    ? `<span class="cd-kicker">${escapeHtml(kicker)}</span>`
    : "";
  const labelEl = labelText
    ? `<div class="cd-label">
         <i class="ph-bold ph-flag"></i>
         <span>${escapeHtml(labelText)}</span>
       </div>`
    : "";
  const heroEl = `
    <div class="cd-hero">
      <span class="num">${escapeHtml(heroNumber)}</span>
      ${heroSuffix ? `<span class="suf">${escapeHtml(heroSuffix)}</span>` : ""}
    </div>`;
  // Friendly target date for the meta line. "Friday, 25 December" reads
  // more naturally than the raw YYYY-MM-DD; we leave the year off when
  // the date is in the current year (the lead-up reads as "this year by
  // default") and tack it on for further-out dates.
  const sameYear = target.getFullYear() === now.getFullYear();
  const targetLabel = target.toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    ...(sameYear ? {} : { year: "numeric" }),
  });
  const progressEl = `
    <div class="cd-progress" aria-hidden="true"><span></span></div>
    <div class="cd-meta">
      <i class="ph-bold ph-calendar"></i>
      <span>${escapeHtml(targetLabel)}</span>
    </div>`;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}</style>
    <div class="w" data-widget="countdown_date">
      <div class="w-body">
        ${kickerEl}
        ${heroEl}
        ${labelEl}
        ${progressEl}
      </div>
    </div>`;
}
