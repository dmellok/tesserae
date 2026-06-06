// public_transport_times, Spectra list archetype. Each upcoming
// departure is a row with a mode icon (train / tram / bus / V/Line /
// night bus), a route-number colour chip (PTV line colour when the
// server forwards one, else a hash-stable accent per route), the
// direction name, and the minutes-until-departure with a countdown
// ring on the next (closest) departure.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// PTV route_type → glyph. Train (0) and V/Line (3) get the heavy-rail
// train icon; tram (1) gets ph-tram, bus (2) gets ph-bus, night-bus
// (4) gets ph-moon-stars riffing on the after-hours service.
const ROUTE_TYPE_PH = {
  0: "ph-train",
  1: "ph-tram",
  2: "ph-bus",
  3: "ph-train-simple",
  4: "ph-moon-stars",
};

function modeIcon(rt) {
  return ROUTE_TYPE_PH[Number(rt)] || "ph-bus";
}

// Hash a route number/name to one of six categorical accents so two
// rows on different lines pick distinct chip colours stably across
// renders. PTV-specific line colours would be nicer but require a
// per-line lookup table the server doesn't currently ship.
function routeAccent(routeName) {
  const s = String(routeName || "").toLowerCase();
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  const accents = ["var(--accent-1)", "var(--accent-2)", "var(--accent-3)", "var(--accent-4)", "var(--accent-5)", "var(--accent-6)"];
  return accents[Math.abs(h) % accents.length];
}

function minsUntil(iso) {
  if (typeof iso !== "string" || !iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  return Math.round((t - Date.now()) / 60000);
}

function fmtMins(m) {
  if (m == null) return "-";
  if (m <= 0) return "NOW";
  if (m === 1) return "1m";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h}h${r}m` : `${h}h`;
}

// Countdown ring for the next departure. Renders a circle whose
// stroke-dasharray shrinks as the time-until approaches 0. Caps the
// "full" position at 30 minutes so a departure 90 minutes out doesn't
// look identical to one 15 minutes out, both still register as
// "soon enough" on the ring.
function countdownRingSvg({ mins, color }) {
  if (!Number.isFinite(mins) || mins < 0) return "";
  const RING_CAP = 30;
  const fraction = Math.max(0, Math.min(1, 1 - mins / RING_CAP));
  const r = 12;
  const circ = 2 * Math.PI * r;
  const filled = circ * fraction;
  return `
    <svg viewBox="-15 -15 30 30" aria-hidden="true"
         style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none">
      <g transform="rotate(-90)">
        <circle r="${r}" fill="none" stroke="color-mix(in oklab, var(--text-primary) 12%, transparent)" stroke-width="2"/>
        <circle r="${r}" fill="none" stroke="${color}" stroke-width="2.4"
                stroke-dasharray="${filled.toFixed(2)} ${circ.toFixed(2)}"
                stroke-linecap="round"/>
      </g>
    </svg>`;
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
  const routeType = data.route_type;

  if (departures.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="public_transport_times">
        <div class="w-title"><i class="ph-bold ${modeIcon(routeType)}" style="color:var(--accent-4)"></i><h3>${escapeHtml(stopName)}</h3></div>
        <div class="w-body"><p class="u-muted">No upcoming departures.</p></div>
      </div>`;
    return;
  }

  const rows = departures.map((d, i) => {
    const iso = d.estimated || d.scheduled;
    const mins = minsUntil(iso);
    const atPlatform = d.at_platform || mins === 0;
    const isNext = i === 0;
    const routeName = d.route_number || d.route_name || "-";
    // Server may include a `route_color` (e.g. PTV gives "#152C6B"
    // for the Frankston line). Use it when present; fall back to a
    // hash-stable accent token.
    const chipColor = d.route_color || routeAccent(routeName);
    const isHex = typeof chipColor === "string" && chipColor.startsWith("#");

    const ring = isNext ? countdownRingSvg({ mins, color: atPlatform ? "var(--accent-3)" : "var(--accent-4)" }) : "";
    const minsAccent = atPlatform ? "var(--accent-3)" : isNext ? "var(--accent-4)" : "var(--text-secondary)";

    return `
      <div class="pt-row ${i % 2 ? "is-zebra" : ""}${isNext ? " is-next" : ""}">
        <div class="list-lead pt-row-lead">
          <span class="pt-mode-wrap">
            <i class="ph-bold ${modeIcon(routeType)}"></i>
            ${ring}
          </span>
          <span class="pt-route-chip" style="background:${chipColor};${isHex ? "color:#fff" : ""}">${escapeHtml(routeName)}</span>
          ${d.direction_name ? `<span class="pt-direction">${escapeHtml(d.direction_name)}</span>` : ""}
        </div>
        <span class="pt-mins" style="color:${minsAccent}">
          ${escapeHtml(fmtMins(mins))}
          ${d.platform ? `<small class="pt-platform">P${escapeHtml(d.platform)}</small>` : ""}
        </span>
      </div>`;
  }).join("");

  const layout = `
    .pt-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-2) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .pt-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .pt-row.is-next {
      background: color-mix(in oklab, var(--accent-4) 6%, transparent);
    }
    .pt-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .pt-mode-wrap {
      position: relative;
      width: 1.6em;
      height: 1.6em;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--accent-4);
      flex: 0 0 auto;
    }
    .pt-mode-wrap i { font-size: 1.05em; }
    .pt-row.is-next .pt-mode-wrap { color: var(--accent-4); }
    .pt-route-chip {
      display: inline-flex;
      align-items: center;
      padding: 1px var(--space-1);
      border-radius: 4px;
      color: var(--surface);
      font-weight: var(--fw-black);
      font-size: var(--fs-caption);
      letter-spacing: var(--ls-label);
      font-variant-numeric: tabular-nums;
      flex: 0 0 auto;
      min-width: 1.6em;
      justify-content: center;
    }
    .pt-direction {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
      flex: 1 1 auto;
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
    }
    .pt-mins {
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      flex: 0 0 auto;
    }
    .pt-platform {
      font-size: .7em;
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      margin-left: var(--space-1);
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="public_transport_times">
      <div class="w-title">
        <i class="ph-bold ${modeIcon(routeType)}" style="color:var(--accent-4)"></i>
        <h3>${escapeHtml(stopName)}</h3>
        <span class="w-title-meta">${departures.length}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
