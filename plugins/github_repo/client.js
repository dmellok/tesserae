// github_repo — single repo at a glance.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function compact(n) {
  if (n == null) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function ago(iso) {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/plugins/github_repo/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const [owner, name] = (data.repo || "/").split("/");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_repo/client.css">
    <div class="root size-${size}">
      <header class="gr-bar">
        <span class="gr-mark" aria-hidden="true"></span>
        <span class="gr-title">${escapeHtml(owner)}<span class="gr-slash">/</span>${escapeHtml(name)}</span>
        <i class="ph-bold ph-github-logo gr-bar-icon"></i>
      </header>
      <section class="gr-body">
        ${data.description ? `<p class="gr-desc">${escapeHtml(data.description)}</p>` : ""}
        <div class="gr-meta">
          ${data.language ? `<span class="gr-tag"><span class="gr-dot"></span>${escapeHtml(data.language)}</span>` : ""}
          ${data.license ? `<span class="gr-tag"><i class="ph ph-scales"></i>${escapeHtml(data.license)}</span>` : ""}
          ${data.is_archived ? `<span class="gr-tag gr-tag--warn"><i class="ph ph-archive"></i>archived</span>` : ""}
        </div>
      </section>
      <section class="gr-stats">
        <div class="gr-stat gr-stat--accent"><i class="ph-bold ph-star"></i><span class="gr-v">${compact(data.stars)}</span><span class="gr-l">Stars</span></div>
        <div class="gr-stat gr-stat--surface"><i class="ph-bold ph-git-fork"></i><span class="gr-v">${compact(data.forks)}</span><span class="gr-l">Forks</span></div>
        <div class="gr-stat gr-stat--accent2"><i class="ph-bold ph-warning-circle"></i><span class="gr-v">${compact(data.issues)}</span><span class="gr-l">Issues</span></div>
        <div class="gr-stat gr-stat--accent3"><i class="ph-bold ph-tag"></i><span class="gr-v">${escapeHtml(data.latest_release || "—")}</span><span class="gr-l">Release</span></div>
      </section>
      <footer class="gr-foot">
        <i class="ph ph-clock-clockwise"></i>last push ${escapeHtml(ago(data.pushed_at))} · ${escapeHtml(data.default_branch)}
      </footer>
    </div>
  `;
}
