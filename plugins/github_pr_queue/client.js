// github_pr_queue — four visual directions for the PR queue (PR1–PR4).
// Stats use categorical --c-* tokens (no ok/warn/danger).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const ACCENT = {
  green: "var(--c-data-2)",
  red:   "var(--c-accent)",
  yellow:"var(--c-data-3)",
  blue:  "var(--c-data-4)",
  ink:   "var(--c-text)",
  muted: "var(--c-text-soft)",
};
function accent(n) { return ACCENT[n] || ACCENT.muted; }
function onAccent(n) { return (n === "yellow" || n === "muted") ? "var(--c-text)" : "var(--c-bg)"; }

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function ageDays(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return (Date.now() - d.getTime()) / 86400000;
}
function fmtAge(days) {
  if (days == null) return "—";
  if (days < 1) return `${Math.max(1, Math.floor(days * 24))}h`;
  if (days < 30) return `${Math.floor(days)}d`;
  return `${Math.floor(days / 30)}mo`;
}

function deriveStats(yours, review) {
  const all = [...yours, ...review];
  const stale = all.filter((p) => {
    const t = new Date(p.updated_at || "");
    return !Number.isNaN(t.getTime()) && Date.now() - t.getTime() > WEEK_MS;
  }).length;
  let oldestDays = null;
  for (const p of all) {
    const a = ageDays(p.updated_at);
    if (a != null && (oldestDays == null || a > oldestDays)) oldestDays = a;
  }
  return [
    { label: "To review",  value: String(review.length), icon: "eye",            accent: "red"    },
    { label: "Yours",      value: String(yours.length),  icon: "user",           accent: "green"  },
    { label: "Stale >1w",  value: String(stale),         icon: "warning",        accent: "yellow" },
    { label: "Oldest",     value: fmtAge(oldestDays),    icon: "clock",          accent: "ink"    },
  ];
}

function darkHeader(title, accentName, right) {
  return `
    <header class="gh-dark">
      <span class="gh-dark-chip" style="background:${accent(accentName)}"></span>
      <span class="gh-dark-title">${escapeHtml(title)}</span>
      ${right ? `<span class="gh-dark-meta">${right}</span>` : ""}
    </header>
  `;
}

function prRows(prs, max = 4) {
  return prs.slice(0, max).map((p) => `
    <div class="pq-row">
      <i class="ph ph-git-pull-request pq-row-icon"></i>
      <span class="pq-row-title">${escapeHtml(p.title || "—")}</span>
      <span class="pq-row-repo">${escapeHtml(p.repo || "")}</span>
      <span class="pq-row-age">${escapeHtml(fmtAge(ageDays(p.updated_at)))}</span>
    </div>
  `).join("");
}

// ===========================================================
// PR1 — REFINED
// ===========================================================
function renderPR1(data, yours, review) {
  const stats = deriveStats(yours, review);
  const clear = yours.length === 0 && review.length === 0;
  const tiles = stats.map((s) => `
    <div class="pr1-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <i class="ph-bold ph-${s.icon}"></i>
      <div class="pr1-stat-text">
        <div class="pr1-stat-value">${escapeHtml(s.value)}</div>
        <div class="pr1-stat-label">${s.label.toUpperCase()}</div>
      </div>
    </div>
  `).join("");
  return `
    <div class="variant variant-pr1">
      ${darkHeader(`PR QUEUE · @${(data.user || "").toUpperCase()}`, "red", `<i class="ph ph-git-pull-request"></i>`)}
      <section class="pr1-stats">${tiles}</section>
      ${clear ? `
        <section class="pr1-clear">
          <i class="ph ph-check"></i>
          <span>ALL CLEAR — nothing awaits your review</span>
        </section>
      ` : `
        <section class="pr1-body">
          <div class="pr1-list-head">REVIEW (${review.length})</div>
          <div class="pr1-list">${prRows(review)}</div>
          <div class="pr1-list-head">YOURS (${yours.length})</div>
          <div class="pr1-list">${prRows(yours)}</div>
        </section>
      `}
    </div>
  `;
}

