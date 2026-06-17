// year_progress, Spectra stat archetype. A calm "how far through"
// hero, with two modes:
//
//   year        the current calendar year as a row of 52 weekly dots
//               (filled = past, empty = future). Hero shows the
//               percentage through the year.
//   life-weeks  same dot grammar applied to a whole life. 52 cols ×
//               life_expectancy_years rows, past weeks filled. Hero
//               shows the percentage through expected lifespan. The
//               row is calendar-year aligned so the boundaries read
//               left-to-right naturally.
//
// Both modes degrade by size:
//
//   xs  percentage only.
//   sm  percentage + a thin linear progress bar.
//   md  percentage + dot row (year) or condensed life summary.
//   lg  percentage + full dot grid + supporting meta.
//
// The dot grid is rendered as a single SVG to keep the DOM cheap and
// the panel render fast even for 4000-week life grids.

const ACCENTS = {
  terracotta: "var(--accent-1)",
  ochre:      "var(--accent-2)",
  moss:       "var(--accent-3)",
  teal:       "var(--accent-4)",
  slate:      "var(--accent-5)",
};

const WEEKS_PER_YEAR = 52;

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Day-of-year position [0..365/366]. Inclusive at Jan 1, so on Jan 1
// the value is 0 and the year is 0% gone, on Dec 31 the value is
// 364/365 and the year is essentially complete.
function dayOfYear(d) {
  const start = new Date(d.getFullYear(), 0, 1);
  return Math.floor((d - start) / 86400000);
}

function daysInYear(year) {
  return ((year % 4 === 0 && year % 100 !== 0) || year % 400 === 0) ? 366 : 365;
}

function weekOfYearIndex(d) {
  // 0-based week index for the dot grid. Floor div by 7 keeps the
  // grid stable across leap years (52 dots, last one absorbs the
  // extra day or two), which reads more honestly than rotating
  // dots in and out of the row each year.
  return Math.min(WEEKS_PER_YEAR - 1, Math.floor(dayOfYear(d) / 7));
}

// Build an inline SVG dot grid. ``cols`` × ``rows`` cells, each
// cell a small circle. Filled circles use the accent token, empty
// ones use a soft surface-mix so the row reads as a single shape
// not a checkerboard. SVG is sized to fill its container; the
// caller controls how much vertical room it takes via CSS.
function dotGrid(cols, rows, filledCount, accent) {
  const cellPx = 8;
  const gapPx = 3;
  const dotR = 2.4;
  const w = cols * cellPx + (cols - 1) * gapPx;
  const h = rows * cellPx + (rows - 1) * gapPx;
  const filledFill = accent;
  const emptyFill = `color-mix(in oklab, ${accent} 22%, var(--surface))`;
  let dots = "";
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const idx = r * cols + c;
      const cx = c * (cellPx + gapPx) + cellPx / 2;
      const cy = r * (cellPx + gapPx) + cellPx / 2;
      const fill = idx < filledCount ? filledFill : emptyFill;
      dots += `<circle cx="${cx}" cy="${cy}" r="${dotR}" fill="${fill}"/>`;
    }
  }
  return `
    <svg class="yp-grid" viewBox="0 0 ${w} ${h}"
         preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      ${dots}
    </svg>`;
}

function formatPct(n) {
  const r = Math.round(n * 10) / 10;
  return (Math.abs(r % 1) < 0.05 ? Math.round(r) : r).toString();
}

