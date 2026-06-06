// glances_status — Spectra status archetype. CPU as the hero number
// + tone-coded state pill. Below: per-metric mini ring gauges for
// CPU / RAM / Disk so the percentage reads as a filled arc rather
// than just a number. xs drops the ring grid for a clean single-
// number layout.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const TONE_ACCENT = {
  ok: "var(--accent-3)",
  warn: "var(--accent-2)",
  danger: "var(--accent-1)",
  offline: "var(--text-muted)",
};

const TONE_ICON = {
  ok: "ph-pulse",
  warn: "ph-warning",
  danger: "ph-warning-circle",
  offline: "ph-plug",
};

function fmtPct(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(n)}%`;
}

function fmtLoad(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n < 10 ? n.toFixed(2) : n.toFixed(1);
}

function fmtUptime(secs) {
  if (!Number.isFinite(secs) || secs <= 0) return "—";
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  const mins = Math.floor((secs % 3600) / 60);
  if (days >= 1) return hours > 0 ? `${days}d${hours}h` : `${days}d`;
  if (hours >= 1) return mins > 0 ? `${hours}h${mins}m` : `${hours}h`;
  return `${mins}m`;
}

// SVG mini ring gauge. Shows a percentage as a filled arc on a
// circular track. Renders the percent number in the centre.
function ringSvg({ pct, label, color, sublabel }) {
  const r = 22;
  const circ = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  const filled = circ * (clamped / 100);
  return `
    <div class="glances-ring-tile">
      <div class="glances-ring-wrap">
        <svg viewBox="-28 -28 56 56" aria-hidden="true" style="width:100%;height:100%;display:block">
          <g transform="rotate(-90)">
            <circle r="${r}" fill="none" stroke="color-mix(in oklab, var(--text-primary) 8%, var(--surface))" stroke-width="5"/>
            <circle r="${r}" fill="none" stroke="${color}" stroke-width="5"
                    stroke-dasharray="${filled.toFixed(2)} ${circ.toFixed(2)}"
                    stroke-linecap="round"/>
          </g>
          <text x="0" y="3" text-anchor="middle" font-size="13" font-weight="900"
                fill="${color}" font-family="var(--font-family)"
                font-variant-numeric="tabular-nums">${Math.round(clamped)}%</text>
        </svg>
      </div>
      <div class="glances-ring-meta">
        <span class="glances-ring-label">${escapeHtml(label)}</span>
        ${sublabel ? `<small class="glances-ring-sub">${escapeHtml(sublabel)}</small>` : ""}
      </div>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const size = ctx?.cell?.size ?? "md";
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="glances_status">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Glances</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const state = data.state || { text: "—", tone: "ok" };
  const tone = state.tone || "ok";
  const accent = TONE_ACCENT[tone] || "var(--text-secondary)";
  const stateIcon = TONE_ICON[tone] || "ph-pulse";
  const label = data.label || "Server";

  const cpu = data.cpu;
  const mem = data.mem;
  const disk = data.disk;
  const load = data.load;
  const uptime = data.uptime;
  const offline = tone === "offline";

  const showRings = size !== "xs";
  const showFooter = size === "md" || size === "lg";

  const ringRow = showRings && !offline ? `
    <div class="glances-rings">
      ${ringSvg({ pct: cpu, label: "CPU", color: "var(--accent-3)" })}
      ${ringSvg({ pct: mem, label: "RAM", color: "var(--accent-4)" })}
      ${ringSvg({ pct: disk?.percent, label: "Disk", color: "var(--accent-5)" })}
    </div>` : "";

  // Footer row: load + uptime as compact text strings on the same
  // line. These don't need rings — they're textual context, not
  // percentages.
  const footer = showFooter && !offline ? `
    <div class="glances-footer">
      <span class="glances-footer-cell"><span class="u-label">Load</span><span style="color:var(--accent-2);font-weight:var(--fw-black);font-variant-numeric:tabular-nums">${fmtLoad(load)}</span></span>
      <span class="glances-footer-cell"><span class="u-label">Uptime</span><span style="font-weight:var(--fw-black);font-variant-numeric:tabular-nums">${escapeHtml(fmtUptime(uptime))}</span></span>
    </div>` : "";

  const layout = `
    .glances-rings {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: var(--space-2);
      width: 100%;
    }
    .glances-ring-tile {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
      min-width: 0;
    }
    .glances-ring-wrap {
      width: clamp(3em, 18cqmin, 5em);
      aspect-ratio: 1;
    }
    .glances-ring-meta {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0;
      line-height: 1.05;
    }
    .glances-ring-label {
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      color: var(--text-secondary);
    }
    .glances-ring-sub {
      font-size: .7em;
      color: var(--text-muted);
      font-weight: var(--fw-semi);
    }
    .glances-footer {
      display: flex;
      gap: var(--space-3);
      justify-content: space-around;
      width: 100%;
    }
    .glances-footer-cell {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 2px;
      line-height: 1.05;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="glances_status">
      <div class="w-title">
        <i class="ph-bold ph-pulse" style="color:${accent}"></i>
        <h3>${escapeHtml(label)}</h3>
        ${data.time ? `<span class="w-title-meta">${escapeHtml(data.time)}</span>` : ""}
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ${stateIcon}" style="color:${accent}"></i>
          <div class="lockup">
            <span class="status-state">${offline ? "Offline" : fmtPct(cpu)}</span>
            <span class="status-sub">${offline ? "Unreachable" : "CPU"}</span>
          </div>
        </div>
        <span class="pill" style="background:${accent}">${escapeHtml(state.text)}</span>
        ${ringRow}
        ${footer}
      </div>
    </div>`;
}
