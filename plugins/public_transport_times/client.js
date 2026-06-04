// public_transport_times — Spectra list archetype. Each upcoming
// departure is a zebra row: mode icon (train / bus / tram / etc) +
// route number + direction as the title, time-from-now as the meta
// column. Routes "at platform" pick up accent-3 so the next-to-board
// pops.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const ROUTE_TYPE_PH = {
  0: "ph-train",
  1: "ph-train",
  2: "ph-train",
  3: "ph-bus",
  4: "ph-tram",
};

function modeIcon(rt) {
  return ROUTE_TYPE_PH[Number(rt)] || "ph-bus";
}

function minsUntil(iso) {
  if (typeof iso !== "string" || !iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  return Math.round((t - Date.now()) / 60000);
}

function fmtMins(m) {
  if (m == null) return "—";
  if (m <= 0) return "NOW";
  if (m === 1) return "1 min";
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h}h${r}m` : `${h}h`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="public_transport_times">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Transit</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const departures = Array.isArray(data.departures) ? data.departures : [];
  const stopName = data.stop_name || "Stop";

  if (departures.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="public_transport_times">
        <div class="w-title"><i class="ph-bold ${modeIcon(data.route_type)}" style="color:var(--accent-4)"></i><h3>${escapeHtml(stopName)}</h3></div>
        <div class="w-body"><p class="u-muted">No upcoming departures.</p></div>
      </div>`;
    return;
  }

  const rows = departures.map((d, i) => {
    const atPlatform = d.at_platform;
    const accent = atPlatform ? "var(--accent-3)" : "var(--accent-4)";
    const iso = d.estimated || d.scheduled;
    const mins = minsUntil(iso);
    const meta = `${escapeHtml(fmtMins(mins))}${d.platform ? `<small style="font-size:.7em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.3em">P${escapeHtml(d.platform)}</small>` : ""}`;
    const routeName = d.route_number || d.route_name || "—";
    const dirBit = d.direction_name ? `<small class="u-muted" style="font-weight:var(--fw-semi);font-size:.7em;margin-left:.4em">${escapeHtml(d.direction_name)}</small>` : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${modeIcon(data.route_type)}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(routeName)}${dirBit}</span>
        </div>
        <span class="list-meta" style="color:${accent};font-weight:var(--fw-black)">${meta}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="public_transport_times">
      <div class="w-title">
        <i class="ph-bold ${modeIcon(data.route_type)}" style="color:var(--accent-4)"></i>
        <h3>${escapeHtml(stopName)}</h3>
        <span class="w-title-meta">${departures.length}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
