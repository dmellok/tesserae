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
      <link rel="stylesheet" href="/plugins/github_contributions/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const weeks = Array.isArray(data.weeks) ? data.weeks : [];

  const cells = weeks.map((week) => {
    const days = week.map((d) => `
      <span class="gc-cell" data-lvl="${d.level}" title="${escapeHtml(d.date)}: ${d.count}"></span>
    `).join("");
    return `<div class="gc-week">${days}</div>`;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_contributions/client.css">
    <div class="root size-${size}">
      <header class="gc-bar">
        <span class="gc-mark" aria-hidden="true"></span>
        <span class="gc-title">@${escapeHtml(data.user)}</span>
        <span class="gc-total">${data.total} contributions</span>
      </header>
      <section class="gc-grid">${cells}</section>
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
