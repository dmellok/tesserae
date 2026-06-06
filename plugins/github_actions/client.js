// github_actions, Spectra list archetype. Each workflow row leads
// with its conclusion icon (success → moss check, failure →
// terracotta x, in_progress → ochre arrow), carries the workflow
// name + repo + branch as the title, and below the title paints a
// mini timeline strip: up to 8 vertical bars whose heights track the
// run's duration and whose colours track the run's conclusion. The
// latest run sits on the right. A workflow-type glyph tucks beside
// the workflow name (build / test / deploy / lint / docs / release /
// security / ci) so a packed list still reads at a glance.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const CONCLUSION_PH = {
  success: "ph-check-circle",
  failure: "ph-x-circle",
  cancelled: "ph-prohibit",
  skipped: "ph-skip-forward",
  timed_out: "ph-clock",
  in_progress: "ph-arrows-clockwise",
  queued: "ph-hourglass",
};

const CONCLUSION_ACCENT = {
  success: "var(--accent-3)",
  failure: "var(--accent-1)",
  cancelled: "var(--text-muted)",
  skipped: "var(--text-muted)",
  timed_out: "var(--accent-2)",
  in_progress: "var(--accent-2)",
  queued: "var(--accent-5)",
};

// Workflow-type icon table. First match wins; falls back to ph-gear
// for the generic case. Matched against the workflow's display name
// (e.g. "Build & Test", "Deploy to Prod", "Lint", "Release").
const WORKFLOW_TYPE_ICONS = [
  [/deploy|publish|release/i, "ph-rocket-launch"],
  [/test|spec|e2e|integration|pytest|jest|vitest/i, "ph-test-tube"],
  [/lint|format|style|prettier|ruff|eslint/i, "ph-magic-wand"],
  [/build|compile|bundle/i, "ph-hammer"],
  [/docs|wiki|pages|mkdocs/i, "ph-book-open"],
  [/security|scan|audit|codeql|trivy/i, "ph-shield-check"],
  [/docker|image|container/i, "ph-package"],
  [/^ci$|continuous|workflow/i, "ph-play-circle"],
];

function workflowTypeIcon(name) {
  for (const [re, icon] of WORKFLOW_TYPE_ICONS) {
    if (re.test(name || "")) return icon;
  }
  return "ph-gear";
}

function conclusionIcon(run) {
  if (run.status === "in_progress" || run.status === "queued") return CONCLUSION_PH[run.status] || "ph-arrows-clockwise";
  return CONCLUSION_PH[run.conclusion] || "ph-circle";
}

function conclusionAccent(run) {
  if (run.status === "in_progress" || run.status === "queued") return CONCLUSION_ACCENT[run.status] || "var(--accent-2)";
  return CONCLUSION_ACCENT[run.conclusion] || "var(--text-secondary)";
}

function repoShort(name) {
  if (typeof name !== "string") return "";
  const slash = name.lastIndexOf("/");
  return slash >= 0 ? name.slice(slash + 1) : name;
}

