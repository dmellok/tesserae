// clock_sunrise_sunset — Spectra status archetype with a sun-path
// arc as the visual centrepiece. Hero number is today's daylight
// duration; the arc paints the sun's trajectory across the sky with
// golden-hour bands tinted at each end, twilight bands extending
// below the horizon line for context, and a marker pip at the sun's
// current position. Civil / nautical / astronomical twilight are
// approximated by fixed time offsets (30 / 60 / 90 minutes outside
// the daylight window) since Open-Meteo doesn't ship per-elevation
// twilight; the approximation lines up with the canonical solar
// elevations (-6° / -12° / -18°) within a few minutes for most
// latitudes.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function hhmmFromIso(iso) {
  if (typeof iso !== "string" || !iso.includes("T")) return "—";
  try { return iso.split("T", 2)[1].slice(0, 5); } catch { return "—"; }
}

function fmtDaylight(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds - h * 3600) / 60);
  return { h, m };
}

function minsFromIso(iso) {
  if (typeof iso !== "string" || !iso.includes("T")) return null;
  const [h, m] = iso.split("T")[1].slice(0, 5).split(":").map(Number);
  return h * 60 + m;
}

// Polar → cartesian on the semicircle. angleDeg in standard math
// convention (0° east, 90° north / top, 180° west). Returns SVG
// coordinates (y axis flipped, centred at cx, cy).
function polar(cx, cy, r, angleDeg) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
}

