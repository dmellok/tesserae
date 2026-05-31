// github_activity — four visual directions for a user's recent activity
// (A1–A4). State→colour maps to the theme's categorical --c-data-* and
// --c-accent tokens (no ok/warn/danger — these are categorical hues, not
// semantic status), so themes restyle cleanly.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Accent → CSS variable. Categorical, NOT semantic — these intentionally
// avoid --c-ok / --c-warn / --c-danger so the GitHub widgets don't
// pretend "passed CI" and "active reading" mean the same thing.
const ACCENT = {
  green: "var(--c-data-2)",
  red:   "var(--c-accent)",
  yellow:"var(--c-data-3)",
  blue:  "var(--c-data-4)",
  ink:   "var(--c-text)",
  muted: "var(--c-text-soft)",
};
function accent(name) { return ACCENT[name] || ACCENT.muted; }
// Text colour on a filled chip — keep darker accents legible.
function onAccent(name) {
  return (name === "yellow" || name === "muted") ? "var(--c-text)" : "var(--c-bg)";
}

// Map server's GitHub event icon (Phosphor name) + type to a label and
// a categorical accent for variants that want one.
const EVENT_ACCENT = {
  PushEvent: "green",
  PullRequestEvent: "blue",
  IssuesEvent: "yellow",
  IssueCommentEvent: "muted",
  ReleaseEvent: "red",
  WatchEvent: "ink",
};
const TYPE_LABEL = {
  "git-commit": "PUSHED",
  "git-pull-request": "MERGED",
  "warning-circle": "OPENED",
  "chat-circle": "COMMENT",
  "tag": "RELEASED",
  "star": "STARRED",
  "git-fork": "FORKED",
  "plus-circle": "CREATED",
  "eye": "REVIEW",
};
function eventTypeLabel(e) {
  return TYPE_LABEL[e.icon] || (e.label || "").toUpperCase();
}
function eventAccent(e) {
  // Derive from icon since the server already classified the event.
  const map = {
    "git-commit": "green",
    "git-pull-request": "blue",
    "warning-circle": "yellow",
    "tag": "red",
    "star": "ink",
    "git-fork": "muted",
  };
  return map[e.icon] || "muted";
}

// Derive stat tiles from the server's denormalised counts.
function statTiles(data) {
  return [
    { label: "Pushes", value: String(data.type_commits || 0), icon: "git-commit",         accent: "green"  },
    { label: "PRs",    value: String(data.type_prs || 0),     icon: "git-pull-request",   accent: "blue"   },
    { label: "Issues", value: String(data.type_issues || 0),  icon: "warning-circle",     accent: "yellow" },
    { label: "Repos",  value: String(data.repos_count || 0),  icon: "book-bookmark",      accent: "ink"    },
  ];
}

function dailyBars(daily) {
  const max = Math.max(1, ...(daily || [0]));
  return { max, values: daily || [], days: ["7d", "6d", "5d", "4d", "3d", "2d", "1d"] };
}

// Format ISO timestamp → "29m" / "1h" / "4d"
function ago(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const secs = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 86400 * 30) return `${Math.floor(secs / 86400)}d`;
  return `${Math.floor(secs / (86400 * 30))}mo`;
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

function weekBars(week, color) {
  const cells = week.values.map((v, i) => `
    <div class="ga-bar-col">
      <div class="ga-bar" style="height:${(v / week.max) * 100}%; background:${color}"></div>
      <span class="ga-bar-label">${escapeHtml(week.days[i] || "")}</span>
    </div>
  `).join("");
  return `<div class="ga-bars">${cells}</div>`;
}

function eventsList(data, max) {
  return (data.events || []).slice(0, max).map((e) => {
    const a = eventAccent(e);
    return `
      <div class="ga-event">
        <i class="ph ph-${escapeHtml(e.icon)} ga-event-icon" style="color:${accent(a)}"></i>
        <span class="ga-event-type" style="color:${accent(a)}">${escapeHtml(eventTypeLabel(e))}</span>
        <span class="ga-event-repo">${escapeHtml(e.repo)}</span>
        <span class="ga-event-detail">${escapeHtml(e.detail)}</span>
        <span class="ga-event-ago">${escapeHtml(ago(e.at))}</span>
      </div>
    `;
  }).join("");
}

// ===========================================================
// A1 — REFINED
// ===========================================================
function renderA1(data) {
  const stats = statTiles(data);
  const week = dailyBars(data.daily);
  const tiles = stats.map((s) => `
    <div class="a1-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <i class="ph-bold ph-${s.icon} a1-stat-icon"></i>
      <div class="a1-stat-text">
        <div class="a1-stat-value">${escapeHtml(s.value)}</div>
        <div class="a1-stat-label">${s.label.toUpperCase()}</div>
      </div>
    </div>
  `).join("");
  return `
    <div class="variant variant-a1">
      ${darkHeader(`@${data.user}`, "green", `<i class="ph ph-git-branch" aria-hidden="true"></i> ${data.total_30 || 0} EVENTS`)}
      <section class="a1-stats">${tiles}</section>
      <section class="a1-week">
        <span class="a1-week-label">LAST 7 DAYS</span>
        ${weekBars(week, accent("green"))}
      </section>
      <section class="a1-events">${eventsList(data, 6)}</section>
    </div>
  `;
}

