// github_pr_queue — your open PRs + review queue.

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

function section(label, items, accentClass) {
  if (!items.length) return "";
  const rows = items.map((p) => `
    <div class="pq-row">
      <i class="ph-bold ph-git-pull-request pq-icon${p.draft ? ' pq-icon--draft' : ''}"></i>
      <span class="pq-num">#${escapeHtml(String(p.number))}</span>
      <span class="pq-title" title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</span>
      <span class="pq-repo">${escapeHtml(p.repo)}</span>
      <span class="pq-when">${escapeHtml(ago(p.updated_at))}</span>
    </div>
  `).join("");
  return `
    <div class="pq-section">
      <div class="pq-section-head ${accentClass}">
        <i class="ph-bold ph-${accentClass === 'pq-section-head--review' ? 'eye' : 'user'}"></i>
        <span>${escapeHtml(label)}</span>
        <span class="pq-count">${items.length}</span>
      </div>
      ${rows}
    </div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/plugins/github_pr_queue/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const yours  = Array.isArray(data.yours) ? data.yours : [];
  const review = Array.isArray(data.review) ? data.review : [];

  const body = (yours.length + review.length === 0)
    ? `<div class="pq-empty">Inbox zero. ✨</div>`
    : section("Awaiting review", review, "pq-section-head--review")
      + section("Yours", yours, "pq-section-head--yours");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/github_pr_queue/client.css">
    <div class="root size-${size}">
      <header class="pq-bar">
        <span class="pq-mark" aria-hidden="true"></span>
        <span class="pq-title-bar">PR Queue · @${escapeHtml(data.user)}</span>
        <i class="ph-bold ph-git-pull-request pq-bar-icon"></i>
      </header>
      <section class="pq-body">${body}</section>
    </div>
  `;
}
