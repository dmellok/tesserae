// github_contributions — four visual directions (CO1–CO4). Heatmap
// cells use level-keyed shades from the theme's --c-* tokens (no
// ok/warn/danger); stats use the categorical --c-accent / --c-data-*
// hues so themes restyle without losing distinction.

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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function busiestLabel(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}

function monthLabels(weeks) {
  return weeks.map((week, i) => {
    if (i === 0) return `<span></span>`;
    if (!week || !week.length || !week[0].date) return `<span></span>`;
    const cur = new Date(week[0].date).getMonth();
    const prev = (weeks[i - 1].length && weeks[i - 1][0].date)
      ? new Date(weeks[i - 1][0].date).getMonth()
      : -1;
    return `<span>${cur !== prev ? MONTHS[cur] : ""}</span>`;
  }).join("");
}

function heatmapCells(weeks) {
  return weeks.map((week) => {
    const days = week.map((d) => {
      const row = d.date ? new Date(d.date).getDay() + 1 : 1;
      return `<span class="gc-cell" data-lvl="${d.level}" style="grid-row:${row}" title="${escapeHtml(d.date)}: ${d.count}"></span>`;
    }).join("");
    return `<div class="gc-week">${days}</div>`;
  }).join("");
}

function statTiles(data) {
  return [
    { label: "Day streak",      value: String(data.current_streak ?? 0), icon: "flame",          accent: "green"  },
    { label: "Longest streak",  value: String(data.longest_streak ?? 0), icon: "trophy",         accent: "yellow" },
    { label: "This week",       value: String(data.this_week ?? 0),      icon: "calendar-check", accent: "red"    },
    { label: `Busiest · ${busiestLabel(data.busiest_date)}`, value: String(data.busiest_count ?? 0), icon: "lightning", accent: "blue" },
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

function heatmap(weeks) {
  return `
    <div class="gc-heatmap" style="--weeks:${weeks.length}">
      <div class="gc-months">${monthLabels(weeks)}</div>
      <div class="gc-grid">${heatmapCells(weeks)}</div>
    </div>
  `;
}

function legend() {
  return `
    <div class="gc-legend">
      <span>LESS</span>
      <span class="gc-cell" data-lvl="0"></span>
      <span class="gc-cell" data-lvl="1"></span>
      <span class="gc-cell" data-lvl="2"></span>
      <span class="gc-cell" data-lvl="3"></span>
      <span class="gc-cell" data-lvl="4"></span>
      <span>MORE</span>
    </div>
  `;
}

// ===========================================================
// CO1 — REFINED
// ===========================================================
function renderCO1(data, weeks) {
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="co1-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <i class="ph-bold ph-${s.icon}"></i>
      <div class="co1-stat-text">
        <div class="co1-stat-value">${escapeHtml(s.value)}</div>
        <div class="co1-stat-label">${s.label.toUpperCase()}</div>
      </div>
    </div>
  `).join("");
  return `
    <div class="variant variant-co1">
      ${darkHeader(`@${data.user || ""}`, "red", `<i class="ph ph-git-branch"></i> ${data.total ?? 0} THIS YEAR`)}
      <section class="co1-stats">${tiles}</section>
      <section class="co1-body">
        ${heatmap(weeks)}
        <div class="co1-legend">${legend()}</div>
      </section>
    </div>
  `;
}

// ===========================================================
// CO2 — GEOMETRIC
// ===========================================================
function renderCO2(data, weeks) {
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="co2-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <div class="co2-stat-head">
        <span class="co2-stat-label">${s.label.toUpperCase()}</span>
        <i class="ph-bold ph-${s.icon}"></i>
      </div>
      <div class="co2-stat-value">${escapeHtml(s.value)}</div>
    </div>
  `).join("");
  return `
    <div class="variant variant-co2">
      <section class="co2-stats">${tiles}</section>
      <section class="co2-body">
        <div class="co2-head">
          <span>${data.total ?? 0} CONTRIBUTIONS</span>
          ${legend()}
        </div>
        ${heatmap(weeks)}
      </section>
    </div>
  `;
}

// ===========================================================
// CO3 — SWISS
// ===========================================================
function renderCO3(data, weeks) {
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="co3-stat">
      <span class="co3-dot" style="background:${accent(s.accent)}"></span>
      <span class="co3-stat-value">${escapeHtml(s.value)}</span>
      <span class="co3-stat-label">${escapeHtml(s.label)}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-co3">
      <div class="co3-eyebrow">
        <span>@${escapeHtml(data.user || "")}</span>
        <span>${data.total ?? 0} this year</span>
      </div>
      <div class="co3-rule"></div>
      <section class="co3-stats">${tiles}</section>
      <section class="co3-body">${heatmap(weeks)}</section>
    </div>
  `;
}

// ===========================================================
// CO4 — DATA
// ===========================================================
function renderCO4(data, weeks) {
  const stats = statTiles(data);
  const tiles = stats.map((s) => `
    <div class="co4-stat" style="border-color:${accent(s.accent)}">
      <span class="co4-stat-value">${escapeHtml(s.value)}</span>
      <span class="co4-stat-label">${s.label.toUpperCase()}</span>
    </div>
  `).join("");
  return `
    <div class="variant variant-co4">
      <header class="co4-header">
        <span class="co4-title">@${escapeHtml(data.user || "")} · CONTRIBUTIONS</span>
        <span class="co4-meta">${data.total ?? 0} / YR</span>
      </header>
      <section class="co4-stats">${tiles}</section>
      <section class="co4-body">
        ${heatmap(weeks)}
        <div class="co4-legend">${legend()}</div>
      </section>
    </div>
  `;
}

const VARIANTS = { co1: renderCO1, co2: renderCO2, co3: renderCO3, co4: renderCO4 };

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/plugins/github_contributions/client.css">`;

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
  const weeks = Array.isArray(data.weeks) ? data.weeks : [];
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "co1";
  const renderer = VARIANTS[variant] || renderCO1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, weeks)}
    </div>`;
}
