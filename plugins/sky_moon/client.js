// sky_moon, Spectra status archetype with an inline SVG of the
// current moon disc. The disc has fixed craters masked to only the
// lit side (so the unlit side reads as truly dark), a phase-progress
// arc circling the disc to show how far through the synodic cycle
// we are, and a coloured halo. Hero is the phase name + illumination
// percent; a "Next ⏵ Full Moon · Sat 8 Jun" chip sits beneath; the
// status-grid stacks sunrise / sunset / moonrise / moonset.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(iso) {
  if (typeof iso !== "string" || !iso) return "-";
  if (iso.includes("T")) return iso.split("T")[1].slice(0, 5);
  return iso;
}

// Render the current moon phase as a stylised SVG disc that scales
// to whatever its container ends up at. ``fraction`` is 0 (new) →
// 0.5 (full) → 1 (next new). Adds:
//  - a phase-progress ring around the disc (accent stroke at the
//    same fraction of a circle)
//  - craters masked to the lit side only via a clip-path
function moonSvg(fraction, waxing, accent) {
  const r = 12;
  const k = Math.cos(fraction * 2 * Math.PI);
  const flag = waxing ? 1 : 0;
  const DISC = "#EAD9A6";
  const SHADOW = "#1B1612";
  const CRATER = "#8A6F4E";
  const craters = [
    [-3, -4, 1.4], [4, 1, 1.0], [-1, 5, 1.2], [5, -5, 0.7], [-5, 2, 0.8],
  ];
  const cratersSvg = craters.map(([cx, cy, cr]) =>
    `<circle cx="${cx}" cy="${cy}" r="${cr}" fill="${CRATER}" opacity="0.4"/>`
  ).join("");

  const shadowPath = `M 0 -${r} A ${r} ${r} 0 1 ${flag} 0 ${r} A ${Math.abs(k * r)} ${r} 0 1 ${k >= 0 ? flag : 1 - flag} 0 -${r} Z`;

  // Progress ring, a circle stroked with dasharray tuned to the
  // cycle fraction. circumference = 2πr at radius 14.5.
  const ringR = 14.5;
  const circ = 2 * Math.PI * ringR;
  const filled = circ * fraction;

  return `
    <svg viewBox="-17 -17 34 34" preserveAspectRatio="xMidYMid meet"
         style="width:100%;height:100%;display:block">
      <defs>
        <!-- Clip path = the disc minus the shadow shape, so craters
             only render on the lit side. -->
        <clipPath id="lit-side">
          <path d="M ${-r} 0 a ${r} ${r} 0 1 0 ${r * 2} 0 a ${r} ${r} 0 1 0 ${-r * 2} 0 z
                   ${shadowPath}" clip-rule="evenodd"/>
        </clipPath>
      </defs>
      <!-- Progress ring around the disc, full circle in muted, with
           the cycle progress overlaid in the active accent. Rotated
           -90deg so 0% sits at the top (12 o'clock). -->
      <g transform="rotate(-90)">
        <circle r="${ringR}" fill="none" stroke="var(--surface-sunken)" stroke-width="1.6"/>
        <circle r="${ringR}" fill="none" stroke="${accent}" stroke-width="1.6"
                stroke-dasharray="${filled.toFixed(2)} ${circ.toFixed(2)}"
                stroke-linecap="round"/>
      </g>
      <!-- Disc base -->
      <circle r="${r}" fill="${DISC}"/>
      <!-- Craters, clipped to the lit side -->
      <g clip-path="url(#lit-side)">${cratersSvg}</g>
      <!-- Shadow (unlit portion) -->
      <path fill="${SHADOW}" d="${shadowPath}"/>
    </svg>`;
}

// Parse "Next full moon · Sat 8 Jun" → "Sat 8 Jun" + a target Date
// computed from the underlying ISO so we can render a countdown.
function nextPhaseInfo(moon) {
  if (!moon) return null;
  const phases = [
    { label: "New Moon", iso: moon.next_new || null },
    { label: "First Quarter", iso: moon.next_first_quarter || null },
    { label: "Full Moon", iso: moon.next_full || null },
    { label: "Last Quarter", iso: moon.next_last_quarter || null },
  ].filter((p) => p.iso);
  if (!phases.length) return null;
  let best = null;
  for (const p of phases) {
    const t = Date.parse(p.iso);
    if (!Number.isFinite(t)) continue;
    const delta = t - Date.now();
    if (delta <= 0) continue;
    if (!best || delta < best.delta) best = { ...p, delta, t };
  }
  return best;
}

