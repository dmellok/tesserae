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
      <link rel="stylesheet" href="/plugins/github_activity/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const events = Array.isArray(data.events) ? data.events : [];

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
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_activity/client.css">
    <div class="root size-${size}">
      <header class="ga-bar">
        <span class="ga-mark" aria-hidden="true"></span>
        <span class="ga-title">@${escapeHtml(data.user)}</span>
        <i class="ph-bold ph-github-logo ga-bar-icon" aria-hidden="true"></i>
      </header>
      <section class="ga-list">${rows || `<div class="ga-empty">No recent activity.</div>`}</section>
    </div>
  `;
}
