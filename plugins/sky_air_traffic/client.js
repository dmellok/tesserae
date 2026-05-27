// sky_air_traffic — flights overhead. Bauhaus shape: header bar with
// total count, lede hero for the closest flight (huge callsign, big
// plane icon rotated to heading, climb/descend marker, 4-up stats),
// then a compact list of the remaining flights. Every row's plane
// icon points along the flight's true track so the eye reads the
// traffic pattern at a glance.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function mToFt(m) { return m == null ? null : Math.round(m * 3.28084); }
function mpsToKt(v) { return v == null ? null : Math.round(v * 1.94384); }

// Track degrees -> 8-point cardinal label.
function cardinal(deg) {
  if (deg == null) return "";
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return dirs[Math.round(((deg % 360) / 45)) % 8];
}

// Vertical rate (m/s) -> {icon, label}. Anything within ±2 m/s of
// level we call "level"; bigger steps map to gentle / strong climb /
// descend so the icon picks up real phase-of-flight changes.
function verticalPhase(vr) {
  if (vr == null) return { icon: "ph-arrow-right", cls: "vr-level", label: "" };
  if (vr > 4)  return { icon: "ph-arrow-fat-up",   cls: "vr-up-strong",   label: "Climbing" };
  if (vr > 1)  return { icon: "ph-arrow-up",       cls: "vr-up",          label: "Climbing" };
  if (vr < -4) return { icon: "ph-arrow-fat-down", cls: "vr-down-strong", label: "Descending" };
  if (vr < -1) return { icon: "ph-arrow-down",     cls: "vr-down",        label: "Descending" };
  return { icon: "ph-arrow-right", cls: "vr-level", label: "Level" };
}

function shell(size, body) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/sky_air_traffic/client.css">
    <div class="root size-${size}">${body}</div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = shell(ctx.cell.size,
      `<div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>`);
    return;
  }
  const size = ctx.cell.size;
  const flights = Array.isArray(data.flights) ? data.flights : [];

  const bar = `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="at-title">Overhead · ${data.shown ?? flights.length} of ${data.count ?? flights.length}</span>
      <i class="ph-bold ph-airplane-tilt wb-bar-icon"></i>
    </header>
  `;

  if (!flights.length) {
    shadow.innerHTML = shell(size, `
      ${bar}
      <div class="at-empty">
        <i class="ph-duotone ph-cloud"></i>
        <div class="at-empty-primary">Empty skies</div>
        <div class="at-empty-secondary">No flights within ${data.radius} km.</div>
      </div>
    `);
    return;
  }

  const lede = flights[0];
  const rest = flights.slice(1);

  // ---- lede block ----
  const ledePhase = verticalPhase(lede.vertical_rate);
  const ledeAlt = mToFt(lede.altitude);
  const ledeKt = mpsToKt(lede.velocity);
  const ledeDir = cardinal(lede.track);
  const ledeHtml = `
    <section class="at-lede ${lede.on_ground ? 'is-ground' : ''}">
      <div class="at-lede-text">
        <div class="at-lede-call">${escapeHtml(lede.callsign || "—")}</div>
        <div class="at-lede-meta">
          <span class="at-country"><i class="ph-bold ph-globe-hemisphere-west"></i>${escapeHtml(lede.country || "—")}</span>
          ${ledePhase.label ? `<span class="at-phase ${ledePhase.cls}"><i class="ph-bold ${ledePhase.icon}"></i>${ledePhase.label}</span>` : ""}
        </div>
      </div>
      <div class="at-lede-icon" aria-hidden="true">
        <i class="ph-bold ph-airplane-tilt" style="transform: rotate(${(lede.track || 0) - 45}deg)"></i>
      </div>
    </section>
    <section class="at-lede-stats">
      <div class="at-stat at-stat--accent">
        <i class="ph-bold ph-arrows-vertical at-stat-icon"></i>
        <span class="at-stat-label">Altitude</span>
        <span class="at-stat-value">${ledeAlt ? ledeAlt.toLocaleString() : "—"}<small>ft</small></span>
      </div>
      <div class="at-stat at-stat--surface">
        <i class="ph-bold ph-gauge at-stat-icon"></i>
        <span class="at-stat-label">Speed</span>
        <span class="at-stat-value">${ledeKt != null ? ledeKt : "—"}<small>kt</small></span>
      </div>
      <div class="at-stat at-stat--accent2">
        <i class="ph-bold ph-compass at-stat-icon"></i>
        <span class="at-stat-label">Bearing</span>
        <span class="at-stat-value">${ledeDir || "—"}<small>${lede.track != null ? Math.round(lede.track) + "°" : ""}</small></span>
      </div>
      <div class="at-stat at-stat--accent3">
        <i class="ph-bold ph-map-pin at-stat-icon"></i>
        <span class="at-stat-label">Distance</span>
        <span class="at-stat-value">${lede.distance_km != null ? lede.distance_km : "—"}<small>km</small></span>
      </div>
    </section>
  `;

  // ---- subsequent rows ----
  const rowsHtml = rest.map((f) => {
    const ph = verticalPhase(f.vertical_rate);
    const alt = mToFt(f.altitude);
    const kt = mpsToKt(f.velocity);
    const dir = cardinal(f.track);
    return `
      <article class="at-row ${f.on_ground ? 'is-ground' : ''}">
        <i class="ph-bold ph-airplane-tilt at-row-icon" style="transform: rotate(${(f.track || 0) - 45}deg)" aria-hidden="true"></i>
        <div class="at-row-text">
          <div class="at-row-line">
            <span class="at-call">${escapeHtml(f.callsign || "—")}</span>
            <span class="at-country">${escapeHtml(f.country || "")}</span>
          </div>
          <div class="at-row-meta">
            <span><i class="ph-bold ph-arrows-vertical"></i>${alt ? alt.toLocaleString() + " ft" : "—"}</span>
            <span><i class="ph-bold ph-gauge"></i>${kt != null ? kt + " kt" : "—"}</span>
            <span><i class="ph-bold ph-compass"></i>${dir || "—"}</span>
            <span><i class="ph-bold ph-map-pin"></i>${f.distance_km != null ? f.distance_km + " km" : "—"}</span>
            ${ph.label ? `<span class="at-phase ${ph.cls}"><i class="ph-bold ${ph.icon}"></i>${ph.label}</span>` : ""}
          </div>
        </div>
      </article>
    `;
  }).join("");

  shadow.innerHTML = shell(size, `
    ${bar}
    ${ledeHtml}
    <section class="at-list">${rowsHtml}</section>
  `);
}
