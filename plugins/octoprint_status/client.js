// octoprint_status — Spectra status archetype. The hero is replaced
// by a large radial ring showing the job's completion percentage,
// with the percent number at the centre and elapsed / ETA captions
// underneath. Temperature row sits below as a status-grid. When no
// job is loaded the ring collapses to a state-only display.

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

// Job completion ring — big SVG circle with the percentage at the
// centre, an outer halo at the active accent tinted by tone, and
// the print state glyph in the dead-centre middle of the percent
// number's ascender height for a small visual rest.
function completionRingSvg({ pct, accent, stateIcon }) {
  const r = 38;
  const circ = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  const filled = circ * (clamped / 100);
  return `
    <div class="octo-ring-wrap">
      <svg viewBox="-45 -45 90 90" aria-hidden="true" style="width:100%;height:100%;display:block">
        <g transform="rotate(-90)">
          <circle r="${r}" fill="none" stroke="color-mix(in oklab, var(--text-primary) 8%, var(--surface))" stroke-width="6"/>
          <circle r="${r}" fill="none" stroke="${accent}" stroke-width="6"
                  stroke-dasharray="${filled.toFixed(2)} ${circ.toFixed(2)}"
                  stroke-linecap="round"/>
        </g>
        <text x="0" y="2" text-anchor="middle" font-size="22" font-weight="900"
              fill="${accent}" font-family="var(--font-family)"
              font-variant-numeric="tabular-nums">${Math.round(clamped)}<tspan font-size="14">%</tspan></text>
        <text x="0" y="18" text-anchor="middle" font-size="9" font-weight="900"
              fill="var(--text-muted)" font-family="var(--font-family)"
              letter-spacing="0.1em">COMPLETE</text>
      </svg>
    </div>`;
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

  const cells = [
    ["Hotend", `${fmtTemp(tool.actual)}<small style="font-size:.55em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.2em">/ ${escapeHtml(fmtTemp(tool.target))}</small>`, "var(--accent-1)"],
    ["Bed", `${fmtTemp(bed.actual)}<small style="font-size:.55em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.2em">/ ${escapeHtml(fmtTemp(bed.target))}</small>`, "var(--accent-2)"],
  ];
  const grid = cells.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${value}</span>
    </div>`).join("");

  const hasJob = job && Number.isFinite(job.completion);
  const ring = hasJob ? completionRingSvg({ pct: job.completion, accent, stateIcon }) : "";

  const layout = `
    .octo-hero {
      display: flex;
      align-items: center;
      gap: var(--space-3);
      flex: 1 1 auto;
      min-height: 0;
    }
    .octo-ring-wrap {
      flex: 0 0 auto;
      width: clamp(7em, 32cqmin, 12em);
      aspect-ratio: 1;
    }
    .octo-text {
      flex: 1 1 auto;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
    }
    .octo-state-line {
      display: flex;
      align-items: center;
      gap: var(--space-1);
      font-size: var(--fs-headline);
      font-weight: var(--fw-black);
      color: ${accent};
      line-height: var(--lh-tight);
    }
    .octo-state-line i {
      font-size: .9em;
    }
    .octo-jobname {
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
      font-size: var(--fs-body);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .octo-times {
      display: flex;
      gap: var(--space-3);
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      color: var(--text-muted);
    }
    .octo-times-value {
      color: var(--text-primary);
      font-variant-numeric: tabular-nums;
    }
    @container (max-aspect-ratio: 1) {
      .octo-hero {
        flex-direction: column;
        align-items: center;
      }
      .octo-text {
        text-align: center;
        align-items: center;
      }
    }
  `;

  const heroBody = hasJob
    ? `
      <div class="octo-hero">
        ${ring}
        <div class="octo-text">
          <span class="octo-state-line"><i class="ph-bold ${stateIcon}"></i>${escapeHtml(state.text)}</span>
          <span class="octo-jobname">${escapeHtml(job?.name || "")}</span>
          <div class="octo-times">
            <span>Elapsed <span class="octo-times-value">${escapeHtml(fmtSecs(job.elapsed))}</span></span>
            <span>ETA <span class="octo-times-value">${escapeHtml(job.eta || fmtSecs(job.remaining))}</span></span>
          </div>
        </div>
      </div>`
    : `
      <div class="status-hero">
        <i class="ph-bold ${stateIcon}" style="color:${accent}"></i>
        <div class="lockup">
          <span class="status-state">${escapeHtml(state.text)}</span>
          <span class="status-sub">${escapeHtml(job?.name || "no job loaded")}</span>
        </div>
      </div>`;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="octoprint_status">
      <div class="w-title">
        <i class="ph-bold ph-printer" style="color:${accent}"></i>
        <h3>${escapeHtml(label)}</h3>
        ${data.time ? `<span class="w-title-meta">${escapeHtml(data.time)}</span>` : ""}
      </div>
      <div class="w-body status-body">
        ${heroBody}
        <span class="pill" style="background:${accent}">${escapeHtml(tone)}</span>
        <div class="status-grid">${grid}</div>
      </div>
    </div>`;
}
