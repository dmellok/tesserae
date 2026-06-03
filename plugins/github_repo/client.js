// github_repo — four visual directions for a single-repo card (RE1–RE4).
// Mapped from a Bauhaus / Swiss handoff specifically tuned for the repo
// card. Categorical accents use --c-* tokens (no ok/warn/danger).

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
function tint(n) { return `color-mix(in oklab, ${accent(n)} 18%, transparent)`; }

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
    { label: "Stars",   value: fmtCount(data.stars),       icon: "star",           accent: "green" },
    { label: "Forks",   value: fmtCount(data.forks),       icon: "git-fork",       accent: "blue"  },
    { label: "Issues",  value: fmtCount(data.issues),      icon: "warning-circle", accent: "red"   },
    { label: "Release", value: data.latest_release || "—", icon: "tag",            accent: "ink"   },
  ];
}

// Server's languages don't carry an accent field; assign by index.
function langs(data) {
  return (data.languages || []).map((l, i) => ({ ...l, accent: PALETTE[i] || "muted" }));
}

function langBar(languages, h) {
  const cells = languages.map((l) => `
    <div class="re-langbar-seg" style="flex-grow:${Math.max(l.pct, 0.6)}; flex-basis:0; min-width:4px; background:${accent(l.accent)}"></div>
  `).join("");
  return `<div class="re-langbar"${h ? ` style="--bar-h:${h}px"` : ""}>${cells}</div>`;
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

// ===========================================================
// RE1 — REFINED
// Charcoal header (lowercase name + repo · PUBLIC); description with
// inline lang + license chips; stacked lang bar + legend; hairline
// stat row (no solid fills, icon + numeral + label); commit chart
// fills remaining height; muted footer.
// ===========================================================
function renderRE1(data) {
  const ls = langs(data);
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="re1-stat">
      <i class="ph ph-${s.icon}" style="color:${accent(s.accent)}"></i>
      <div class="re1-stat-text">
        <div class="re1-stat-value">${escapeHtml(s.value)}</div>
        <div class="re1-stat-label">${s.label.toUpperCase()}</div>
      </div>
    </div>
  `).join("");
  return `
    <div class="variant variant-re1">
      <header class="re1-dark">
        <span class="re1-dark-chip"></span>
        <span class="re1-dark-title">${escapeHtml(data.repo || "")}</span>
        <span class="re1-dark-meta">
          <i class="ph ph-book-bookmark"></i>${data.is_archived ? "ARCHIVED" : "PUBLIC"}
        </span>
      </header>
      <section class="re1-meta">
        <span class="re1-desc">${escapeHtml(data.description || "")}</span>
        <span class="re1-chips">
          <span class="re1-chip re1-chip--lang">
            <span class="re1-chip-dot" style="background:${accent("green")}"></span>${escapeHtml(data.language || "")}
          </span>
          <span class="re1-chip re1-chip--lic">${escapeHtml(data.license || "—")}</span>
        </span>
      </section>
      <section class="re1-lang">
        ${langBar(ls)}
        ${langLegend(ls)}
      </section>
      <section class="re1-stats">${tiles}</section>
      <section class="re1-activity">
        <div class="re1-activity-head">
          <span>52-WEEK COMMIT ACTIVITY</span>
          <span>${fmtCount(data.commits_year)} COMMITS</span>
        </div>
        ${activityBars(data.commit_weeks, accent("green"))}
      </section>
      <footer class="re1-foot">
        <i class="ph ph-clock"></i>
        LAST PUSH ${escapeHtml(ago(data.pushed_at))} · ${escapeHtml((data.default_branch || "").toUpperCase())}
      </footer>
    </div>
  `;
}

// ===========================================================
// RE2 — GEOMETRIC (De Stijl)
// Solid green title bar, slim paper desc strip, solid colour lang
// strip (taller), 4-up solid-colour stat tiles, yellow commit panel
// with ink bars. Ink gaps between sections.
// ===========================================================
function renderRE2(data) {
  const ls = langs(data);
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="re2-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <span class="re2-stat-label">${s.label.toUpperCase()}</span>
      <span class="re2-stat-value">${escapeHtml(s.value)}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-re2">
      <header class="re2-header" style="background:${accent("green")}; color:${onAccent("green")}">
        <span class="re2-title">${escapeHtml((data.repo || "").toUpperCase())}</span>
        <span class="re2-meta">${escapeHtml(data.language || "")} · ${escapeHtml(data.license || "—")}</span>
      </header>
      <section class="re2-desc">${escapeHtml(data.description || "")}</section>
      <section class="re2-lang">${langBar(ls, 16).replace('class="re-langbar"', 'class="re-langbar re-langbar--thick"')}</section>
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
// RE3 — SWISS / INTERNATIONAL
// No charcoal. Lowercase bold name + thin desc; thin lang bar +
// legend; hairline rule; stats row with small coloured SQUARES +
// light-weight numerals; ink commit bars fill bottom; one mono
// caption line.
// ===========================================================
function renderRE3(data) {
  const ls = langs(data);
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="re3-stat">
      <span class="re3-square" style="background:${accent(s.accent)}"></span>
      <span class="re3-stat-value">${escapeHtml(s.value)}</span>
      <span class="re3-stat-label">${escapeHtml(s.label.toUpperCase())}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-re3">
      <div class="re3-head">
        <div class="re3-head-left">
          <div class="re3-name">${escapeHtml(data.repo || "")}</div>
          <div class="re3-desc">${escapeHtml(data.description || "")}</div>
        </div>
        <div class="re3-head-right">${escapeHtml(data.language || "")} · ${escapeHtml(data.license || "—")}</div>
      </div>
      <div class="re3-lang-wrap">${langBar(ls)}</div>
      ${langLegend(ls)}
      <div class="re3-rule"></div>
      <section class="re3-stats">${tiles}</section>
      <section class="re3-activity">
        ${activityBars(data.commit_weeks, "var(--c-text)")}
      </section>
      <footer class="re3-foot">52-week commit activity · ${fmtCount(data.commits_year)} · last push ${escapeHtml(ago(data.pushed_at))}</footer>
    </div>
  `;
}

// ===========================================================
// RE4 — DATA
// No charcoal. Lowercase name + desc on the right; outlined stat
// tiles across top; two-column body: left = vertical language list
// (row per lang, distributed to fill) under thin lang bar; right =
// big commit chart filling its panel. Hairline divider between cols.
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
  const langRows = ls.map((l) => `
    <div class="re4-lang-row">
      <span class="re4-lang-dot" style="background:${accent(l.accent)}"></span>
      <span class="re4-lang-name">${escapeHtml(l.name)}</span>
      <span class="re4-lang-pct">${l.pct}%</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-re4">
      <header class="re4-header">
        <span class="re4-title">${escapeHtml(data.repo || "")}</span>
        <span class="re4-meta">${escapeHtml(data.description || "")}</span>
      </header>
      <section class="re4-stats">${tiles}</section>
      <section class="re4-body">
        <aside class="re4-lang-col">
          <div class="re4-col-head">LANGUAGES</div>
          ${langBar(ls)}
          <div class="re4-lang-list">${langRows}</div>
        </aside>
        <div class="re4-activity">
          <div class="re4-col-head">
            <span>52-WEEK COMMITS</span>
            <span>${fmtCount(data.commits_year)}</span>
          </div>
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
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
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
