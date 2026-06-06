// sky_air_traffic, Spectra status archetype with a radar-style
// hero. SVG dial with concentric range rings, the user at the
// centre, and each nearby flight plotted by its bearing + distance.
// Below the radar, the flight list carries an altitude bar per row
// (vertical mini-bar scaled to a 12 km cruising ceiling), an
// airplane glyph rotated by heading, the callsign, and distance.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtAlt(m) {
  if (m == null) return "-";
  const v = Number(m);
  if (!Number.isFinite(v)) return "-";
  return `${(v / 1000).toFixed(1)}km`;
}

// Initial bearing (forward azimuth) from point 1 to point 2, in
// degrees from north, clockwise.
function bearingDeg(lat1, lon1, lat2, lon2) {
  const toRad = Math.PI / 180;
  const φ1 = lat1 * toRad;
  const φ2 = lat2 * toRad;
  const Δλ = (lon2 - lon1) * toRad;
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// Radar SVG. Centre = user's location; rings at 25/50/75/100% of
// the search radius; each flight is a small triangle pointing in
// its heading direction, placed at its (bearing, distance) polar
// coordinate.
function radarSvg({ centerLat, centerLon, radius, flights }) {
  const W = 240;
  const H = 240;
  const cx = W / 2;
  const cy = H / 2;
  const R = Math.min(cx, cy) - 16;
  const rings = [];
  for (let i = 1; i <= 4; i++) {
    const r = (R * i) / 4;
    // Inner rings use --text-secondary at increasing stroke so the
    // radar reads as a real chart, not a faint guideline. Outer
    // ring at 2.5px is bold enough to anchor the eye against any
    // theme; inner rings step down but stay readable.
    const sw = i === 4 ? 2.5 : i === 3 ? 1.6 : 1.2;
    rings.push(`<circle cx="${cx}" cy="${cy}" r="${r.toFixed(1)}"
      fill="none" stroke="var(--text-secondary)" stroke-width="${sw}"
      opacity="${i === 4 ? 0.7 : 0.45}"/>`);
  }
  // Cardinal direction ticks.
  const cardinals = [
    { angle: 0, label: "N" },
    { angle: 90, label: "E" },
    { angle: 180, label: "S" },
    { angle: 270, label: "W" },
  ].map(({ angle, label }) => {
    const rad = ((angle - 90) * Math.PI) / 180;
    const x = cx + Math.cos(rad) * (R + 11);
    const y = cy + Math.sin(rad) * (R + 11);
    return `<text x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}"
            text-anchor="middle" font-size="12" font-weight="900"
            fill="var(--text-primary)" font-family="var(--font-family)">${label}</text>`;
  }).join("");
  // Centre pin, chunky concentric rings so "you are here" reads
  // unambiguously even against the radar's coloured plane dots.
  const centre = `
    <circle cx="${cx}" cy="${cy}" r="5" fill="var(--accent-1)"/>
    <circle cx="${cx}" cy="${cy}" r="10" fill="none" stroke="var(--accent-1)" stroke-width="2" opacity="0.65"/>`;
  // Per-flight triangle, rotated by heading.
  const planes = flights.map((f, i) => {
    const dist = Number(f.distance_km) || 0;
    if (dist <= 0 || dist > radius) return "";
    const bearing = (Number.isFinite(f.lat) && Number.isFinite(f.lon))
      ? bearingDeg(centerLat, centerLon, f.lat, f.lon)
      : 0;
    const r = (dist / radius) * R;
    // SVG y is flipped, turn the bearing into screen coords with -90 offset.
    const θ = ((bearing - 90) * Math.PI) / 180;
    const x = cx + Math.cos(θ) * r;
    const y = cy + Math.sin(θ) * r;
    const heading = Number.isFinite(f.track) ? f.track : 0;
    const onGround = !!f.on_ground;
    const fill = i === 0 ? "var(--accent-1)" : onGround ? "var(--text-muted)" : "var(--accent-4)";
    // Bigger plane triangle + white halo so the dots pop off the
    // bolder rings.
    return `
      <g transform="translate(${x.toFixed(1)}, ${y.toFixed(1)}) rotate(${heading.toFixed(0)})">
        <polygon points="0,-8 6,6 0,3 -6,6" fill="${fill}"
                 stroke="var(--surface)" stroke-width="1.2"/>
      </g>`;
  }).join("");

  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
         width="100%" height="100%" aria-hidden="true">
      ${rings.join("")}
      ${cardinals}
      ${centre}
      ${planes}
    </svg>`;
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
  const radius = Number(data.radius) || 60;
  const centerLat = Number(data.lat) || 0;
  const centerLon = Number(data.lon) || 0;

  if (flights.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_air_traffic">
        <div class="w-title"><i class="ph-bold ph-airplane-tilt" style="color:var(--accent-4)"></i><h3>Air Traffic</h3></div>
        <div class="w-body"><p class="u-muted">No flights nearby.</p></div>
      </div>`;
    return;
  }

  const radar = radarSvg({ centerLat, centerLon, radius, flights });

  // Altitude reference for the per-row bar. 12 km is a typical
  // upper-cruising ceiling, so a CRJ at 10k reads as a tall bar.
  const ALT_CEIL = 12000;

  const rows = flights.map((f, i) => {
    const inAir = !f.on_ground;
    const accent = i === 0 ? "var(--accent-1)" : inAir ? "var(--accent-4)" : "var(--text-muted)";
    const ph = inAir ? "ph-airplane-tilt" : "ph-airplane-landing";
    const rot = Number.isFinite(f.track) ? f.track - 45 : 0;
    const altPct = Math.max(2, Math.min(100, ((Number(f.altitude) || 0) / ALT_CEIL) * 100));
    return `
      <div class="at-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead at-row-lead">
          <i class="ph-bold ${ph}" style="color:${accent};transform:rotate(${rot}deg)"></i>
          <span class="list-title">${escapeHtml(f.callsign || "-")}<small class="at-country">${escapeHtml(f.country || "")}</small></span>
        </div>
        <div class="at-meta">
          <span class="at-alt-bar" title="${escapeHtml(fmtAlt(f.altitude))}">
            <span class="at-alt-fill" style="height:${altPct.toFixed(0)}%;background:${accent}"></span>
          </span>
          <span class="at-alt-text" style="color:${accent}">${escapeHtml(fmtAlt(f.altitude))}</span>
          ${f.distance_km != null ? `<small class="at-dist">${escapeHtml(f.distance_km + "km")}</small>` : ""}
        </div>
      </div>`;
  }).join("");

  const totalMeta = data.count != null
    ? `${data.shown ?? flights.length}/${data.count}`
    : `${flights.length}`;

  const layout = `
    .at-radar {
      flex: 0 0 auto;
      width: 100%;
      max-height: 14em;
      display: flex;
      justify-content: center;
      padding: var(--space-1) 0;
    }
    .at-radar svg {
      width: auto;
      max-width: 100%;
      max-height: 14em;
    }
    .at-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-2);
      padding: var(--space-1) var(--space-3);
      border-radius: var(--radius-1);
      min-width: 0;
    }
    .at-row.is-zebra {
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
    }
    .at-row-lead {
      flex: 1 1 auto;
      min-width: 0;
      gap: var(--space-2);
    }
    .at-row-lead .list-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .at-country {
      font-weight: var(--fw-semi);
      font-size: .7em;
      margin-left: .4em;
      color: var(--text-muted);
    }
    .at-meta {
      display: flex;
      align-items: center;
      gap: var(--space-1);
      flex: 0 0 auto;
    }
    .at-alt-bar {
      position: relative;
      width: 4px;
      height: 1.4em;
      border-radius: 2px;
      background: color-mix(in oklab, var(--text-primary) 6%, transparent);
      overflow: hidden;
    }
    .at-alt-fill {
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      border-radius: 2px;
    }
    .at-alt-text {
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
    }
    .at-dist {
      color: var(--text-muted);
      font-weight: var(--fw-semi);
      font-size: .8em;
      font-variant-numeric: tabular-nums;
    }
    @container (max-width: 320px) {
      .at-radar { max-height: 9em; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="sky_air_traffic">
      <div class="w-title">
        <i class="ph-bold ph-airplane-tilt" style="color:var(--accent-4)"></i>
        <h3>Air Traffic</h3>
        <span class="w-title-meta">${escapeHtml(totalMeta)}</span>
      </div>
      <div class="w-body" style="gap:var(--space-2)">
        <div class="at-radar">${radar}</div>
        <div class="list-body" style="display:flex;flex-direction:column;flex:0 0 auto">${rows}</div>
      </div>
    </div>`;
}