// ===========================================================
// A2 — GEOMETRIC
// ===========================================================
function renderA2(data) {
  const stats = statTiles(data);
  const week = dailyBars(data.daily);
  const tiles = stats.map((s) => `
    <div class="a2-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <div class="a2-stat-head">
        <span class="a2-stat-label">${s.label.toUpperCase()}</span>
        <i class="ph-bold ph-${s.icon}"></i>
      </div>
      <div class="a2-stat-value">${escapeHtml(s.value)}</div>
    </div>
  `).join("");
  const rows = (data.events || []).slice(0, 5).map((e) => {
    const a = eventAccent(e);
    return `
      <div class="a2-event">
        <span class="a2-event-block" style="background:${accent(a)}; color:${onAccent(a)}"><i class="ph-bold ph-${escapeHtml(e.icon)}"></i></span>
        <span class="a2-event-repo">${escapeHtml(e.repo)}</span>
        <span class="a2-event-detail">${escapeHtml(e.detail)}</span>
        <span class="a2-event-ago">${escapeHtml(ago(e.at))}</span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-a2">
      <section class="a2-stats">${tiles}</section>
      <section class="a2-body">
        <aside class="a2-week" style="background:${accent("yellow")}; color:${onAccent("yellow")}">
          <span class="a2-week-label">LAST 7 DAYS</span>
          ${weekBars(week, "var(--c-text)")}
        </aside>
        <div class="a2-events">${rows}</div>
      </section>
    </div>
  `;
}

// ===========================================================
// A3 — SWISS
// ===========================================================
function renderA3(data) {
  const stats = statTiles(data);
  const week = dailyBars(data.daily);
  const tiles = stats.map((s) => `
    <div class="a3-stat">
      <span class="a3-dot" style="background:${accent(s.accent)}"></span>
      <span class="a3-stat-value">${escapeHtml(s.value)}</span>
      <span class="a3-stat-label">${escapeHtml(s.label)}</span>
    </div>
  `).join("");
  const rows = (data.events || []).slice(0, 6).map((e) => {
    const a = eventAccent(e);
    return `
      <div class="a3-event">
        <span class="a3-event-dot" style="background:${accent(a)}"></span>
        <span class="a3-event-type">${escapeHtml(eventTypeLabel(e))}</span>
        <span class="a3-event-repo">${escapeHtml(e.repo)}</span>
        <span class="a3-event-detail">${escapeHtml(e.detail)}</span>
        <span class="a3-event-ago">${escapeHtml(ago(e.at))}</span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-a3">
      <div class="a3-eyebrow">
        <span>@${escapeHtml(data.user || "")}</span>
        <span>${data.total_30 || 0} EVENTS THIS MONTH</span>
      </div>
      <div class="a3-rule"></div>
      <section class="a3-stats">${tiles}</section>
      <section class="a3-week">
        <span class="a3-week-label">Last 7 days</span>
        ${weekBars(week, "var(--c-text)")}
      </section>
      <section class="a3-events">${rows}</section>
    </div>
  `;
}

// ===========================================================
// A4 — DATA
// ===========================================================
function renderA4(data) {
  const stats = statTiles(data);
  const week = dailyBars(data.daily);
  const tiles = stats.map((s) => `
    <div class="a4-stat" style="border-color:${accent(s.accent)}">
      <i class="ph ph-${s.icon}" style="color:${accent(s.accent)}"></i>
      <span class="a4-stat-value">${escapeHtml(s.value)}</span>
      <span class="a4-stat-label">${s.label.toUpperCase()}</span>
    </div>
  `).join("");
  const cols = week.values.map((v, i) => `
    <div class="a4-bar-col">
      <span class="a4-bar-num">${v}</span>
      <div class="a4-bar-shaft">
        <div class="a4-bar-fill" style="height:${(v / week.max) * 100}%; background:${accent("green")}"></div>
      </div>
      <span class="a4-bar-day">${escapeHtml(week.days[i] || "")}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-a4">
      <header class="a4-header">
        <span class="a4-title">@${escapeHtml(data.user || "")} · ACTIVITY</span>
        <span class="a4-meta">${data.total_30 || 0} / 30D</span>
      </header>
      <section class="a4-stats">${tiles}</section>
      <div class="a4-bars-head">
        <span>COMMITS · LAST 7 DAYS</span><span>peak ${week.max}</span>
      </div>
      <div class="a4-bars">${cols}</div>
    </div>
  `;
}

const VARIANTS = { a1: renderA1, a2: renderA2, a3: renderA3, a4: renderA4 };

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/plugins/github_activity/client.css">`;

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="root error">
        <i class="ph ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "a1";
  const renderer = VARIANTS[variant] || renderA1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data)}
    </div>`;
}