function fmtCountdown(deltaMs) {
  if (!Number.isFinite(deltaMs) || deltaMs <= 0) return "now";
  const days = Math.floor(deltaMs / 86_400_000);
  if (days >= 2) return `${days}d`;
  const hours = Math.floor(deltaMs / 3_600_000);
  if (hours >= 2) return `${hours}h`;
  const minutes = Math.max(1, Math.floor(deltaMs / 60_000));
  return `${minutes}m`;
}

function fmtDate(iso) {
  if (typeof iso !== "string") return "";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "";
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${days[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]}`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_moon">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Sun &amp; Moon</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const place = data.place || data.label || "Sun & Moon";
  const phase = data.phase_name || "-";
  const illum = data.illumination;
  const fraction = data.fraction != null ? Number(data.fraction) : 0;
  const waxing = data.waxing !== false;
  const next = nextPhaseInfo(data);
  const moonIcon = moonSvg(fraction, waxing, "var(--accent-2)");

  const cells = [
    ["Sunrise", fmtTime(data.sunrise), "var(--accent-2)"],
    ["Sunset", fmtTime(data.sunset), "var(--accent-1)"],
    ["Moonrise", fmtTime(data.moonrise), "var(--text-secondary)"],
    ["Moonset", fmtTime(data.moonset), "var(--text-secondary)"],
  ];

  const grid = cells.map(([label, value, c]) => `
    <div class="status-cell">
      <span class="u-label">${escapeHtml(label)}</span>
      <span class="v" style="color:${c}">${escapeHtml(value)}</span>
    </div>`).join("");

  const nextChip = next
    ? `<div class="moon-next-row">
        <span class="moon-next-chip">
          <i class="ph-bold ${next.label === "Full Moon" ? "ph-moon" : next.label === "New Moon" ? "ph-circle" : "ph-moon-stars"}"></i>
          ${escapeHtml(next.label)}
        </span>
        <small class="moon-next-when">
          <span class="moon-next-date">${escapeHtml(fmtDate(next.iso))}</span>
          <span class="moon-next-count">${escapeHtml(fmtCountdown(next.delta))}</span>
        </small>
      </div>`
    : "";

  const layout = `
    .moon-center {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--space-2);
    }
    .moon-disc {
      width: clamp(5em, 38cqmin, 11em);
      aspect-ratio: 1;
      flex: 0 0 auto;
    }
    .moon-lockup {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--space-1);
      min-width: 0;
      max-width: 100%;
    }
    .moon-phase {
      font-size: clamp(1.4em, 9cqw, 2.5em);
      font-weight: var(--fw-black);
      line-height: var(--lh-tight);
      color: var(--text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 100%;
      text-align: center;
    }
    .moon-illum {
      font-size: var(--fs-body);
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
    }
    .moon-next-row {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      flex-wrap: wrap;
      justify-content: center;
    }
    .moon-next-chip {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 1px var(--space-2);
      border-radius: 999px;
      background: color-mix(in oklab, var(--accent-2) 14%, var(--surface));
      color: var(--accent-2);
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
    }
    .moon-next-when {
      display: inline-flex;
      align-items: center;
      gap: var(--space-1);
      font-size: var(--fs-caption);
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
    }
    .moon-next-count {
      color: var(--accent-1);
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
    }
    @container (min-aspect-ratio: 1.4) {
      .moon-center { flex-direction: row; gap: var(--space-4); }
      .moon-disc { width: clamp(4em, 30cqmin, 9em); }
      .moon-lockup { align-items: flex-start; text-align: left; }
      .moon-phase { text-align: left; }
      .moon-next-row { justify-content: flex-start; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="sky_moon">
      <div class="w-title">
        <i class="ph-bold ph-moon" style="color:var(--accent-2)"></i>
        <h3>${escapeHtml(place)}</h3>
      </div>
      <div class="w-body status-body">
        <div class="moon-center">
          <div class="moon-disc">${moonIcon}</div>
          <div class="moon-lockup">
            <span class="moon-phase">${escapeHtml(phase)}</span>
            ${illum != null ? `<span class="moon-illum">${escapeHtml(String(illum))}% illuminated</span>` : ""}
            ${nextChip}
          </div>
        </div>
        <div class="status-grid">${grid}</div>
      </div>
    </div>`;
}
