// clock_sunrise_sunset — Bauhaus sun-arc card.
// Hero: big arc with the sun's current position marked, sunrise and
// sunset times anchored to the arc's endpoints, daylight remaining
// in the centre as the lede readout. 4-up stat strip below.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function timeStr(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/T(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : "—";
}

function isoToMin(iso) {
  const m = String(iso).match(/T(\d{2}):(\d{2})/);
  return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : null;
}

function fmtDur(secs) {
  if (!secs && secs !== 0) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

function fmtMin(mins) {
  if (mins == null || mins < 0) return "—";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h === 0) return `${m}m`;
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

function progressOfDay(sunriseMin, sunsetMin) {
  if (sunriseMin == null || sunsetMin == null) return 0;
  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  if (sunsetMin <= sunriseMin) return 0;
  return Math.max(0, Math.min(1, (nowMin - sunriseMin) / (sunsetMin - sunriseMin)));
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/clock_sunrise_sunset/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const riseMin = isoToMin(data.sunrise);
  const setMin = isoToMin(data.sunset);
  const noonMin = (riseMin != null && setMin != null) ? Math.round((riseMin + setMin) / 2) : null;
  const t = progressOfDay(riseMin, setMin);

  // Daylight remaining or "until sunrise" depending on phase.
  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  let leadLabel, leadValue, isDay;
  if (riseMin == null || setMin == null) {
    leadLabel = "Daylight";
    leadValue = fmtDur(data.daylight_seconds);
    isDay = true;
  } else if (nowMin >= riseMin && nowMin < setMin) {
    leadLabel = "Until sunset";
    leadValue = fmtMin(setMin - nowMin);
    isDay = true;
  } else {
    leadLabel = "Until sunrise";
    // Sunrise tomorrow if we're already past sunset today.
    const minutesToSunrise = nowMin >= setMin ? (24 * 60 - nowMin) + riseMin : riseMin - nowMin;
    leadValue = fmtMin(minutesToSunrise);
    isDay = false;
  }

  // Arc geometry — semicircle from horizon to horizon.
  const W = 200, H = 100, R = 92;
  const cx = W / 2, cy = H - 2;  // baseline 2px from the bottom for the horizon line
  const angle = Math.PI - t * Math.PI;
  const sunX = cx + R * Math.cos(angle);
  const sunY = cy - R * Math.sin(angle);
  // Past arc (already happened) = traced from left horizon to sun position.
  // Must use sweep-flag = 1 (same as the track) so the past arc bows
  // UP along the upper semicircle. With sweep-flag = 0 the arc would
  // bow the opposite way (below the horizon) — the old ``t > 0.5`` switch
  // drew the morning past arc upside-down for that reason.
  const sweep = 1;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/clock_sunrise_sunset/client.css">
    <div class="root size-${size} ${isDay ? 'is-day' : 'is-night'}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="ss-title">${escapeHtml(data.label || "Sun")}</span>
        <i class="ph-bold ph-sun-horizon wb-bar-icon"></i>
      </header>

      <section class="ss-hero">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMax meet" class="ss-arc" aria-hidden="true">
          <!-- Full future arc as the dim track -->
          <path d="M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}" class="ss-arc-track" />
          <!-- Sun-progress: solid arc from left horizon up to the current sun position -->
          <path d="M ${cx - R} ${cy} A ${R} ${R} 0 0 ${sweep} ${sunX.toFixed(1)} ${sunY.toFixed(1)}" class="ss-arc-past" />
          <!-- Horizon -->
          <line x1="0" y1="${cy}" x2="${W}" y2="${cy}" class="ss-horizon" />
          <!-- Sun -->
          <circle cx="${sunX.toFixed(1)}" cy="${sunY.toFixed(1)}" r="7" class="ss-sun" />
          <!-- Endpoints (small ticks) -->
          <circle cx="${cx - R}" cy="${cy}" r="3" class="ss-endpoint ss-rise" />
          <circle cx="${cx + R}" cy="${cy}" r="3" class="ss-endpoint ss-set" />
        </svg>
        <div class="ss-rail">
          <div class="ss-rail-end ss-rail-rise">
            <i class="ph-bold ph-sun-horizon"></i>
            <span class="ss-rail-time">${escapeHtml(timeStr(data.sunrise))}</span>
            <span class="ss-rail-lbl">Sunrise</span>
          </div>
          <div class="ss-rail-mid">
            <div class="ss-rail-value">${escapeHtml(leadValue)}</div>
            <div class="ss-rail-lbl">${escapeHtml(leadLabel)}</div>
          </div>
          <div class="ss-rail-end ss-rail-set">
            <i class="ph-bold ph-moon-stars"></i>
            <span class="ss-rail-time">${escapeHtml(timeStr(data.sunset))}</span>
            <span class="ss-rail-lbl">Sunset</span>
          </div>
        </div>
      </section>

      <section class="ss-stats">
        <div class="ss-stat ss-stat--accent">
          <i class="ph-bold ph-sun-horizon ss-stat-icon"></i>
          <span class="ss-stat-label">Sunrise</span>
          <span class="ss-stat-value">${escapeHtml(timeStr(data.sunrise))}</span>
        </div>
        <div class="ss-stat ss-stat--surface">
          <i class="ph-bold ph-sun ss-stat-icon"></i>
          <span class="ss-stat-label">Solar noon</span>
          <span class="ss-stat-value">${noonMin != null ? `${String(Math.floor(noonMin / 60)).padStart(2, "0")}:${String(noonMin % 60).padStart(2, "0")}` : "—"}</span>
        </div>
        <div class="ss-stat ss-stat--accent2">
          <i class="ph-bold ph-moon ss-stat-icon"></i>
          <span class="ss-stat-label">Sunset</span>
          <span class="ss-stat-value">${escapeHtml(timeStr(data.sunset))}</span>
        </div>
        <div class="ss-stat ss-stat--accent3">
          <i class="ph-bold ph-clock ss-stat-icon"></i>
          <span class="ss-stat-label">Daylight</span>
          <span class="ss-stat-value">${escapeHtml(fmtDur(data.daylight_seconds))}</span>
        </div>
      </section>
    </div>
  `;
}
