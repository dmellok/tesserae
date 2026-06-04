// github_contributions — Spectra contributions-heatmap. 53-week ×
// 7-day grid of activity cells (level 0-4 mapped onto a moss-accent
// gradient), with the total + streak / busiest-day stats stacked
// underneath as a small status-grid.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
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

  const grid = [
    ["Streak", `${data.current_streak ?? 0}d`, "var(--accent-3)"],
    ["Longest", `${data.longest_streak ?? 0}d`, "var(--text-secondary)"],
    ["This week", `${data.this_week ?? 0}`, "var(--text-secondary)"],
    ["This month", `${data.this_month ?? 0}`, "var(--text-secondary)"],
  ];
  const gridHtml = grid.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(value)}</span>
    </div>`).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="github_contributions">
      <div class="w-title">
        <i class="ph-bold ph-github-logo" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(user)}</h3>
        <span class="w-title-meta">${escapeHtml(String(data.total ?? 0))} CONTRIBUTIONS</span>
      </div>
      <div class="w-body" style="gap:var(--space-3)">
        <div class="heat">${cells.join("")}</div>
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
