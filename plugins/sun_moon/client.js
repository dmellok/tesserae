// Sun & moon — header strip + sun-arc visualisation + moon-phase disc +
// stat cards. SVG visualisations are sized to fit the cell via container
// queries; the moon phase is computed client-side so the widget paints
// even if the server fetch failed.

const KNOWN_NEW_MOON = Date.UTC(2000, 0, 6, 18, 14) / 1000;
const SYNODIC_DAYS = 29.530588853;

function moonPhase(date) {
  const days = (date.getTime() / 1000 - KNOWN_NEW_MOON) / 86400;
  const cycle = ((days % SYNODIC_DAYS) + SYNODIC_DAYS) % SYNODIC_DAYS;
  const fraction = cycle / SYNODIC_DAYS;
  const illuminationPct = Math.round((1 - Math.cos(2 * Math.PI * fraction)) * 50);
  let label;
  if (fraction < 0.03 || fraction > 0.97) label = "New";
  else if (fraction < 0.22) label = "Waxing crescent";
  else if (fraction < 0.28) label = "First quarter";
  else if (fraction < 0.47) label = "Waxing gibbous";
  else if (fraction < 0.53) label = "Full";
  else if (fraction < 0.72) label = "Waning gibbous";
  else if (fraction < 0.78) label = "Last quarter";
  else label = "Waning crescent";
  return { fraction, label, illuminationPct };
}

function moonLitPath(phase, cx = 50, cy = 50, R = 42) {
  const cos = Math.cos(phase * Math.PI * 2);
  const illum = (1 - cos) / 2;
  const waxing = phase < 0.5;
  const a = Math.abs(cos) * R;
  const outerSweep = waxing ? 1 : 0;
  const terminatorSweep = illum < 0.5 ? outerSweep : 1 - outerSweep;
  return (
    `M ${cx},${cy - R} ` +
    `A ${R},${R} 0 0,${outerSweep} ${cx},${cy + R} ` +
    `A ${a},${R} 0 0,${terminatorSweep} ${cx},${cy - R} Z`
  );
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function dayLength(sunrise, sunset) {
  if (!sunrise || !sunset) return "—";
  const ms = new Date(sunset) - new Date(sunrise);
  if (Number.isNaN(ms) || ms <= 0) return "—";
  const total = Math.round(ms / 60000);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return `${h}h ${m}m`;
}

function sunProgress(now, sunrise, sunset) {
  if (!sunrise || !sunset) return null;
  const r = new Date(sunrise).getTime();
  const s = new Date(sunset).getTime();
  const t = now.getTime();
  if (s <= r) return null;
  return (t - r) / (s - r);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatTime(now) {
  return now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function render(host, ctx) {
  const { options } = ctx.cell;
  const { data } = ctx;
  const place = options.label || "";
  const moon = moonPhase(new Date());
  const sunrise = data && data.sunrise;
  const sunset = data && data.sunset;
  const error = data && data.error;
  const now = new Date();

  if (error) {
    host.innerHTML = `
      <link rel="stylesheet" href="/static/style/widget-base.css">
      <link rel="stylesheet" href="/plugins/sun_moon/client.css">
      <div class="sm sm--error">
        <div class="msg">${escapeHtml(error)}</div>
      </div>
    `;
    host.host.dataset.rendered = "true";
    return;
  }

  // Sun-arc geometry. SVG viewBox is 200x100; horizon at y=80, peak at y=8.
  const progress = sunProgress(now, sunrise, sunset);
  const arcLeft = 18, arcRight = 182, horizonY = 80, peakY = 8;
  const clamped = progress == null ? null : Math.max(0, Math.min(1, progress));
  const sunX = clamped == null ? null : arcLeft + (arcRight - arcLeft) * clamped;
  const sunY = clamped == null ? null : horizonY - (horizonY - peakY) * Math.sin(Math.PI * clamped);

  const rays = [];
  for (let i = 0; i < 8; i++) {
    const angle = (i / 8) * 2 * Math.PI;
    rays.push(
      `<line x1="${(Math.cos(angle) * 9).toFixed(1)}" y1="${(Math.sin(angle) * 9).toFixed(1)}"
             x2="${(Math.cos(angle) * 13.5).toFixed(1)}" y2="${(Math.sin(angle) * 13.5).toFixed(1)}" />`
    );
  }

  const sunSvg = `
    <svg viewBox="0 0 200 100" preserveAspectRatio="xMidYMid meet" class="sun-svg">
      <line class="horizon" x1="10" y1="${horizonY}" x2="190" y2="${horizonY}" />
      <path class="arc" d="M ${arcLeft},${horizonY} Q 100,${peakY - 10} ${arcRight},${horizonY}" />
      ${sunX !== null
        ? `<g class="sun" transform="translate(${sunX.toFixed(1)},${sunY.toFixed(1)})">
            <g class="rays">${rays.join("")}</g>
            <circle cx="0" cy="0" r="6.5" />
          </g>`
        : `<text class="sun-out" x="100" y="${horizonY + 12}" text-anchor="middle">Below horizon</text>`}
      <g class="endpoints">
        <circle cx="${arcLeft}" cy="${horizonY}" r="2" />
        <circle cx="${arcRight}" cy="${horizonY}" r="2" />
      </g>
    </svg>
  `;

  const moonSvg = `
    <svg viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" class="moon-svg">
      <circle class="moon-dark" cx="50" cy="50" r="42" />
      <path class="moon-lit" d="${moonLitPath(moon.fraction)}" />
      <circle class="moon-outline" cx="50" cy="50" r="42" />
    </svg>
  `;

  host.innerHTML = `
    <link rel="stylesheet" href="/static/style/widget-base.css">
    <link rel="stylesheet" href="/plugins/sun_moon/client.css">
    <div class="sm">
      <div class="head">
        <span class="head-title">SUN &amp; MOON</span>
        ${place ? `<span class="head-place">${escapeHtml(place)}</span>` : ""}
        <span class="head-time">${escapeHtml(formatTime(now))}</span>
      </div>

      <div class="visuals">
        <div class="sun-card">${sunSvg}</div>
        <div class="moon-card">
          ${moonSvg}
          <div class="moon-meta">
            <div class="moon-label">${escapeHtml(moon.label)}</div>
            <div class="moon-illum">${moon.illuminationPct}% lit</div>
          </div>
        </div>
      </div>

      <div class="stats">
        <div class="stat">
          <div class="stat-label">SUNRISE</div>
          <div class="stat-value">${escapeHtml(fmtTime(sunrise))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">SUNSET</div>
          <div class="stat-value">${escapeHtml(fmtTime(sunset))}</div>
        </div>
        <div class="stat">
          <div class="stat-label">DAY LENGTH</div>
          <div class="stat-value">${escapeHtml(dayLength(sunrise, sunset))}</div>
        </div>
      </div>
    </div>
  `;

  host.host.dataset.rendered = "true";
}