function formatDuration(secs) {
  if (!Number.isFinite(secs)) return "";
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// Mini timeline strip, up to 8 vertical bars, height ∝ run duration
// relative to the row's max, colour = run conclusion. Tooltip on
// hover carries the run number + status + duration so you can still
// drill down to a specific run without leaving the dashboard.
function timelineStrip(run) {
  const history = Array.isArray(run.history) ? run.history : [];
  if (history.length === 0) return "";
  const durations = history.map((h) => h.duration_s).filter((d) => Number.isFinite(d) && d > 0);
  const maxDur = durations.length ? Math.max(...durations) : 0;
  const bars = history.map((h, idx) => {
    const isLatest = idx === history.length - 1;
    let heightPct;
    if (maxDur > 0 && Number.isFinite(h.duration_s) && h.duration_s > 0) {
      heightPct = Math.max(18, (h.duration_s / maxDur) * 100);
    } else {
      heightPct = 30;
    }
    let color;
    if (h.status === "in_progress" || h.status === "queued") {
      color = CONCLUSION_ACCENT[h.status] || "var(--accent-2)";
    } else {
      color = CONCLUSION_ACCENT[h.conclusion] || "var(--text-muted)";
    }
    const opacity = isLatest ? 1 : 0.75;
    const tip = `#${h.run_number ?? "?"} ${h.conclusion || h.status || "?"}${
      Number.isFinite(h.duration_s) ? ` · ${formatDuration(h.duration_s)}` : ""
    }`;
    return `<span class="ga-bar${isLatest ? " is-latest" : ""}"
                  style="height:${heightPct.toFixed(0)}%;background:${color};opacity:${opacity}"
                  title="${escapeHtml(tip)}"></span>`;
  }).join("");
  return `<div class="ga-timeline">${bars}</div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_actions">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Actions</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const runs = Array.isArray(data.runs) ? data.runs : [];
  const failing = runs.filter((r) => r.conclusion === "failure").length;
  const inProgress = runs.filter((r) => r.status === "in_progress").length;

  let meta;
  if (failing > 0) meta = `<span class="w-title-meta" style="color:var(--accent-1)">${failing} FAILING</span>`;
  else if (inProgress > 0) meta = `<span class="w-title-meta" style="color:var(--accent-2)">${inProgress} RUNNING</span>`;
  else meta = `<span class="w-title-meta">${runs.length} OK</span>`;

  if (runs.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="github_actions">
        <div class="w-title">
          <i class="ph-bold ph-github-logo" style="color:var(--accent-5)"></i>
          <h3>Actions</h3>
        </div>
        <div class="w-body"><p class="u-muted">No runs.</p></div>
      </div>`;
    return;
  }

  const rows = runs.map((r, i) => {
    const accent = conclusionAccent(r);
    const ph = conclusionIcon(r);
    const typePh = workflowTypeIcon(r.name);
    const duration = formatDuration(r.duration_s);
    const durChip = duration
      ? `<span class="ga-dur" title="latest run duration">${escapeHtml(duration)}</span>`
      : "";
    const titleBit = `
      <span class="ga-title-text">
        <i class="ph-bold ${typePh} ga-type-ic" title="${escapeHtml(r.name || "Workflow")}"></i>
        <span class="ga-name">${escapeHtml(r.name || "Workflow")}</span>
        <small class="u-muted ga-repo">${escapeHtml(repoShort(r.repo))}</small>
      </span>`;
    const branch = r.branch ? `<small class="ga-branch">${escapeHtml(r.branch)}</small>` : "";
    return `
      <div class="ga-row ${i % 2 ? "is-zebra" : ""}">
        <div class="ga-row-head">
          <div class="list-lead">
            <i class="ph-bold ${ph}" style="color:${accent}"></i>
            ${titleBit}
          </div>
          <div class="ga-row-meta">
            ${branch}
            ${durChip}
          </div>
        </div>
        ${timelineStrip(r)}
      </div>`;
  }).join("");

  const headAccent = failing > 0 ? "var(--accent-1)" : inProgress > 0 ? "var(--accent-2)" : "var(--accent-3)";

  const layout = `
    .ga-row {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
    }
    .ga-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .ga-row-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
      min-width: 0;
    }
    .ga-row-head .list-lead {
      min-width: 0;
      flex: 1 1 auto;
    }
    .ga-title-text {
      display: inline-flex;
      align-items: baseline;
      gap: var(--space-1);
      min-width: 0;
      overflow: hidden;
    }
    .ga-type-ic {
      color: var(--text-secondary);
      font-size: .85em;
      flex: 0 0 auto;
    }
    .ga-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .ga-repo {
      font-weight: var(--fw-semi);
      font-size: .7em;
      margin-left: .2em;
      flex: 0 0 auto;
    }
    .ga-row-meta {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      flex: 0 0 auto;
    }
    .ga-branch {
      font-size: .7em;
      color: var(--text-muted);
      font-weight: var(--fw-semi);
    }
    .ga-dur {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      color: var(--text-secondary);
      padding: 1px var(--space-1);
      border-radius: var(--radius-1);
      background: color-mix(in oklab, var(--text-primary) 5%, transparent);
      font-variant-numeric: tabular-nums;
    }
    /* Mini timeline: 8 vertical bars, latest run on the right. Sits
       in a sunken track so the bars read as a deliberate chart row
       rather than a free-floating set of pips. */
    .ga-timeline {
      display: flex;
      align-items: flex-end;
      gap: 2px;
      height: 16px;
      padding: 2px 4px;
      border-radius: var(--radius-1);
      background: color-mix(in oklab, var(--text-primary) 4%, var(--surface));
      margin-left: calc(1.2em + var(--space-2));
    }
    .ga-bar {
      flex: 1 1 0;
      min-width: 3px;
      border-radius: 1.5px;
      transition: none;
    }
    .ga-bar.is-latest {
      /* Latest bar reads as "now" purely via the full-opacity colour
         vs the 75% opacity on older bars. No halo. */
    }
    /* xs / sm: drop the duration chip + branch to keep the row compact. */
    @container (max-width: 320px) {
      .ga-dur, .ga-branch { display: none; }
    }
    @container (max-width: 240px) {
      .ga-timeline { display: none; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="github_actions">
      <div class="w-title">
        <i class="ph-bold ph-github-logo" style="color:${headAccent}"></i>
        <h3>Actions</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
