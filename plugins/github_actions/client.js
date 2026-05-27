// github_actions — last N CI runs across watched repos.

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
  return `${Math.floor(s / 86400)}d`;
}

function status(run) {
  if (run.status === "in_progress" || run.status === "queued" || run.status === "waiting") {
    return { cls: "ga-pill--run",  icon: "circle-notch",   label: "running" };
  }
  if (run.conclusion === "success") return { cls: "ga-pill--ok",   icon: "check-circle",   label: "passed" };
  if (run.conclusion === "failure") return { cls: "ga-pill--fail", icon: "x-circle",       label: "failed" };
  if (run.conclusion === "cancelled") return { cls: "ga-pill--off", icon: "minus-circle", label: "cancelled" };
  if (run.conclusion === "skipped") return { cls: "ga-pill--off", icon: "skip-forward", label: "skipped" };
  return { cls: "ga-pill--off", icon: "circle", label: run.conclusion || run.status || "?" };
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/plugins/github_actions/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const runs = Array.isArray(data.runs) ? data.runs : [];

  const rows = runs.map((r) => {
    const s = status(r);
    return `
      <div class="ga-row">
        <span class="ga-pill ${s.cls}"><i class="ph-bold ph-${s.icon}"></i>${escapeHtml(s.label)}</span>
        <span class="ga-name" title="${escapeHtml(r.name)}">${escapeHtml(r.name)}</span>
        <span class="ga-repo">${escapeHtml(r.repo)}</span>
        <span class="ga-branch"><i class="ph ph-git-branch"></i>${escapeHtml(r.branch)}</span>
        <span class="ga-when">${escapeHtml(ago(r.updated_at))}</span>
      </div>
    `;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_actions/client.css">
    <div class="root size-${size}">
      <header class="ga-bar">
        <span class="ga-mark" aria-hidden="true"></span>
        <span class="ga-title">CI Runs</span>
        <i class="ph-bold ph-play-circle ga-bar-icon"></i>
      </header>
      <section class="ga-list">${rows || `<div class="ga-empty">No recent runs.</div>`}</section>
    </div>
  `;
}
