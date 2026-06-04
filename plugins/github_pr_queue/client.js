// github_pr_queue — Spectra list archetype with two sub-sections.
// "Yours" (PRs you authored) leads with an accent-4 git-branch icon;
// "Review requested" leads with an accent-1 chat-circle. Each row's
// meta column shows the repo short-name + PR number.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function repoShort(name) {
  if (typeof name !== "string") return "";
  const slash = name.lastIndexOf("/");
  return slash >= 0 ? name.slice(slash + 1) : name;
}

function sectionHeader(label) {
  return `<div class="u-label" style="padding:var(--space-1) var(--space-3) 0;color:var(--text-muted)">${escapeHtml(label)}</div>`;
}

function row(item, i, leadIcon, leadColor) {
  const draftBadge = item.draft ? `<small style="font-size:.65em;color:var(--text-muted);font-weight:var(--fw-bold);margin-left:.4em">DRAFT</small>` : "";
  const meta = `${escapeHtml(repoShort(item.repo))}${item.number ? `<small style="color:var(--text-muted);font-weight:var(--fw-semi);font-size:.7em;margin-left:.3em">#${item.number}</small>` : ""}`;
  return `
    <div class="list-row ${i % 2 ? "is-zebra" : ""}">
      <div class="list-lead">
        <i class="ph-bold ${leadIcon}" style="color:${leadColor}"></i>
        <span class="list-title">${escapeHtml(item.title)}${draftBadge}</span>
      </div>
      <span class="list-meta">${meta}</span>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_pr_queue">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>PR Queue</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const yours = Array.isArray(data.yours) ? data.yours : [];
  const review = Array.isArray(data.review) ? data.review : [];

  if (yours.length === 0 && review.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_pr_queue">
        <div class="w-title">
          <i class="ph-bold ph-git-pull-request" style="color:var(--accent-3)"></i>
          <h3>PR Queue</h3>
        </div>
        <div class="w-body"><p class="u-muted">Inbox zero.</p></div>
      </div>`;
    return;
  }

  const yoursRows = yours.map((p, i) => row(p, i, "ph-git-branch", "var(--accent-4)")).join("");
  const reviewRows = review.map((p, i) => row(p, i, "ph-chat-circle", "var(--accent-1)")).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="github_pr_queue">
      <div class="w-title">
        <i class="ph-bold ph-git-pull-request" style="color:var(--accent-1)"></i>
        <h3>PR Queue</h3>
        <span class="w-title-meta">${yours.length} MINE · ${review.length} REVIEW</span>
      </div>
      <div class="w-body list-body" style="gap:0">
        ${yours.length ? sectionHeader("Mine") + yoursRows : ""}
        ${review.length ? sectionHeader("Review requested") + reviewRows : ""}
      </div>
    </div>`;
}
