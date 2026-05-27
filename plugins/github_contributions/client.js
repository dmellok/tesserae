// github_contributions — 53-week heatmap. Each cell's --lvl picks
// the colour intensity via CSS.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/github_contributions/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const weeks = Array.isArray(data.weeks) ? data.weeks : [];

  // Month labels — emit the month abbreviation only when this week's
  // first day starts a new month vs the previous week. Column 0 is a
  // partial week from a prior month, so we always skip its label —
  // otherwise "May" + the immediately-following "Jun" overlap.
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const monthLabels = weeks.map((week, i) => {
    if (i === 0) return `<span></span>`;
    if (!week || !week.length || !week[0].date) return `<span></span>`;
    const cur = new Date(week[0].date).getMonth();
    const prev = (weeks[i - 1].length && weeks[i - 1][0].date)
      ? new Date(weeks[i - 1][0].date).getMonth()
      : -1;
    return `<span>${cur !== prev ? MONTHS[cur] : ""}</span>`;
  }).join("");

  const cells = weeks.map((week) => {
    const days = week.map((d) => `
      <span class="gc-cell" data-lvl="${d.level}" title="${escapeHtml(d.date)}: ${d.count}"></span>
    `).join("");
    return `<div class="gc-week">${days}</div>`;
  }).join("");

  // Format busiest date like "Tue 13 Aug"
  let busiest = "—";
  if (data.busiest_date) {
    const bd = new Date(data.busiest_date);
    if (!Number.isNaN(bd.getTime())) {
      busiest = bd.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    }
  }

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_contributions/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="gc-title">@${escapeHtml(data.user)}</span>
        <span class="gc-total"><i class="ph-bold ph-git-commit"></i>${data.total} this year</span>
      </header>
      <section class="gc-stats">
        <div class="gc-stat">
          <i class="ph-bold ph-flame gc-stat-icon"></i>
          <span class="gc-stat-v">${data.current_streak ?? 0}</span>
          <span class="gc-stat-l">Day streak</span>
        </div>
        <div class="gc-stat">
          <i class="ph-bold ph-trophy gc-stat-icon"></i>
          <span class="gc-stat-v">${data.longest_streak ?? 0}</span>
          <span class="gc-stat-l">Longest streak</span>
        </div>
        <div class="gc-stat">
          <i class="ph-bold ph-calendar-check gc-stat-icon"></i>
          <span class="gc-stat-v">${data.this_week ?? 0}</span>
          <span class="gc-stat-l">This week</span>
        </div>
        <div class="gc-stat">
          <i class="ph-bold ph-lightning gc-stat-icon"></i>
          <span class="gc-stat-v">${data.busiest_count ?? 0}</span>
          <span class="gc-stat-l">Busiest · ${escapeHtml(busiest)}</span>
        </div>
      </section>
      <section class="gc-heatmap" style="--weeks:${weeks.length}">
        <div class="gc-months">${monthLabels}</div>
        <div class="gc-grid">${cells}</div>
      </section>
      <footer class="gc-legend">
        <span>Less</span>
        <span class="gc-cell" data-lvl="0"></span>
        <span class="gc-cell" data-lvl="1"></span>
        <span class="gc-cell" data-lvl="2"></span>
        <span class="gc-cell" data-lvl="3"></span>
        <span class="gc-cell" data-lvl="4"></span>
        <span>More</span>
      </footer>
    </div>
  `;
}
