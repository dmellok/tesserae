// sky_moon — Spectra status archetype with an inline SVG of the
// current moon disc. Hero is the phase name + illumination percent;
// status-grid stacks sunrise / sunset / moonrise / moonset.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTime(iso) {
  if (typeof iso !== "string" || !iso) return "—";
  if (iso.includes("T")) return iso.split("T")[1].slice(0, 5);
  return iso;
}

// Render the current moon phase as a stylised SVG disc that scales
// to whatever its container ends up at. ``fraction`` is 0 (new) →
// 0.5 (full) → 1 (next new).
//
// Colours are **hardcoded**, not theme tokens. The moon is a real-
// world object with a real colour; binding the disc to
// ``--text-primary`` flipped the disc/shadow relationship per theme
// (light themes painted the disc dark + shadow light — a "negative
// space" moon — while dark themes painted the disc light + shadow
// dark — a realistic moon). The hardcoded cream disc + dark warm
// shadow give the same realistic moon shape on every theme. The halo
// ring around the edge keeps the theme accent so the widget still
// integrates colour-wise with whatever palette is active.
function moonSvg(fraction, waxing, accent) {
  const r = 12;
  const k = Math.cos(fraction * 2 * Math.PI);
  const flag = waxing ? 1 : 0;
  const DISC = "#EAD9A6";       // warm cream — the lit lunar surface
  const SHADOW = "#1B1612";     // dark warm — the unlit side
  const CRATER = "#8A6F4E";     // muted warm shadow inside the disc
  // Fixed crater layout — same positions every phase so the moon
  // reads as a body, not a flat disc. Sized to be visible at the
  // larger clamp scales without becoming dust at the smaller ones.
  const craters = [
    [-3, -4, 1.4], [4, 1, 1.0], [-1, 5, 1.2], [5, -5, 0.7], [-5, 2, 0.8],
  ];
  const cratersSvg = craters.map(([cx, cy, cr]) =>
    `<circle cx="${cx}" cy="${cy}" r="${cr}" fill="${CRATER}" opacity="0.35"/>`
  ).join("");
  return `
    <svg viewBox="-15 -15 30 30" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block">
      <circle r="${r + 1.4}" fill="none" stroke="${accent}" stroke-width="0.6" opacity="0.22"/>
      <circle r="${r}" fill="${DISC}"/>
      ${cratersSvg}
      <path fill="${SHADOW}"
            d="M 0 -${r} A ${r} ${r} 0 1 ${flag} 0 ${r} A ${Math.abs(k * r)} ${r} 0 1 ${k >= 0 ? flag : 1 - flag} 0 -${r} Z"/>
    </svg>`;
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
  const phase = data.phase_name || "—";
  const illum = data.illumination;
  const fraction = data.fraction != null ? Number(data.fraction) : 0;
  const waxing = data.waxing !== false;
  const moonIcon = moonSvg(fraction, waxing, "var(--text-primary)");

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

  // Style block kept inline alongside the markup so the layout +
  // sizing live in one place. The centrepiece switches between
  // "moon stacked above phase name" (taller cells) and "moon left,
  // lockup right" (wider cells) via a container query, and the phase
  // name itself is clamp()'d against the container width so jumbo
  // text doesn't overflow at md and clip the title bar.
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
    /* Wide-and-short cells (md landscape): put the moon disc on the
       left and the lockup beside it so the phase name has room to
       breathe instead of fighting the status grid below for height. */
    @container (min-aspect-ratio: 1.4) {
      .moon-center { flex-direction: row; gap: var(--space-4); }
      .moon-disc { width: clamp(4em, 30cqmin, 9em); }
      .moon-lockup { align-items: flex-start; text-align: left; }
      .moon-phase { text-align: left; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="sky_moon">
      <div class="w-title">
        <i class="ph-bold ph-moon" style="color:var(--accent-5)"></i>
        <h3>${escapeHtml(place)}</h3>
      </div>
      <div class="w-body status-body">
        <div class="moon-center">
          <div class="moon-disc">${moonIcon}</div>
          <div class="moon-lockup">
            <span class="moon-phase">${escapeHtml(phase)}</span>
            ${illum != null ? `<span class="moon-illum">${escapeHtml(String(illum))}% illuminated</span>` : ""}
          </div>
        </div>
        <div class="status-grid">${grid}</div>
      </div>
    </div>`;
}
