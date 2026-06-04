// octoprint_status — Spectra status archetype. Hero shows the
// printer state + filename when a job is loaded; pill colours by
// tone (printing → moss, paused → ochre, error → terracotta,
// offline/idle → muted). Job progress fills an .img-progress bar
// with elapsed / ETA times underneath; temps drop into the
// status-grid.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const TONE_ACCENT = {
  printing: "var(--accent-3)",
  paused: "var(--accent-2)",
  complete: "var(--accent-3)",
  error: "var(--accent-1)",
  offline: "var(--text-muted)",
  idle: "var(--text-secondary)",
};

const TONE_ICON = {
  printing: "ph-printer",
  paused: "ph-pause",
  complete: "ph-check-circle",
  error: "ph-warning-circle",
  offline: "ph-plug",
  idle: "ph-printer",
};

function fmtSecs(s) {
  if (!Number.isFinite(s) || s < 0) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h${m}m` : `${m}m`;
}

function fmtTemp(t) {
  if (t == null) return "—";
  const v = Number(t);
  if (!Number.isFinite(v)) return "—";
  return `${Math.round(v)}°`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="octoprint_status">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>OctoPrint</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const state = data.state || { text: "—", tone: "idle" };
  const tone = state.tone || "idle";
  const accent = TONE_ACCENT[tone] || "var(--text-secondary)";
  const stateIcon = TONE_ICON[tone] || "ph-printer";
  const label = data.label || "Printer";

  const job = data.job || null;
  const temps = data.temps || { tool: { actual: null, target: null }, bed: { actual: null, target: null } };
  const tool = temps.tool || {};
  const bed = temps.bed || {};

  const progressBar = job && Number.isFinite(job.completion)
    ? `
      <div class="img-progress">
        <div class="img-progress-track">
          <div class="img-progress-fill" style="width:${Math.max(0, Math.min(100, job.completion)).toFixed(1)}%;background:${accent}"></div>
        </div>
        <div class="img-progress-times">
          <span>${escapeHtml(fmtSecs(job.elapsed))}</span>
          <span>${escapeHtml(job.eta || fmtSecs(job.remaining))}</span>
        </div>
      </div>`
    : "";

  const cells = [
    ["Hotend", `${fmtTemp(tool.actual)}<small style="font-size:.55em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.2em">/ ${escapeHtml(fmtTemp(tool.target))}</small>`, "var(--accent-1)"],
    ["Bed", `${fmtTemp(bed.actual)}<small style="font-size:.55em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.2em">/ ${escapeHtml(fmtTemp(bed.target))}</small>`, "var(--accent-2)"],
  ];
  const grid = cells.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${value}</span>
    </div>`).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="octoprint_status">
      <div class="w-title">
        <i class="ph-bold ph-printer" style="color:${accent}"></i>
        <h3>${escapeHtml(label)}</h3>
        ${data.time ? `<span class="w-title-meta">${escapeHtml(data.time)}</span>` : ""}
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ${stateIcon}" style="color:${accent}"></i>
          <div class="lockup">
            <span class="status-state">${escapeHtml(state.text)}</span>
            <span class="status-sub">${escapeHtml(job?.name || "no job loaded")}</span>
          </div>
        </div>
        <span class="pill" style="background:${accent}">${escapeHtml(tone)}</span>
        ${progressBar}
        <div class="status-grid">${grid}</div>
      </div>
    </div>`;
}
