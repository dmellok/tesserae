// github_actions — four visual directions for CI runs (CI1–CI4). All
// colours come from categorical --c-* tokens (no ok/warn/danger), so
// "passed" / "failed" read as distinct hues without claiming a semantic
// status meaning that themes might re-tone.

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

// status helpers — keyed off conclusion/status from the GH Actions API.
function statusInfo(r) {
  const conc = (r.conclusion || "").toLowerCase();
  const stat = (r.status || "").toLowerCase();
  if (conc === "success") return { key: "passed", label: "PASSED", icon: "check", accent: "green" };
  if (conc === "failure" || conc === "timed_out" || conc === "cancelled") return { key: "failed", label: "FAILED", icon: "x", accent: "red" };
  if (stat === "in_progress" || stat === "queued" || stat === "waiting") return { key: "live", label: "LIVE", icon: "play", accent: "yellow" };
  return { key: "other", label: conc.toUpperCase() || stat.toUpperCase() || "—", icon: "circle", accent: "muted" };
}

function deriveStats(runs) {
  let pass = 0, fail = 0, live = 0;
  for (const r of runs) {
    const k = statusInfo(r).key;
    if (k === "passed") pass++;
    else if (k === "failed") fail++;
    else if (k === "live") live++;
  }
  const total = runs.length;
  const considered = pass + fail;
  const success = considered > 0 ? Math.round((pass / considered) * 100) : 0;
  return {
    success,
    stats: [
      { label: "Pass", value: String(pass),  accent: "green",  icon: "check" },
      { label: "Fail", value: String(fail),  accent: "red",    icon: "x" },
      { label: "Live", value: String(live),  accent: "yellow", icon: "play" },
      { label: "Runs", value: String(total), accent: "ink",    icon: "list" },
    ],
  };
}