export default function render(shadow, ctx) {
  const opts = ctx?.cell?.options || {};
  const mode = opts.mode === "life-weeks" ? "life-weeks" : "year";
  const accent = ACCENTS[opts.accent] || ACCENTS.terracotta;
  const showPct = opts.show_percentage !== false;
  const now = new Date();
  const year = now.getFullYear();

  // Common shapes both modes fill in below.
  let pct;
  let kicker;
  let metaLeft;
  let metaRight;
  let gridCols = WEEKS_PER_YEAR;
  let gridRows = 1;
  let filled = 0;

  if (mode === "year") {
    const doy = dayOfYear(now);
    const total = daysInYear(year);
    pct = (doy / total) * 100;
    filled = weekOfYearIndex(now);
    kicker = String(year);
    metaLeft = `Week ${filled + 1} of ${WEEKS_PER_YEAR}`;
    metaRight = `${total - doy} days left`;
  } else {
    const birthYearRaw = Number(opts.birth_year);
    const lifeYears = Math.max(1, Math.min(120, Number(opts.life_expectancy_years) || 80));
    if (!Number.isFinite(birthYearRaw) || birthYearRaw < 1900 || birthYearRaw > year) {
      shadow.innerHTML = `
        <link rel="stylesheet" href="/static/style/spectra-widgets.css">
        <div class="w" data-widget="year_progress">
          <div class="w-title">
            <i class="ph-bold ph-calendar-dots" style="color:${accent}"></i>
            <h3>Life in weeks</h3>
          </div>
          <div class="w-body"><p class="u-muted">Set a birth year in cell options.</p></div>
        </div>`;
      return;
    }
    const totalWeeks = lifeYears * WEEKS_PER_YEAR;
    // Past weeks = (full years since birth × 52) + this year's week index.
    const fullYearsLived = Math.max(0, year - birthYearRaw);
    const weeksThroughThisYear = weekOfYearIndex(now);
    const pastWeeks = Math.min(totalWeeks, fullYearsLived * WEEKS_PER_YEAR + weeksThroughThisYear);
    pct = (pastWeeks / totalWeeks) * 100;
    filled = pastWeeks;
    gridCols = WEEKS_PER_YEAR;
    gridRows = lifeYears;
    kicker = `Year ${fullYearsLived + 1} of ${lifeYears}`;
    metaLeft = `${pastWeeks.toLocaleString()} weeks lived`;
    metaRight = `${(totalWeeks - pastWeeks).toLocaleString()} weeks to go`;
  }

  const pctText = `${formatPct(pct)}%`;
  const grid = dotGrid(gridCols, gridRows, filled, accent);

  // Linear progress strip for the sm/md layouts where the SVG grid
  // would dominate. Stays visible on lg too as a quick visual anchor
  // above the grid.
  const linearBar = `
    <div class="yp-bar" aria-hidden="true">
      <span style="width:${Math.max(0, Math.min(100, pct)).toFixed(1)}%"></span>
    </div>`;

  const layout = `
    .w[data-widget="year_progress"] .w-body {
      justify-content: center;
      align-items: stretch;
      gap: var(--space-3);
    }
    .yp-kicker {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
      color: var(--text-secondary);
    }
    .yp-hero {
      display: flex;
      align-items: baseline;
      gap: var(--space-3);
      min-width: 0;
    }
    .yp-hero .num {
      font-size: var(--fs-jumbo);
      font-weight: var(--fw-black);
      line-height: var(--lh-tight);
      letter-spacing: var(--ls-tight);
      color: ${accent};
    }
    .yp-hero .suf {
      font-size: var(--fs-body);
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
    }
    .yp-bar {
      width: 100%;
      height: 10px;
      background: color-mix(in oklab, ${accent} 14%, var(--surface));
      border-radius: 999px;
      overflow: hidden;
    }
    .yp-bar > span {
      display: block;
      height: 100%;
      background: ${accent};
      border-radius: inherit;
    }
    .yp-grid-wrap {
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 0;
      overflow: hidden;
    }
    .yp-grid {
      width: 100%;
      max-height: 100%;
    }
    .yp-meta {
      display: flex;
      justify-content: space-between;
      font-size: var(--fs-caption);
      color: var(--text-muted);
      font-weight: var(--fw-semi);
    }
    @container (max-width: 260px) {
      .yp-bar, .yp-grid-wrap, .yp-meta { display: none; }
    }
    @container (min-width: 261px) and (max-width: 360px) {
      .yp-grid-wrap, .yp-meta { display: none; }
    }
    @container (max-width: 460px) {
      .yp-meta { display: none; }
    }
  `;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}</style>
    <div class="w" data-widget="year_progress">
      <div class="w-body">
        <span class="yp-kicker">${escapeHtml(kicker)}</span>
        <div class="yp-hero">
          ${showPct ? `<span class="num">${escapeHtml(pctText)}</span>` : ""}
          <span class="suf">${mode === "year" ? "through the year" : "of expected life"}</span>
        </div>
        ${linearBar}
        <div class="yp-grid-wrap">${grid}</div>
        <div class="yp-meta">
          <span>${escapeHtml(metaLeft)}</span>
          <span>${escapeHtml(metaRight)}</span>
        </div>
      </div>
    </div>`;
}
