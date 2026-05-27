// sky_air_traffic — nearest flights overhead.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function mToFt(m) { return m == null ? null : Math.round(m * 3.28084); }
function mpsToKt(v) { return v == null ? null : Math.round(v * 1.94384); }

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/sky_air_traffic/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const flights = Array.isArray(data.flights) ? data.flights : [];

  if (!flights.length) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
      <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
      <link rel="stylesheet" href="/plugins/sky_air_traffic/client.css">
      <div class="root size-${size}">
        <header class="wb-bar">
          <span class="wb-mark"></span>
          <span class="at-title">Overhead</span>
          <i class="ph-bold ph-airplane-tilt wb-bar-icon"></i>
        </header>
        <div class="at-empty">
          <i class="ph-duotone ph-cloud"></i>
          <div class="at-empty-primary">Empty skies</div>
          <div class="at-empty-secondary">No flights within ${data.radius} km.</div>
        </div>
      </div>
    `;
    return;
  }

  const rows = flights.map((f) => `
    <div class="at-row ${f.on_ground ? 'is-ground' : ''}">
      <i class="ph-bold ph-airplane-tilt at-icon" style="transform: rotate(${(f.track || 0) - 45}deg)"></i>
      <span class="at-call">${escapeHtml(f.callsign || "—")}</span>
      <span class="at-country">${escapeHtml(f.country || "")}</span>
      <span class="at-alt">${mToFt(f.altitude) ? mToFt(f.altitude).toLocaleString() + " ft" : "—"}</span>
      <span class="at-spd">${mpsToKt(f.velocity) ? mpsToKt(f.velocity) + " kt" : "—"}</span>
      <span class="at-dist">${f.distance_km} km</span>
    </div>
  `).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/sky_air_traffic/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark"></span>
        <span class="at-title">Overhead · ${data.shown} of ${data.count}</span>
        <i class="ph-bold ph-airplane-tilt wb-bar-icon"></i>
      </header>
      <section class="at-list">${rows}</section>
    </div>
  `;
}