function ago(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
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

// SVG donut, sized to fit container.
function donutSvg(pct, color, label) {
  const size = 100, sw = 13;
  const r = (size - sw) / 2;
  const c = 2 * Math.PI * r;
  const cx = size / 2;
  const fill = ((pct / 100) * c).toFixed(2);
  return `
    <svg class="gh-donut" viewBox="0 0 ${size} ${size}" preserveAspectRatio="xMidYMid meet">
      <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="color-mix(in srgb, var(--c-text) 14%, transparent)" stroke-width="${sw}"/>
      <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round" stroke-dasharray="${fill} ${c.toFixed(2)}" transform="rotate(-90 ${cx} ${cx})"/>
      <text x="${cx}" y="${cx + 4}" text-anchor="middle" class="gh-donut-num">${pct}<tspan class="gh-donut-pct">%</tspan></text>
      <text x="${cx}" y="${cx + 20}" text-anchor="middle" class="gh-donut-label">${escapeHtml(label)}</text>
    </svg>
  `;
}

function runRow(r, classPrefix) {
  const s = statusInfo(r);
  return `
    <div class="${classPrefix}-run">
      <span class="${classPrefix}-pill" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
        <i class="ph-bold ph-${s.icon}"></i>${s.label}
      </span>
      <span class="${classPrefix}-job">${escapeHtml(r.name || "—")}</span>
      <span class="${classPrefix}-repo">${escapeHtml(r.repo || "")}</span>
      <span class="${classPrefix}-ref"><i class="ph ph-git-branch"></i>${escapeHtml(r.branch || "")}</span>
      <span class="${classPrefix}-ago">${escapeHtml(ago(r.updated_at))}</span>
    </div>
  `;
}

// ===========================================================
// CI1 — REFINED
// ===========================================================
function renderCI1(data, runs) {
  const d = deriveStats(runs);
  const tiles = d.stats.map((s) => `
    <div class="ci1-stat" style="background:color-mix(in oklab, ${accent(s.accent)} 18%, transparent)">
      <i class="ph ph-${s.icon}" style="color:${accent(s.accent)}"></i>
      <span class="ci1-stat-value">${escapeHtml(s.value)}</span>
      <span class="ci1-stat-label">${s.label.toUpperCase()}</span>
    </div>
  `).join("");
  const rows = runs.slice(0, 6).map((r) => runRow(r, "ci1")).join("");
  return `
    <div class="variant variant-ci1">
      ${darkHeader("CI RUNS", "red", `<i class="ph ph-play"></i>`)}
      <section class="ci1-summary">
        <div class="ci1-donut">${donutSvg(d.success, accent("green"), "SUCCESS")}</div>
        <div class="ci1-stats">${tiles}</div>
      </section>
      <section class="ci1-runs">${rows}</section>
    </div>
  `;
}

// ===========================================================
// CI2 — GEOMETRIC
// ===========================================================
function renderCI2(data, runs) {
  const d = deriveStats(runs);
  const tiles = d.stats.map((s) => `
    <div class="ci2-stat" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
      <span class="ci2-stat-value">${escapeHtml(s.value)}</span>
      <span class="ci2-stat-label">${s.label.toUpperCase()}</span>
    </div>
  `).join("");
  const rows = runs.slice(0, 6).map((r) => {
    const s = statusInfo(r);
    return `
      <div class="ci2-run">
        <span class="ci2-run-block" style="background:${accent(s.accent)}; color:${onAccent(s.accent)}">
          <i class="ph-bold ph-${s.icon}"></i>
        </span>
        <span class="ci2-job">${escapeHtml(r.name || "—")}</span>
        <span class="ci2-meta">${escapeHtml(r.repo || "")} · ${escapeHtml(r.branch || "")}</span>
        <span class="ci2-ago">${escapeHtml(ago(r.updated_at))}</span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-ci2">
      <section class="ci2-top">
        <div class="ci2-donut-wrap">${donutSvg(d.success, accent("green"), "SUCCESS")}</div>
        <div class="ci2-stats">${tiles}</div>
      </section>
      <section class="ci2-runs">${rows}</section>
    </div>
  `;
}

// ===========================================================
// CI3 — SWISS
// ===========================================================
function renderCI3(data, runs) {
  const d = deriveStats(runs);
  const tiles = d.stats.map((s) => `
    <div class="ci3-stat">
      <span class="ci3-dot" style="background:${accent(s.accent)}"></span>
      <span class="ci3-stat-value">${escapeHtml(s.value)}</span>
      <span class="ci3-stat-label">${escapeHtml(s.label)}</span>
    </div>
  `).join("");
  const rows = runs.slice(0, 6).map((r) => {
    const s = statusInfo(r);
    return `
      <div class="ci3-run">
        <span class="ci3-dot" style="background:${accent(s.accent)}"></span>
        <span class="ci3-status">${escapeHtml(s.label)}</span>
        <span class="ci3-job">${escapeHtml(r.name || "—")}</span>
        <span class="ci3-meta">${escapeHtml(r.repo || "")} · ${escapeHtml(r.branch || "")} · ${escapeHtml(ago(r.updated_at))}</span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-ci3">
      <div class="ci3-eyebrow">
        <span>CI Runs</span>
        <span>${d.success}% SUCCESS · ${runs.length} RUNS</span>
      </div>
      <div class="ci3-rule"></div>
      <section class="ci3-stats">${tiles}</section>
      <section class="ci3-runs">${rows}</section>
    </div>
  `;
}

// ===========================================================
// CI4 — DATA
// ===========================================================
function renderCI4(data, runs) {
  const d = deriveStats(runs);
  const pass = parseInt(d.stats[0].value, 10);
  const fail = parseInt(d.stats[1].value, 10);
  const live = parseInt(d.stats[2].value, 10);
  const total = Math.max(1, pass + fail + live);
  const rows = runs.slice(0, 6).map((r) => {
    const s = statusInfo(r);
    return `
      <div class="ci4-run">
        <i class="ph ph-${s.icon}" style="color:${accent(s.accent)}"></i>
        <span class="ci4-job">${escapeHtml(r.name || "—")}</span>
        <span class="ci4-ref">${escapeHtml(r.branch || "")}</span>
        <span class="ci4-meta">${escapeHtml(r.repo || "")} · ${escapeHtml(ago(r.updated_at))}</span>
      </div>
    `;
  }).join("");
  return `
    <div class="variant variant-ci4">
      <header class="ci4-header">
        <span class="ci4-title">CI RUNS</span>
        <span class="ci4-meta">${runs.length} RUNS · ${d.success}% PASS</span>
      </header>
      <section class="ci4-summary">
        <div class="ci4-donut">${donutSvg(d.success, accent("green"), "SUCCESS")}</div>
        <div class="ci4-bar-wrap">
          <div class="ci4-bar">
            <div style="flex:${pass}; background:${accent("green")}"></div>
            <div style="flex:${fail}; background:${accent("red")}"></div>
            <div style="flex:${live}; background:${accent("yellow")}"></div>
          </div>
          <div class="ci4-bar-legend">
            <span style="color:${accent("green")}">■ ${pass} PASS</span>
            <span style="color:${accent("red")}">■ ${fail} FAIL</span>
            <span style="color:${accent("yellow")}">■ ${live} LIVE</span>
          </div>
        </div>
      </section>
      <section class="ci4-runs">${rows}</section>
    </div>
  `;
}

const VARIANTS = { ci1: renderCI1, ci2: renderCI2, ci3: renderCI3, ci4: renderCI4 };

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/plugins/github_actions/client.css">`;

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
  const runs = Array.isArray(data.runs) ? data.runs : [];
  if (!runs.length) {
    shadow.innerHTML = `${HEAD}
      <div class="root">
        <div class="gh-stub">
          <i class="ph-duotone ph-play-circle"></i>
          <div class="gh-stub-primary">No runs yet</div>
          <div class="gh-stub-secondary">List repos in the cell options to chart recent CI runs.</div>
        </div>
      </div>`;
    return;
  }
  const size = ctx.cell.size;
  const variant = (ctx.cell.options && ctx.cell.options.variant) || "ci1";
  const renderer = VARIANTS[variant] || renderCI1;
  shadow.innerHTML = `${HEAD}
    <div class="root size-${size} variant-host variant-host--${variant}">
      ${renderer(data, runs)}
    </div>`;
}
