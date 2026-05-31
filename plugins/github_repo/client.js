// github_repo — four visual directions for a single-repo card (RE1–RE4).
// Stats + language chips use categorical --c-* tokens (no ok/warn/danger).

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
const PALETTE = ["green", "red", "yellow", "blue", "ink", "muted"];
function accent(n) { return ACCENT[n] || ACCENT.muted; }
function onAccent(n) { return (n === "yellow" || n === "muted") ? "var(--c-text)" : "var(--c-bg)"; }

function fmtCount(n) {
  if (n == null) return "—";
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function ago(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return `${Math.floor(s / (86400 * 30))}mo ago`;
}

function statTiles(data) {
  return [
    { label: "Stars",   value: fmtCount(data.stars),                       icon: "star",           accent: "green" },
    { label: "Forks",   value: fmtCount(data.forks),                       icon: "git-fork",       accent: "blue"  },
    { label: "Issues",  value: fmtCount(data.issues),                      icon: "warning-circle", accent: "red"   },
    { label: "Release", value: data.latest_release || "—",                 icon: "tag",            accent: "ink"   },
  ];
}

// Languages from the server don't carry an accent field; assign by index.
function langs(data) {
  return (data.languages || []).map((l, i) => ({
    ...l,
    accent: PALETTE[i] || "muted",
  }));
}

function langBar(languages) {
  const cells = languages.map((l) => `
    <div style="flex-grow:${Math.max(l.pct, 0.6)}; flex-basis:0; min-width:4px; background:${accent(l.accent)}"></div>
  `).join("");
  return `<div class="re-langbar">${cells}</div>`;
}

function langLegend(languages) {
  return `
    <div class="re-langlegend">
      ${languages.map((l) => `
        <span class="re-lang-item">
          <span class="re-lang-dot" style="background:${accent(l.accent)}"></span>
          ${escapeHtml(l.name)} ${l.pct}%
        </span>
      `).join("")}
    </div>
  `;
}

function activityBars(values, color) {
  if (!values || !values.length) return `<div class="re-bars-empty">No commit activity</div>`;
  const max = Math.max(1, ...values);
  return `
    <div class="re-bars">
      ${values.map((v) => `<div class="re-bar" style="height:${(v / max) * 100}%; background:${color}"></div>`).join("")}
    </div>
  `;
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

// ===========================================================
// RE1 — REFINED
// ===========================================================
function renderRE1(data) {
  const ls = langs(data);
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="re1-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <i class="ph-bold ph-${s.icon}"></i>
      <div class="re1-stat-text">
        <div class="re1-stat-value">${escapeHtml(s.value)}</div>
        <div class="re1-stat-label">${s.label.toUpperCase()}</div>
      </div>
    </div>
  `).join("");
  return `
    <div class="variant variant-re1">
      ${darkHeader((data.repo || "").toUpperCase(), "green", `<i class="ph ph-git-branch"></i>`)}
      <section class="re1-meta">
        <div class="re1-meta-row">
          <span class="re1-desc">${escapeHtml(data.description || "")}</span>
          <span class="re1-langlic">
            <span class="re1-langlic-item"><span class="re1-lang-dot" style="background:${accent("green")}"></span>${escapeHtml(data.language || "")}</span>
            <span class="re1-langlic-sep">${escapeHtml(data.license || "—")}</span>
          </span>
        </div>
        ${langBar(ls)}
        ${langLegend(ls)}
      </section>
      <section class="re1-stats">${tiles}</section>
      <section class="re1-activity">
        <div class="re1-activity-head">
          <span>52-WEEK COMMIT ACTIVITY</span>
          <span>${fmtCount(data.commits_year)}</span>
        </div>
        ${activityBars(data.commit_weeks, accent("green"))}
        <div class="re1-activity-foot">
          <i class="ph ph-clock"></i>
          LAST PUSH ${escapeHtml(ago(data.pushed_at))} · ${escapeHtml((data.default_branch || "").toUpperCase())}
        </div>
      </section>
    </div>
  `;
}

// ===========================================================
// RE2 — GEOMETRIC
// ===========================================================
function renderRE2(data) {
  const ls = langs(data);
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="re2-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <span class="re2-stat-value">${escapeHtml(s.value)}</span>
      <span class="re2-stat-label">${s.label.toUpperCase()}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-re2">
      <header class="re2-header" style="background:${accent("green")}; color:${onAccent("green")}">
        <span class="re2-title">${escapeHtml((data.repo || "").toUpperCase())}</span>
        <span class="re2-meta">${escapeHtml(data.language || "")} · ${escapeHtml(data.license || "—")}</span>
      </header>
      <section class="re2-lang">
        ${ls.map((l) => `<div class="re2-lang-cell" style="flex-grow:${Math.max(l.pct, 0.6)}; flex-basis:0; min-width:6px; background:${accent(l.accent)}" title="${escapeHtml(l.name)}"></div>`).join("")}
      </section>
      <section class="re2-stats">${tiles}</section>
      <section class="re2-activity" style="background:${accent("yellow")}; color:${onAccent("yellow")}">
        <div class="re2-activity-head">
          <span>52-WEEK COMMITS</span>
          <span>${fmtCount(data.commits_year)}</span>
        </div>
        ${activityBars(data.commit_weeks, "var(--c-text)")}
      </section>
    </div>
  `;
}

// ===========================================================
// RE3 — SWISS
// ===========================================================
function renderRE3(data) {
  const ls = langs(data);
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="re3-stat">
      <span class="re3-dot" style="background:${accent(s.accent)}"></span>
      <span class="re3-stat-value">${escapeHtml(s.value)}</span>
      <span class="re3-stat-label">${escapeHtml(s.label)}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-re3">
      <div class="re3-eyebrow">
        <span>${escapeHtml(data.repo || "")}</span>
        <span>${escapeHtml(data.language || "")} · ${escapeHtml(data.license || "—")}</span>
      </div>
      <div class="re3-desc">${escapeHtml(data.description || "")}</div>
      <div class="re3-lang-wrap">${langBar(ls)}</div>
      ${langLegend(ls)}
      <div class="re3-rule"></div>
      <section class="re3-stats">${tiles}</section>
      <section class="re3-activity">
        ${activityBars(data.commit_weeks, "var(--c-text)")}
        <div class="re3-activity-foot">52-week commit activity · ${fmtCount(data.commits_year)} · last push ${escapeHtml(ago(data.pushed_at))}</div>
      </section>
    </div>
  `;
}

// ===========================================================
// RE4 — DATA
// ===========================================================
function renderRE4(data) {
  const ls = langs(data);
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="re4-stat" style="border-color:${accent(s.accent)}">
      <i class="ph ph-${s.icon}" style="color:${accent(s.accent)}"></i>
      <span class="re4-stat-value">${escapeHtml(s.value)}</span>
      <span class="re4-stat-label">${s.label.toUpperCase()}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-re4">
      <header class="re4-header">
        <span class="re4-title">${escapeHtml(data.repo || "")}</span>
        <span class="re4-meta">${escapeHtml(data.description || "")}</span>
      </header>
      <section class="re4-stats">${tiles}</section>
      <div class="re4-meta-row">
        <span>LANGUAGES</span>
        <span>52-WEEK COMMITS · ${fmtCount(data.commits_year)}</span>
      </div>
      <section class="re4-body">
        <aside class="re4-lang-col">
          ${langBar(ls)}
          ${langLegend(ls)}
        </aside>
        <div class="re4-activity">
          ${activityBars(data.commit_weeks, accent("green"))}
          <div class="re4-activity-foot">LAST PUSH ${escapeHtml(ago(data.pushed_at))} · ${escapeHtml((data.default_branch || "").toUpperCase())}</div>
        </div>
      </section>
    </div>
  `;
}

const VARIANTS = { re1: renderRE1, re2: renderRE2, re3: renderRE3, re4: renderRE4 };

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/plugins/github_repo/client.css">`;

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
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "re1";
  const renderer = VARIANTS[variant] || renderRE1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data)}
    </div>`;
}
