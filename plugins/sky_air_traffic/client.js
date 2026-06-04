// sky_air_traffic — Spectra list archetype. Each flight is a zebra
// row with an airplane-tilt icon (accent-4 for in-air, muted for
// on-ground), the callsign as the title, and altitude / distance as
// meta. Title meta shows the in-radius count.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtAlt(m) {
  if (m == null) return "—";
  const v = Number(m);
  if (!Number.isFinite(v)) return "—";
  return `${Math.round(v / 100) / 10}k`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_air_traffic">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Air Traffic</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const flights = Array.isArray(data.flights) ? data.flights : [];

  if (flights.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_air_traffic">
        <div class="w-title"><i class="ph-bold ph-airplane-tilt" style="color:var(--accent-4)"></i><h3>Air Traffic</h3></div>
        <div class="w-body"><p class="u-muted">No flights nearby.</p></div>
      </div>`;
    return;
  }

  const rows = flights.map((f, i) => {
    const inAir = !f.on_ground;
    const accent = inAir ? "var(--accent-4)" : "var(--text-muted)";
    const ph = inAir ? "ph-airplane-tilt" : "ph-airplane-landing";
    // Rotate the airplane icon by the heading so the row carries a
    // sense of direction. ph-airplane-tilt points up-right (~45°);
    // subtract 45 so the icon's natural orientation matches a 0°
    // track (north).
    const rot = Number.isFinite(f.track) ? (f.track - 45) : 0;
    const alt = `${fmtAlt(f.altitude)}m`;
    const dist = f.distance_km != null ? `${f.distance_km}km` : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent};transform:rotate(${rot}deg)"></i>
          <span class="list-title">${escapeHtml(f.callsign || "—")}<small class="u-muted" style="font-weight:var(--fw-semi);font-size:.7em;margin-left:.4em">${escapeHtml(f.country || "")}</small></span>
        </div>
        <span class="list-meta" style="color:${accent}">${alt}${dist ? `<small style="font-size:.7em;color:var(--text-muted);font-weight:var(--fw-semi);margin-left:.3em">${dist}</small>` : ""}</span>
      </div>`;
  }).join("");

  const totalMeta = (data.count != null) ? `${data.shown ?? flights.length}/${data.count}` : `${flights.length}`;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="sky_air_traffic">
      <div class="w-title">
        <i class="ph-bold ph-airplane-tilt" style="color:var(--accent-4)"></i>
        <h3>Air Traffic</h3>
        <span class="w-title-meta">${escapeHtml(totalMeta)}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
