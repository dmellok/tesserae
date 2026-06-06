// github_releases, Spectra list archetype. Each row leads with a
// version-tag icon (accent-2 stable / muted prerelease / accent-1
// draft), the release name + repo as the title, and the tag as
// right-aligned meta. Two server-derived chips per row light up the
// release's character: a SemVer bump-type pill (MAJOR / MINOR / PATCH
// computed by comparing the tag to the previous release) and, for
// the most recent release of each repo, a "+N COMMITS" tail counting
// unreleased commits on the default branch, so a glance reads
// "this dep has 12 commits waiting" without leaving the dashboard.

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

function fmtAgo(iso) {
  if (typeof iso !== "string" || !iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const days = Math.floor((Date.now() - t) / (1000 * 60 * 60 * 24));
  if (days < 1) return "today";
  if (days < 7) return `${days}d`;
  if (days < 30) return `${Math.floor(days / 7)}w`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
}

// SemVer bump-type chip palette. Major bumps are loud (terracotta) so
// they catch the eye on a list of "what's new"; minor sits at ochre;
// patches are quiet text-secondary so they don't compete.
const BUMP_STYLE = {
  major: { label: "MAJOR", color: "var(--accent-1)", tint: 16 },
  minor: { label: "MINOR", color: "var(--accent-2)", tint: 14 },
  patch: { label: "PATCH", color: "var(--text-secondary)", tint: 8 },
};

function bumpChip(bump) {
  const s = BUMP_STYLE[bump];
  if (!s) return "";
  return `<span class="rel-chip rel-bump" style="color:${s.color};background:color-mix(in oklab, ${s.color} ${s.tint}%, var(--surface))">${s.label}</span>`;
}

function commitsTail(n) {
  if (!Number.isFinite(n) || n <= 0) return "";
  // 1 commit "+1 commit", >1 "+N commits". Tabular-nums to keep the
  // chip width steady across rows even when N jumps from 9 to 10.
  return `<span class="rel-chip rel-commits" title="${n} commits on the default branch since this release">
    <i class="ph-bold ph-git-commit" style="font-size:.9em"></i>+${n}
  </span>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_releases">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Releases</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const releases = Array.isArray(data.releases) ? data.releases : [];

  if (releases.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_releases">
        <div class="w-title">
          <i class="ph-bold ph-tag" style="color:var(--accent-2)"></i>
          <h3>Releases</h3>
        </div>
        <div class="w-body"><p class="u-muted">No releases.</p></div>
      </div>`;
    return;
  }

  const rows = releases.map((r, i) => {
    const accent = r.draft ? "var(--accent-1)" : r.prerelease ? "var(--text-muted)" : "var(--accent-2)";
    const ago = fmtAgo(r.published_at);
    const repoBit = `<small class="rel-repo">${escapeHtml(repoShort(r.repo))}</small>`;
    const tagBadge = r.draft
      ? `<span class="rel-chip rel-tag" style="color:var(--accent-1);background:color-mix(in oklab, var(--accent-1) 14%, var(--surface))">DRAFT</span>`
      : r.prerelease
        ? `<span class="rel-chip rel-tag" style="color:var(--text-muted);background:color-mix(in oklab, var(--text-muted) 14%, var(--surface))">PRE</span>`
        : "";
    return `
      <div class="rel-row ${i % 2 ? "is-zebra" : ""}">
        <div class="rel-row-head">
          <div class="list-lead">
            <i class="ph-bold ph-tag" style="color:${accent}"></i>
            <span class="list-title">${escapeHtml(r.name || r.tag)}${repoBit}</span>
          </div>
          <div class="rel-row-meta">
            ${bumpChip(r.bump_type)}
            ${tagBadge}
            <span class="rel-tag-text" style="color:${accent}">${escapeHtml(r.tag)}</span>
            ${ago ? `<small class="rel-ago">${ago}</small>` : ""}
          </div>
        </div>
        ${commitsTail(r.commits_since) ? `<div class="rel-row-foot">${commitsTail(r.commits_since)}</div>` : ""}
      </div>`;
  }).join("");

  const layout = `
    .rel-row {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
    }
    .rel-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .rel-row-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      min-width: 0;
    }
    .rel-row-head .list-lead {
      min-width: 0;
      flex: 1 1 auto;
    }
    .rel-row-head .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .rel-repo {
      font-weight: var(--fw-semi);
      font-size: .7em;
      margin-left: .4em;
      color: var(--text-muted);
    }
    .rel-row-meta {
      display: flex;
      align-items: center;
      gap: var(--space-1);
      flex: 0 0 auto;
    }
    .rel-tag-text {
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      font-size: var(--fs-caption);
    }
    .rel-ago {
      font-size: var(--fs-caption);
      color: var(--text-muted);
      font-weight: var(--fw-semi);
    }
    .rel-row-foot {
      margin-left: calc(1.2em + var(--space-2));
      display: flex;
      align-items: center;
      gap: var(--space-1);
    }
    .rel-chip {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      font-variant-numeric: tabular-nums;
      flex: 0 0 auto;
    }
    .rel-commits {
      color: var(--accent-4);
      background: color-mix(in oklab, var(--accent-4) 14%, var(--surface));
    }
    @container (max-width: 320px) {
      .rel-ago { display: none; }
    }
    @container (max-width: 280px) {
      .rel-bump { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="github_releases">
      <div class="w-title">
        <i class="ph-bold ph-tag" style="color:var(--accent-2)"></i>
        <h3>Releases</h3>
        <span class="w-title-meta">${releases.length}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
