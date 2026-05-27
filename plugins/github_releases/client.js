// github_releases — latest tags across watched repos.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function ago(iso) {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
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
      <link rel="stylesheet" href="/plugins/github_releases/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const releases = Array.isArray(data.releases) ? data.releases : [];
  const rows = releases.map((r) => `
    <div class="gr-row">
      <i class="ph-bold ph-tag gr-icon"></i>
      <span class="gr-repo" title="${escapeHtml(r.repo)}">${escapeHtml(r.repo)}</span>
      <span class="gr-tag">${escapeHtml(r.tag)}${r.prerelease ? ' <span class="gr-pre">pre</span>' : ""}</span>
      <span class="gr-when">${escapeHtml(ago(r.published_at))}</span>
    </div>
  `).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_releases/client.css">
    <div class="root size-${size}">
      <header class="gr-bar">
        <span class="gr-mark" aria-hidden="true"></span>
        <span class="gr-title">Releases</span>
        <i class="ph-bold ph-github-logo gr-bar-icon"></i>
      </header>
      <section class="gr-list">${rows || `<div class="gr-empty">No releases.</div>`}</section>
    </div>
  `;
}
