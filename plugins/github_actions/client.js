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
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/github_actions/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const runs = Array.isArray(data.runs) ? data.runs : [];

  // Derived stats from the visible window — success rate, run counts
  // by outcome, currently-running flag.
  const total = runs.length;
  const passed = runs.filter((r) => r.conclusion === "success").length;
  const failed = runs.filter((r) => r.conclusion === "failure").length;
  const running = runs.filter((r) => r.status === "in_progress" || r.status === "queued").length;
  const successRate = total > 0 ? Math.round((passed / total) * 100) : 0;

  // Health tier — drives the colour of the gauge ring.
  let healthClass = "gauge--ok";
  if (running > 0) healthClass = "gauge--run";
  else if (total === 0) healthClass = "gauge--idle";
  else if (failed > total / 3) healthClass = "gauge--fail";
  else if (failed > 0) healthClass = "gauge--mixed";

  // Render a circular gauge as a conic-gradient — much cheaper than
  // SVG and looks crisp at any size.
  const ringDeg = successRate * 3.6;

  const statBlock = total > 0 ? `
    <section class="ga-summary">
      <div class="ga-gauge ${healthClass}" style="--ring:${ringDeg}deg">
        <span class="ga-gauge-v">${successRate}<small>%</small></span>
        <span class="ga-gauge-l">Success</span>
      </div>
      <div class="ga-summary-stats">
        <div class="ga-summary-stat ga-summary-stat--ok"><i class="ph-bold ph-check-circle"></i><span>${passed}</span><small>Pass</small></div>
        <div class="ga-summary-stat ga-summary-stat--fail"><i class="ph-bold ph-x-circle"></i><span>${failed}</span><small>Fail</small></div>
        <div class="ga-summary-stat ga-summary-stat--run"><i class="ph-bold ph-circle-notch"></i><span>${running}</span><small>Live</small></div>
        <div class="ga-summary-stat ga-summary-stat--total"><i class="ph-bold ph-list-numbers"></i><span>${total}</span><small>Runs</small></div>
      </div>
    </section>
  ` : "";

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
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_actions/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="ga-title">CI Runs</span>
        <i class="ph-bold ph-play-circle wb-bar-icon"></i>
      </header>
      ${statBlock}
      <section class="ga-list">${rows || `<div class="ga-empty">No recent runs.</div>`}</section>
    </div>
  `;
}
