// github_pr_queue, Spectra list archetype with two sub-sections.
// "Mine" (PRs you authored) leads with an accent-4 git-branch icon
// for ready / a muted ph-pencil-circle for drafts; "Review requested"
// leads with an accent-1 chat-circle. Each row carries a PR-age chip
// (colour-tinted by how long it has been open: fresh → muted, days →
// ochre, weeks → terracotta, months → plum) and a chat-bubble chip
// showing comment activity so a glance reads "old PR with a lot of
// discussion" vs "fresh PR nobody touched".

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

// Age in days from an ISO timestamp to now. Returns NaN for missing
// or malformed values.
function ageDays(iso) {
  if (typeof iso !== "string") return NaN;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return NaN;
  return Math.max(0, (Date.now() - t) / 86_400_000);
}

// Format the age as a compact chip label + pick an accent tier:
// fresh (< 2d) → muted, days (2-7d) → ochre, > 1w → terracotta,
// > 1mo → plum.
function ageTier(days) {
  if (!Number.isFinite(days)) return { label: "-", color: "var(--text-muted)", tint: 0 };
  if (days < 1) return { label: `${Math.max(1, Math.round(days * 24))}h`, color: "var(--text-secondary)", tint: 0 };
  if (days < 2) return { label: `${Math.round(days)}d`, color: "var(--text-secondary)", tint: 0 };
  if (days < 7) return { label: `${Math.round(days)}d`, color: "var(--accent-2)", tint: 14 };
  if (days < 30) return { label: `${Math.round(days / 7)}w`, color: "var(--accent-1)", tint: 16 };
  return { label: `${Math.round(days / 30)}mo`, color: "var(--accent-6)", tint: 18 };
}

function ageChip(iso) {
  const d = ageDays(iso);
  const tier = ageTier(d);
  const bg = tier.tint > 0
    ? `background:color-mix(in oklab, ${tier.color} ${tier.tint}%, var(--surface));`
    : "background:color-mix(in oklab, var(--text-primary) 5%, transparent);";
  return `<span class="pr-chip pr-age" style="color:${tier.color};${bg}" title="opened ${iso || "-"}">
    <i class="ph-bold ph-clock" style="font-size:.9em"></i>${escapeHtml(tier.label)}
  </span>`;
}

function commentChip(n) {
  if (!Number.isFinite(n) || n <= 0) return "";
  return `<span class="pr-chip pr-comments" title="${n} comments">
    <i class="ph-bold ph-chat-circle" style="font-size:.9em"></i>${n}
  </span>`;
}

function sectionHeader(label, count) {
  return `
    <div class="pr-section-head">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="u-label pr-section-count">${count}</span>
    </div>`;
}

function row(item, i, readyIcon, readyColor) {
  // Draft / ready-for-review state: lead glyph swaps to ph-pencil-circle
  // (muted) for drafts; the ready ones get the section's archetype icon.
  const isDraft = !!item.draft;
  const lead = isDraft ? "ph-pencil-circle" : readyIcon;
  const leadColor = isDraft ? "var(--text-muted)" : readyColor;
  const repoMeta = `<span class="pr-repo">${escapeHtml(repoShort(item.repo))}${
    item.number ? `<small class="pr-num">#${item.number}</small>` : ""
  }</span>`;
  const draftTag = isDraft
    ? `<span class="pr-chip pr-draft" title="draft PR">
        <i class="ph-bold ph-pencil-simple" style="font-size:.9em"></i>DRAFT
      </span>`
    : "";
  return `
    <div class="pr-row ${i % 2 ? "is-zebra" : ""} ${isDraft ? "is-draft" : ""}">
      <div class="pr-row-head">
        <div class="list-lead">
          <i class="ph-bold ${lead}" style="color:${leadColor}"></i>
          <span class="list-title">${escapeHtml(item.title)}</span>
        </div>
        ${ageChip(item.created_at)}
      </div>
      <div class="pr-row-meta">
        ${repoMeta}
        ${draftTag}
        ${commentChip(item.comments)}
      </div>
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
        <div class="w-body" style="justify-content:center;align-items:center">
          <i class="ph-bold ph-check-circle" style="color:var(--accent-3);font-size:3em"></i>
          <p class="u-muted">Inbox zero.</p>
        </div>
      </div>`;
    return;
  }

  // Sort each section oldest-first so the most stale PR sits at the
  // top, the one that most needs attention.
  yours.sort((a, b) => Date.parse(a.created_at || 0) - Date.parse(b.created_at || 0));
  review.sort((a, b) => Date.parse(a.created_at || 0) - Date.parse(b.created_at || 0));

  const yoursRows = yours.map((p, i) => row(p, i, "ph-git-branch", "var(--accent-4)")).join("");
  const reviewRows = review.map((p, i) => row(p, i, "ph-chat-circle", "var(--accent-1)")).join("");

  const layout = `
    .pr-section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--space-1) var(--space-3) var(--space-1);
      color: var(--text-muted);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      font-weight: var(--fw-bold);
    }
    .pr-section-count {
      color: var(--text-secondary);
      font-variant-numeric: tabular-nums;
    }
    .pr-row {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
    }
    .pr-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .pr-row.is-draft {
      opacity: 0.78;
    }
    .pr-row-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
      min-width: 0;
    }
    .pr-row-head .list-lead {
      min-width: 0;
      flex: 1 1 auto;
    }
    .pr-row-head .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .pr-row-meta {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      margin-left: calc(1.2em + var(--space-2));
      font-size: var(--fs-caption);
    }
    .pr-repo {
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      font-size: var(--fs-caption);
      flex: 0 0 auto;
    }
    .pr-num {
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      font-size: .85em;
      margin-left: .2em;
    }
    .pr-chip {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
      letter-spacing: 0;
      flex: 0 0 auto;
    }
    .pr-comments {
      color: var(--text-secondary);
      background: color-mix(in oklab, var(--text-primary) 5%, transparent);
    }
    .pr-draft {
      color: var(--text-muted);
      background: color-mix(in oklab, var(--text-primary) 6%, transparent);
      letter-spacing: var(--ls-label);
    }
    @container (max-width: 280px) {
      .pr-comments { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="github_pr_queue">
      <div class="w-title">
        <i class="ph-bold ph-git-pull-request" style="color:var(--accent-1)"></i>
        <h3>PR Queue</h3>
        <span class="w-title-meta">${yours.length} MINE · ${review.length} REVIEW</span>
      </div>
      <div class="w-body list-body" style="gap:0">
        ${yours.length ? sectionHeader("Mine", yours.length) + yoursRows : ""}
        ${review.length ? sectionHeader("Review requested", review.length) + reviewRows : ""}
      </div>
    </div>`;
}
