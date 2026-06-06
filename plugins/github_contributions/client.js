// github_contributions, Spectra contributions-heatmap. 53-week ×
// 7-day grid of activity cells (level 0-4 mapped onto a moss-accent
// gradient). Above the heatmap sits a paired streak hero (current +
// longest, each with its own glyph + accent); below the heatmap a
// 12-month summary strip shows the year's monthly totals as scaled
// bars. The bottom status-grid carries the remaining stats.

const MONTH_SHORT = [
  "J", "F", "M", "A", "M", "J",
  "J", "A", "S", "O", "N", "D",
];

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Bucket the heatmap days by calendar month (YYYY-MM) and return the
// last 12 months chronologically, each {month: "JAN", count: N}. Even
// for accounts with sparse activity we always emit 12 cells so the
// strip width stays consistent across users.
function monthlyTotals(weeks) {
  const byMonth = new Map();
  for (const week of weeks) {
    for (const day of week || []) {
      if (!day?.date) continue;
      const ym = day.date.slice(0, 7);
      byMonth.set(ym, (byMonth.get(ym) || 0) + (day.count || 0));
    }
  }
  const ordered = [...byMonth.entries()].sort(([a], [b]) => a.localeCompare(b));
  const last12 = ordered.slice(-12);
  return last12.map(([ym, count]) => {
    const monthIdx = parseInt(ym.slice(5, 7), 10) - 1;
    return {
      ym,
      label: MONTH_SHORT[monthIdx] || "?",
      count,
    };
  });
}

