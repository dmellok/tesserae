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

// Colour-tier a PR by how long it's been sitting. Anything over a
// week reads as stale (accent3); fresh PRs under 2 days read as
// healthy (accent). The pill class drives the colour in client.css.
function ageTier(iso) {
  if (!iso) return "stale";
  const days = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (days < 2)  return "fresh";
  if (days < 7)  return "warming";
  if (days < 14) return "stale";
  return "ancient";
}

function section(label, items, accentClass) {
  if (!items.length) return "";
  const rows = items.map((p) => {
    const tier = ageTier(p.updated_at);
    const comments = p.comments || 0;
    return `
      <div class="pq-row pq-row--${tier}">
        <i class="ph-bold ph-git-pull-request pq-icon${p.draft ? ' pq-icon--draft' : ''}"></i>
        <span class="pq-num">#${escapeHtml(String(p.number))}</span>
        <span class="pq-title" title="${escapeHtml(p.title)}">${escapeHtml(p.title)}</span>
        <span class="pq-repo">${escapeHtml(p.repo)}</span>
        ${comments > 0 ? `<span class="pq-com" title="${comments} comments"><i class="ph-bold ph-chat-circle"></i>${comments}</span>` : ""}
        ${p.draft ? `<span class="pq-pill pq-pill--draft"><i class="ph ph-circle-dashed"></i>Draft</span>` : ""}
        <span class="pq-age pq-age--${tier}"><i class="ph-bold ph-clock"></i>${escapeHtml(ago(p.updated_at))}</span>
      </div>
    `;
  }).join("");
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

  // Stat strip — counts + oldest PR in either queue so the user
  // sees the "stalest" thing they own at a glance.
  const all = [...review, ...yours];
  const stale = all.filter((p) => {
    const t = ageTier(p.updated_at);
    return t === "stale" || t === "ancient";
  }).length;
  let oldestAge = "—";
  if (all.length) {
    const oldest = all.reduce((a, b) =>
      new Date(a.updated_at) < new Date(b.updated_at) ? a : b);
    oldestAge = ago(oldest.updated_at);
  }

  const statStrip = `
    <section class="pq-stats">
      <div class="pq-stat pq-stat--accent2">
        <i class="ph-bold ph-eye"></i>
        <span class="pq-v">${review.length}</span>
        <span class="pq-l">To review</span>
      </div>
      <div class="pq-stat pq-stat--accent">
        <i class="ph-bold ph-user"></i>
        <span class="pq-v">${yours.length}</span>
        <span class="pq-l">Yours</span>
      </div>
      <div class="pq-stat pq-stat--accent3">
        <i class="ph-bold ph-warning"></i>
        <span class="pq-v">${stale}</span>
        <span class="pq-l">Stale (>1w)</span>
      </div>
      <div class="pq-stat pq-stat--surface">
        <i class="ph-bold ph-clock"></i>
        <span class="pq-v">${escapeHtml(oldestAge)}</span>
        <span class="pq-l">Oldest</span>
      </div>
    </section>
  `;

  const body = (yours.length + review.length === 0)
    ? `<div class="pq-empty"><i class="ph-duotone ph-confetti"></i>Inbox zero.</div>`
    : section("Awaiting review", review, "pq-section-head--review")
      + section("Yours", yours, "pq-section-head--yours");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/github_pr_queue/client.css">
    <div class="root size-${size}">
      <header class="pq-bar">
        <span class="pq-mark" aria-hidden="true"></span>
        <span class="pq-title-bar">PR Queue · @${escapeHtml(data.user)}</span>
        <i class="ph-bold ph-git-pull-request pq-bar-icon"></i>
      </header>
      ${statStrip}
      <section class="pq-body">${body}</section>
    </div>
  `;
}
