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

// Render the current moon phase as a small SVG disc. ``fraction`` is
// 0 (new) → 0.5 (full) → 1 (next new). Drawn with a single white
// circle masked by an ellipse so a waxing crescent / gibbous reads
// like a real photograph of the moon at this phase.
function moonSvg(fraction, waxing, accent) {
  const r = 12;
  const k = Math.cos(fraction * 2 * Math.PI);
  const flag = waxing ? 1 : 0;
  // Draw a full disc, then a shadow piece to subtract the dark side.
  return `
    <svg viewBox="-15 -15 30 30" style="width:1.8em;height:1.8em;display:block">
      <circle r="${r}" fill="${accent}"/>
      <path fill="var(--surface-sunken)" d="M 0 -${r} A ${r} ${r} 0 1 ${flag} 0 ${r} A ${Math.abs(k * r)} ${r} 0 1 ${k >= 0 ? flag : 1 - flag} 0 -${r} Z"/>
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
        <div class="status-hero">
          <span>${moonIcon}</span>
          <div class="lockup">
            <span class="status-state">${escapeHtml(phase)}</span>
            <span class="status-sub">${illum != null ? `${illum}% illuminated` : ""}</span>
          </div>
        </div>
        <div class="status-grid">${grid}</div>
      </div>
    </div>`;
}