function monthlyStrip(months, currentMonthYm) {
  if (!months.length) return "";
  const max = Math.max(1, ...months.map((m) => m.count));
  const cells = months.map((m) => {
    const pct = Math.max(8, (m.count / max) * 100);
    const isCurrent = m.ym === currentMonthYm;
    const fill = isCurrent ? "var(--accent-3)" : "color-mix(in oklab, var(--accent-3) 55%, var(--surface-sunken))";
    return `
      <div class="month-cell" title="${escapeHtml(m.ym)}: ${m.count} contributions">
        <div class="month-bar-track">
          <div class="month-bar" style="height:${pct.toFixed(0)}%;background:${fill}"></div>
        </div>
        <div class="month-label ${isCurrent ? "is-current" : ""}">${escapeHtml(m.label)}</div>
      </div>`;
  }).join("");
  return `<div class="month-strip">${cells}</div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_contributions">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Contributions</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const user = data.user || "GitHub";
  const weeks = Array.isArray(data.weeks) ? data.weeks : [];

  // Flatten weeks into a column-flowed grid. Each week is one column;
  // each day is a row 0-6.
  const cells = [];
  for (const week of weeks) {
    for (let d = 0; d < 7; d++) {
      const day = week[d];
      if (!day) {
        cells.push(`<div class="heat-cell"></div>`);
        continue;
      }
      const level = Math.max(0, Math.min(4, day.level || 0));
      const cls = level > 0 ? `heat-cell l${level}` : "heat-cell";
      cells.push(`<div class="${cls}" title="${escapeHtml(day.date || "")} · ${day.count || 0}"></div>`);
    }
  }

  if (weeks.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_contributions">
        <div class="w-title">
          <i class="ph-bold ph-github-logo" style="color:var(--accent-3)"></i>
          <h3>${escapeHtml(user)}</h3>
        </div>
        <div class="w-body"><p class="u-muted">No contribution data.</p></div>
      </div>`;
    return;
  }

  const current = data.current_streak ?? 0;
  const longest = data.longest_streak ?? 0;
  const months = monthlyTotals(weeks);
  const todayYm = new Date().toISOString().slice(0, 7);

  // Status grid drops Streak / Longest (they got promoted to the
  // hero chips above) in favour of Busiest Day + Active Days.
  const activeDays = weeks.reduce(
    (acc, w) => acc + (w || []).filter((d) => (d?.count || 0) > 0).length,
    0,
  );
  const busiestDate = data.busiest_date ? data.busiest_date.slice(5) : "-";
  const grid = [
    ["This week", `${data.this_week ?? 0}`, "var(--accent-3)"],
    ["This month", `${data.this_month ?? 0}`, "var(--accent-3)"],
    ["Busiest day", `${data.busiest_count ?? 0}`, "var(--accent-2)", busiestDate],
    ["Active days", `${activeDays}`, "var(--text-secondary)"],
  ];
  const gridHtml = grid.map(([label, value, c, sub]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(String(value))}</span>
      ${sub ? `<span class="u-label" style="opacity:.7">${escapeHtml(sub)}</span>` : ""}
    </div>`).join("");

  const layout = `
    /* Paired streak hero row above the heatmap. Each chip is a soft
       accent-tinted card with a chunky glyph, big number, and label;
       sits side-by-side so the year's two main streak stats read in
       one scan. Wraps to stacked on cramped widths. */
    .contrib-streaks {
      display: flex;
      gap: var(--space-3);
      flex-wrap: wrap;
    }
    .streak-chip {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      flex: 1 1 0;
      min-width: 7em;
    }
    .streak-chip.current {
      background: color-mix(in oklab, var(--accent-1) 12%, var(--surface));
    }
    .streak-chip.longest {
      background: color-mix(in oklab, var(--accent-2) 12%, var(--surface));
    }
    .streak-chip i {
      font-size: 1.6em;
      flex: 0 0 auto;
    }
    .streak-text {
      display: flex;
      flex-direction: column;
      gap: 0;
      line-height: 1.05;
    }
    .streak-value {
      font-size: var(--fs-headline);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
    }
    .streak-unit {
      font-size: .55em;
      font-weight: var(--fw-bold);
      color: var(--text-secondary);
      margin-left: .1em;
    }
    .streak-label {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      color: var(--text-muted);
    }
    /* 12-month summary strip beneath the heatmap. Each cell paints a
       vertical bar scaled to the month's contribution count vs the
       year's max; the current calendar month gets the full accent-3,
       past months a tinted surface-sunken so the recent month pops. */
    .month-strip {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 3px;
      height: 32px;
    }
    .month-cell {
      display: flex;
      flex-direction: column;
      align-items: stretch;
      gap: 2px;
      min-width: 0;
    }
    .month-bar-track {
      flex: 1 1 auto;
      display: flex;
      align-items: flex-end;
      min-height: 0;
      background: color-mix(in oklab, var(--text-primary) 4%, var(--surface));
      border-radius: 2px;
      overflow: hidden;
    }
    .month-bar {
      width: 100%;
      border-radius: 2px;
    }
    .month-label {
      font-size: 9px;
      font-weight: var(--fw-bold);
      letter-spacing: 0;
      text-align: center;
      color: var(--text-muted);
      font-family: var(--font-family);
    }
    .month-label.is-current {
      color: var(--text-primary);
    }
    /* xs / sm: drop the streak labels + tighten the chip padding so
       two chips fit a narrow cell. */
    @container (max-width: 320px) {
      .streak-label { display: none; }
      .streak-chip { padding: var(--space-1) var(--space-2); }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="github_contributions">
      <div class="w-title">
        <i class="ph-bold ph-github-logo" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(user)}</h3>
        <span class="w-title-meta">${escapeHtml(String(data.total ?? 0))} CONTRIBUTIONS</span>
      </div>
      <div class="w-body" style="gap:var(--space-3)">
        <div class="contrib-streaks">
          <div class="streak-chip current">
            <i class="ph-bold ph-flame" style="color:var(--accent-1)"></i>
            <div class="streak-text">
              <span class="streak-value">${current}<span class="streak-unit">d</span></span>
              <span class="streak-label">Current streak</span>
            </div>
          </div>
          <div class="streak-chip longest">
            <i class="ph-bold ph-trophy" style="color:var(--accent-2)"></i>
            <div class="streak-text">
              <span class="streak-value">${longest}<span class="streak-unit">d</span></span>
              <span class="streak-label">Longest streak</span>
            </div>
          </div>
        </div>
        <div class="heat">${cells.join("")}</div>
        ${monthlyStrip(months, todayYm)}
        <div class="heat-legend">
          Less <div class="heat-cell"></div>
          <div class="heat-cell l1"></div>
          <div class="heat-cell l2"></div>
          <div class="heat-cell l3"></div>
          <div class="heat-cell l4"></div>
          More
        </div>
        <div class="status-grid">${gridHtml}</div>
      </div>
    </div>`;
}
