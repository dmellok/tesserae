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
// 0.5 (full) → 1 (next new). Five fixed crater dots at --text-muted
// give the disc weight at large sizes; a subtle accent-tinted halo
// ring sits just outside the edge. No fine gradients — Spectra-spec
// safe on e-ink.
function moonSvg(fraction, waxing, accent) {
  const r = 12;
  const k = Math.cos(fraction * 2 * Math.PI);
  const flag = waxing ? 1 : 0;
  // Fixed crater layout — same positions every phase so the moon
  // reads as a body, not a flat disc. Sized to be visible at the
  // larger clamp scales without becoming dust at the smaller ones.
  const craters = [
    [-3, -4, 1.4], [4, 1, 1.0], [-1, 5, 1.2], [5, -5, 0.7], [-5, 2, 0.8],
  ];
  const cratersSvg = craters.map(([cx, cy, cr]) =>
    `<circle cx="${cx}" cy="${cy}" r="${cr}" fill="var(--text-muted)" opacity="0.25"/>`
  ).join("");
  return `
    <svg viewBox="-15 -15 30 30" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block">
      <circle r="${r + 1.4}" fill="none" stroke="${accent}" stroke-width="0.6" opacity="0.22"/>
      <circle r="${r}" fill="${accent}"/>
      ${cratersSvg}
      <path fill="var(--surface-sunken)"
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

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="sky_moon">
      <div class="w-title">
        <i class="ph-bold ph-moon" style="color:var(--accent-5)"></i>
        <h3>${escapeHtml(place)}</h3>
      </div>
      <div class="w-body status-body">
        <!-- Big moon as the centrepiece. Width clamps so a small tile
             still gets a recognisable phase disc and a large panel
             really shows it off. -->
        <div style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:var(--space-2)">
          <div style="width:clamp(5em, 40cqmin, 12em);aspect-ratio:1;flex:0 0 auto">${moonIcon}</div>
          <span style="font-size:var(--fs-jumbo);font-weight:var(--fw-black);line-height:var(--lh-tight);color:var(--text-primary)">${escapeHtml(phase)}</span>
          ${illum != null ? `<span style="font-size:var(--fs-body);font-weight:var(--fw-semi);color:var(--text-secondary)">${escapeHtml(String(illum))}% illuminated</span>` : ""}
        </div>
        <div class="status-grid">${grid}</div>
      </div>
    </div>`;
}