// ===========================================================
// PR2 — GEOMETRIC
// ===========================================================
function renderPR2(data, yours, review) {
  const stats = deriveStats(yours, review);
  const clear = yours.length === 0 && review.length === 0;
  const tiles = stats.map((s) => `
    <div class="pr2-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <div class="pr2-stat-head">
        <span class="pr2-stat-label">${s.label.toUpperCase()}</span>
        <i class="ph-bold ph-${s.icon}"></i>
      </div>
      <div class="pr2-stat-value">${escapeHtml(s.value)}</div>
    </div>
  `).join("");
  return `
    <div class="variant variant-pr2">
      <header class="pr2-header">
        <span class="pr2-chip" style="background:${accent("red")}"></span>
        <span class="pr2-title">PR QUEUE · @${escapeHtml((data.user || "").toUpperCase())}</span>
        <span class="pr2-tag">
          <i class="ph ph-check" style="color:${accent("green")}"></i>
          ${clear ? "ALL CLEAR" : `${yours.length + review.length} ACTIVE`}
        </span>
      </header>
      <section class="pr2-stats">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// PR3 — SWISS
// ===========================================================
function renderPR3(data, yours, review) {
  const stats = deriveStats(yours, review);
  const clear = yours.length === 0 && review.length === 0;
  const tiles = stats.map((s) => `
    <div class="pr3-stat">
      <span class="pr3-num-row">
        <span class="pr3-dot" style="background:${accent(s.accent)}"></span>
        <span class="pr3-stat-value">${escapeHtml(s.value)}</span>
      </span>
      <span class="pr3-stat-label">${escapeHtml(s.label)}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-pr3">
      <div class="pr3-eyebrow">
        <span>PR Queue · @${escapeHtml(data.user || "")}</span>
        <span>
          <i class="ph ph-check" style="color:${accent("green")}"></i>
          ${clear ? "all clear" : `${yours.length + review.length} active`}
        </span>
      </div>
      <div class="pr3-rule"></div>
      <section class="pr3-stats">${tiles}</section>
    </div>
  `;
}

// ===========================================================
// PR4 — DATA
// ===========================================================
function renderPR4(data, yours, review) {
  const stats = deriveStats(yours, review);
  const clear = yours.length === 0 && review.length === 0;
  const tiles = stats.map((s) => `
    <div class="pr4-stat" style="border-color:${accent(s.accent)}">
      <div class="pr4-stat-head">
        <i class="ph ph-${s.icon}" style="color:${accent(s.accent)}"></i>
        <span class="pr4-stat-label">${s.label.toUpperCase()}</span>
      </div>
      <div class="pr4-stat-value">${escapeHtml(s.value)}</div>
    </div>
  `).join("");
  return `
    <div class="variant variant-pr4">
      <header class="pr4-header">
        <span class="pr4-title">PR QUEUE · @${escapeHtml((data.user || "").toUpperCase())}</span>
        <span class="pr4-meta" style="color:${accent("green")}">
          <i class="ph ph-check"></i>
          ${clear ? "INBOX ZERO" : `${yours.length + review.length} ACTIVE`}
        </span>
      </header>
      <section class="pr4-stats">${tiles}</section>
    </div>
  `;
}

const VARIANTS = { pr1: renderPR1, pr2: renderPR2, pr3: renderPR3, pr4: renderPR4 };

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/plugins/github_pr_queue/client.css">`;

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="root error">
        <i class="ph ph-warning-circle"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  const yours = Array.isArray(data.yours) ? data.yours : [];
  const review = Array.isArray(data.review) ? data.review : [];
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "pr1";
  const renderer = VARIANTS[variant] || renderPR1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, yours, review)}
    </div>`;
}
