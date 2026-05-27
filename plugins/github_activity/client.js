// github_activity — recent activity timeline for one user.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function ago(iso) {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return `${Math.floor(s / 604800)}w`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/github_activity/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const events = Array.isArray(data.events) ? data.events : [];

  // Four-up derived stat strip + a 7-day bar histogram.
  const statStrip = `
    <section class="ga-stats">
      <div class="ga-stat ga-stat--accent">
        <i class="ph-bold ph-git-commit"></i>
        <span class="ga-v">${data.type_commits ?? 0}</span>
        <span class="ga-l">Pushes</span>
      </div>
      <div class="ga-stat ga-stat--accent2">
        <i class="ph-bold ph-git-pull-request"></i>
        <span class="ga-v">${data.type_prs ?? 0}</span>
        <span class="ga-l">PRs</span>
      </div>
      <div class="ga-stat ga-stat--accent3">
        <i class="ph-bold ph-warning-circle"></i>
        <span class="ga-v">${data.type_issues ?? 0}</span>
        <span class="ga-l">Issues</span>
      </div>
      <div class="ga-stat ga-stat--surface">
        <i class="ph-bold ph-stack"></i>
        <span class="ga-v">${data.repos_count ?? 0}</span>
        <span class="ga-l">Repos</span>
      </div>
    </section>
  `;

  const daily = Array.isArray(data.daily) ? data.daily : [];
  const peak = Math.max(1, ...daily);
  const dayLabels = (() => {
    // Build day-of-week letters for the last 7 days ending today.
    const now = new Date();
    const out = [];
    for (let i = 6; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(now.getDate() - i);
      out.push("SMTWTFS"[d.getDay()]);
    }
    return out;
  })();
  const histBars = daily.map((c, i) => {
    const h = c === 0 ? 6 : Math.max(14, Math.min(100, (c / peak) * 100));
    return `
      <div class="ga-dcol">
        <span class="ga-dbar" style="height:${h}%" title="${c} events"></span>
        <span class="ga-dlbl">${dayLabels[i] || ""}</span>
      </div>
    `;
  }).join("");
  const histBlock = daily.length ? `
    <section class="ga-hist">
      <div class="ga-hist-head">
        <span>Last 7 days</span>
        <span class="ga-hist-total"><i class="ph-bold ph-pulse"></i>${daily.reduce((a, b) => a + b, 0)}</span>
      </div>
      <div class="ga-hist-bars">${histBars}</div>
    </section>
  ` : "";

  const rows = events.map((e) => `
    <div class="ga-row">
      <i class="ph-bold ph-${escapeHtml(e.icon)} ga-icon" aria-hidden="true"></i>
      <span class="ga-label">${escapeHtml(e.label)}</span>
      <span class="ga-repo" title="${escapeHtml(e.repo)}">${escapeHtml(e.repo)}</span>
      ${e.detail ? `<span class="ga-detail">${escapeHtml(e.detail)}</span>` : ""}
      <span class="ga-when">${escapeHtml(ago(e.at))}</span>
    </div>
  `).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_activity/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="ga-title">@${escapeHtml(data.user)}</span>
        <i class="ph-bold ph-github-logo wb-bar-icon" aria-hidden="true"></i>
      </header>
      ${statStrip}
      ${histBlock}
      <section class="ga-list">${rows || `<div class="ga-empty">No recent activity.</div>`}</section>
    </div>
  `;
}