// Build the sun-path arc. viewBox 300×120: horizon line at y=100,
// arc from (20, 100) sweeping up to (280, 100) with radius 130 (the
// curve is slightly flatter than a true semicircle so the band fits
// the rectangle nicely). Background bands (twilight, golden hour,
// daytime) layer underneath the dashed arc path; the sun marker
// rides the arc at the current wall-clock position; rise / set
// ticks anchor the ends.
function sunArcSvg({ riseMin, setMin, nowMin, isDay, opts }) {
  if (riseMin == null || setMin == null) return "";
  const span = Math.max(1, setMin - riseMin);
  // t parameter: 0 at sunrise, 1 at sunset, clamped.
  const t = Math.max(0, Math.min(1, (nowMin - riseMin) / span));

  // viewBox geometry — taller than the visible chord so a TRUE
  // semicircle apex (y = cy - archR) fits inside the box with a
  // little padding above. cy sits near the bottom; the viewBox
  // extends down to leave room for the twilight bands below the
  // horizon line.
  const W = 300, H = 160, cy = 140;
  const archCx = 150;
  const archR = 120;
  const archLeft = archCx - archR;   // 30
  const archRight = archCx + archR;  // 270

  // Marker x/y along the semicircle at parameter t ∈ [0, 1]. t=0
  // → left horizon, t=0.5 → apex, t=1 → right horizon.
  function arcPoint(tt) {
    const ang = 180 - tt * 180;
    const rad = (ang * Math.PI) / 180;
    return {
      x: archCx + archR * Math.cos(rad),
      y: cy - archR * Math.sin(rad),
    };
  }

  // Background regions:
  // - night: x=0..archLeft + x=archRight..W (bands beyond rise/set)
  // - astronomical twilight: ±90 min outside the daylight window
  // - nautical: ±60 min outside
  // - civil: ±30 min outside
  // - golden hour: ±60 min inside the daylight window
  // We render these as a horizontal gradient band underneath the arc,
  // since the chart is essentially a time strip.
  function tToX(tt) {
    return archLeft + tt * (archRight - archLeft);
  }
  // Map a wall-clock-minute offset relative to sunrise/sunset into t.
  // Inside the daylight window, tOff(0, +60) gives the "1h after
  // sunrise" boundary. Outside (negative deltas before rise / past
  // set), the t values fall outside [0, 1] but we map them through
  // the same linear-to-x function so the bands extend beyond the arc.
  function tForMin(minute) {
    return (minute - riseMin) / span;
  }
  const tCivilStart = tForMin(riseMin - 30);
  const tNautStart = tForMin(riseMin - 60);
  const tAstStart = tForMin(riseMin - 90);
  const tCivilEnd = tForMin(setMin + 30);
  const tNautEnd = tForMin(setMin + 60);
  const tAstEnd = tForMin(setMin + 90);
  const tGoldStart = tForMin(riseMin + 60);
  const tGoldEnd = tForMin(setMin - 60);

  // Clamp helper for x-axis band rectangles so they don't escape the
  // visible viewBox area. Rendered rectangles cover the strip from
  // y=cy-50 to y=cy+15 (just above + a sliver below the horizon).
  function bandRect(tStart, tEnd, fill) {
    const x1 = Math.max(0, Math.min(W, tToX(tStart)));
    const x2 = Math.max(0, Math.min(W, tToX(tEnd)));
    if (x2 <= x1) return "";
    return `<rect x="${x1.toFixed(2)}" y="50" width="${(x2 - x1).toFixed(2)}" height="${(cy + 15 - 50).toFixed(2)}" fill="${fill}"/>`;
  }

  // Twilight bands beyond the daylight window. Civil = closest to
  // the horizon (lightest); astronomical = furthest (darkest).
  let twilights = "";
  if (opts.showTwilight) {
    twilights += bandRect(tAstStart, tNautStart, "color-mix(in oklab, var(--text-primary) 20%, transparent)");
    twilights += bandRect(tNautStart, tCivilStart, "color-mix(in oklab, var(--text-primary) 14%, transparent)");
    twilights += bandRect(tCivilStart, 0, "color-mix(in oklab, var(--text-primary) 7%, transparent)");
    twilights += bandRect(1, tCivilEnd, "color-mix(in oklab, var(--text-primary) 7%, transparent)");
    twilights += bandRect(tCivilEnd, tNautEnd, "color-mix(in oklab, var(--text-primary) 14%, transparent)");
    twilights += bandRect(tNautEnd, tAstEnd, "color-mix(in oklab, var(--text-primary) 20%, transparent)");
  }

  // Golden hour tints: warm soft accent-2 (ochre) inside the
  // daylight window, ±60 min from each endpoint. These stack OVER
  // the daylight area, so they read as soft warm zones flanking the
  // bright midday band.
  let golden = "";
  if (opts.showGolden) {
    golden += bandRect(0, tGoldStart, "color-mix(in oklab, var(--accent-2) 22%, transparent)");
    golden += bandRect(tGoldEnd, 1, "color-mix(in oklab, var(--accent-2) 22%, transparent)");
  }

  // The dashed semicircle path — circular arc (rx === ry === archR)
  // so the SVG curve matches the parametric arcPoint() the marker
  // uses and the visual is the iconic "sun crosses the sky" arc.
  const archPath = `M ${archLeft} ${cy}
                    A ${archR} ${archR} 0 0 1 ${archRight} ${cy}`;

  // Sun / moon marker at the current position. During daylight the
  // marker rides the arc; at night it hides — let the moon icon in
  // the hero carry the night state.
  let marker = "";
  if (isDay) {
    const p = arcPoint(t);
    marker = `<circle cx="${p.x.toFixed(2)}" cy="${p.y.toFixed(2)}" r="6"
                      fill="var(--accent-2)"/>
              <circle cx="${p.x.toFixed(2)}" cy="${p.y.toFixed(2)}" r="2.5"
                      fill="var(--surface)"/>`;
  }

  return `
    <svg class="sun-arc" viewBox="0 0 ${W} ${H}"
         preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      ${twilights}
      ${golden}
      <!-- horizon line -->
      <line x1="0" y1="${cy}" x2="${W}" y2="${cy}"
            stroke="var(--text-muted)" stroke-width="1.4"/>
      <!-- dashed arc path -->
      <path d="${archPath}"
            fill="none" stroke="var(--text-secondary)"
            stroke-width="1.8" stroke-dasharray="3 3" opacity="0.6"/>
      <!-- rise / set ticks -->
      <circle cx="${archLeft}" cy="${cy}" r="2.4" fill="var(--text-secondary)"/>
      <circle cx="${archRight}" cy="${cy}" r="2.4" fill="var(--text-secondary)"/>
      ${marker}
    </svg>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showTwilight = opts.show_twilight !== false;
  const showGolden = opts.show_golden !== false;
  const showArc = opts.show_arc !== false;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="clock_sunrise_sunset">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Sun</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.label || "Sun";
  const sunrise = hhmmFromIso(data.sunrise);
  const sunset = hhmmFromIso(data.sunset);
  const daylight = fmtDaylight(data.daylight_seconds);

  const now = new Date();
  const minsNow = now.getHours() * 60 + now.getMinutes();
  const riseMin = minsFromIso(data.sunrise);
  const setMin = minsFromIso(data.sunset);
  const inDay = riseMin != null && setMin != null && minsNow >= riseMin && minsNow < setMin;
  const heroIcon = inDay ? "ph-sun" : "ph-moon";
  const heroAccent = inDay ? "var(--accent-2)" : "var(--text-secondary)";

  const arcBlock = showArc
    ? `<div class="sun-arc-wrap">${sunArcSvg({
        riseMin, setMin, nowMin: minsNow, isDay: inDay,
        opts: { showTwilight, showGolden },
      })}</div>`
    : "";

  const layout = `
    /* Arc absorbs the body's flexible space and centres the SVG
       within it. The hero (Daylight) and the bottom sunrise/sunset
       grid keep their natural heights; everything else between
       belongs to the arc so it reads as the cell's focal element. */
    .sun-arc-wrap {
      width: 100%;
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: var(--space-2) 0;
    }
    .sun-arc {
      width: 100%;
      height: 100%;
      max-height: 100%;
      min-height: clamp(3.5em, 14cqmin, 8em);
      display: block;
    }
    /* Centre the SUNRISE / SUNSET pair inside each grid column
       instead of letting them left-align under the arc's rise / set
       anchors. Reads as a deliberate two-up label block rather than
       hanging off the left edges of each half. */
    .status-grid .status-cell {
      align-items: center;
      text-align: center;
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="clock_sunrise_sunset">
      <div class="w-title">
        <i class="ph-bold ph-sun-horizon" style="color:var(--accent-2)"></i>
        <h3>${escapeHtml(label)}</h3>
      </div>
      <div class="w-body status-body">
        <div class="status-hero">
          <i class="ph-bold ${heroIcon}" style="color:${heroAccent}"></i>
          <div class="lockup">
            <span class="status-state">${daylight ? `${daylight.h}<small style="font-size:.5em;color:var(--text-secondary)"> H </small>${daylight.m}<small style="font-size:.5em;color:var(--text-secondary)"> M</small>` : "—"}</span>
            <span class="status-sub">Daylight</span>
          </div>
        </div>
        ${arcBlock}
        <div class="status-grid">
          <div class="status-cell">
            <span class="u-label">Sunrise</span>
            <span class="v" style="color:var(--accent-2)">${escapeHtml(sunrise)}</span>
          </div>
          <div class="status-cell">
            <span class="u-label">Sunset</span>
            <span class="v" style="color:var(--accent-1)">${escapeHtml(sunset)}</span>
          </div>
        </div>
      </div>
    </div>`;
}
