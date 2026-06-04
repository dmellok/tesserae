// github_actions — Spectra list archetype. Each workflow run is a
// zebra row leading with a conclusion icon (success → moss check,
// failure → terracotta x, in_progress → ochre arrow), the workflow
// name + branch as the title, and the run number / event as meta.

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
    const titleBit = `${escapeHtml(r.name || "Workflow")}<small class="u-muted" style="font-weight:var(--fw-semi);font-size:.7em;margin-left:.4em">${escapeHtml(repoShort(r.repo))}</small>`;
    const branch = r.branch ? `<small style="font-size:.7em;color:var(--text-muted);font-weight:var(--fw-semi)">${escapeHtml(r.branch)}</small>` : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${titleBit}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${branch}</span>
      </div>`;
  }).join("");

  const headAccent = failing > 0 ? "var(--accent-1)" : inProgress > 0 ? "var(--accent-2)" : "var(--accent-3)";

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="github_actions">
      <div class="w-title">
        <i class="ph-bold ph-github-logo" style="color:${headAccent}"></i>
        <h3>Actions</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
