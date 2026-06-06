// glances_status — Spectra status archetype. The hero shows CPU%
// as the headline number, with a tone-coded pill that swings ok →
// warn → danger based on the server's combined metric heuristic.
// At sm+ a status-grid carries the secondary stats (RAM, disk,
// load, uptime); xs drops everything below the hero.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const TONE_ACCENT = {
  ok: "var(--accent-3)",       // forest / moss — calm
  warn: "var(--accent-2)",     // ochre / mustard
  danger: "var(--accent-1)",   // terracotta
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
  // Two decimal places at small values, one at high — matches how
  // load averages read on a typical Linux shell.
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

  // At xs we hand the user one number — CPU% — and a state pill.
  // Everything else fights for space and reads as noise on a small
  // tile. md/lg get the full metric grid; sm drops uptime + load to
  // keep the grid readable at 380×240.
  const showRam = size !== "xs";
  const showDisk = size !== "xs";
  const showLoad = size === "md" || size === "lg";
  const showUptime = size === "md" || size === "lg";

  // Build the secondary metric grid. Each cell is (label, value,
  // colour). Cells colour by accent role so the eye reads the same
  // family at a glance — RAM uses accent-4 (teal), disk accent-5
  // (slate-blue), load accent-2 (ochre), uptime stays in muted text
  // because it's contextual not actionable.
  const cells = [];
  if (showRam) cells.push(["RAM", fmtPct(mem), "var(--accent-4)"]);
  if (showDisk) cells.push([
    "Disk",
    disk ? `${fmtPct(disk.percent)}` : "—",
    "var(--accent-5)",
  ]);
  if (showLoad) cells.push(["Load 1m", fmtLoad(load), "var(--accent-2)"]);
  if (showUptime) cells.push(["Uptime", fmtUptime(uptime), "var(--text-primary)"]);

  const grid = cells.length
    ? `<div class="status-grid">${cells.map(([l, v, c]) => `
        <div class="status-cell">
          <span class="u-label">${escapeHtml(l)}</span>
          <span class="v" style="color:${c}">${v}</span>
        </div>`).join("")}</div>`
    : "";

  shadow.innerHTML = `
    ${css}
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
        ${grid}
      </div>
    </div>`;
}
