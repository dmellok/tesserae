// clock_sunrise_sunset — sun arc with current position marked.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function timeStr(iso) {
  if (!iso) return "—";
  // ISO from Open-Meteo lacks the timezone suffix when timezone=auto — it's
  // local for the supplied lat/lon. Parse as local and format hh:mm.
  const m = String(iso).match(/T(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : "—";
}

function durationStr(secs) {
  if (!secs) return "—";
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return `${h}h ${m}m`;
}

function progressOfDay(sunrise, sunset, nowIso) {
  if (!sunrise || !sunset) return 0;
  // Compare wall-clock minutes within the day; Open-Meteo timezone=auto
  // returns local times so this works without tz conversion.
  const toMin = (iso) => {
    const m = String(iso).match(/T(\d{2}):(\d{2})/);
    return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : 0;
  };
  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const r = toMin(sunrise);
  const s = toMin(sunset);
  if (s <= r) return 0;
  return Math.max(0, Math.min(1, (nowMin - r) / (s - r)));
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
  const t = progressOfDay(data.sunrise, data.sunset);

  // Arc geometry: half-circle from (10, 100) to (190, 100), peak at (100, 10).
  // Quadratic Bezier control point at (100, -80) approximates an arc.
  const W = 200, H = 110, R = 90;
  const cx = 100, cy = 100;
  // Position on arc — semi-circle parametrised by t in [0, 1].
  const angle = Math.PI - t * Math.PI;  // pi → 0
  const x = cx + R * Math.cos(angle);
  const y = cy - R * Math.sin(angle);
  // Past arc (already happened) vs future arc — split via two paths.
  const splitAngle = Math.PI - t * Math.PI;
  const splitX = cx + R * Math.cos(splitAngle);
  const splitY = cy - R * Math.sin(splitAngle);
  const sweep = t > 0.5 ? 1 : 0;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/clock_sunrise_sunset/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="ss-title">${escapeHtml(data.label || "Sun")}</span>
        <i class="ph-bold ph-sun-horizon wb-bar-icon"></i>
      </header>
      <section class="ss-hero">
        <svg viewBox="0 0 ${W} ${H}" class="ss-svg" preserveAspectRatio="xMidYMid meet">
          <path d="M ${cx - R} ${cy} A ${R} ${R} 0 0 1 ${cx + R} ${cy}" class="ss-arc-future" />
          <path d="M ${cx - R} ${cy} A ${R} ${R} 0 0 ${sweep} ${splitX.toFixed(1)} ${splitY.toFixed(1)}" class="ss-arc-past" />
          <line x1="${cx - R}" y1="${cy}" x2="${cx + R}" y2="${cy}" class="ss-horizon" />
          <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="9" class="ss-sun" />
        </svg>
        <div class="ss-times">
          <div class="ss-t ss-t--rise">
            <i class="ph-bold ph-sun-horizon"></i>
            <span class="ss-t-lbl">Rise</span>
            <span class="ss-t-v">${escapeHtml(timeStr(data.sunrise))}</span>
          </div>
          <div class="ss-t ss-t--set">
            <i class="ph-bold ph-moon-stars"></i>
            <span class="ss-t-lbl">Set</span>
            <span class="ss-t-v">${escapeHtml(timeStr(data.sunset))}</span>
          </div>
        </div>
      </section>
      <footer class="ss-foot">
        <i class="ph ph-clock"></i>
        <span>Daylight ${escapeHtml(durationStr(data.daylight_seconds))}</span>
      </footer>
    </div>
  `;
}
