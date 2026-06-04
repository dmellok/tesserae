// clock_sunrise_sunset — Spectra status archetype. Daylight duration
// as the hero number, sunrise + sunset as the bottom grid. The hero
// icon is the sun (always — even at night this widget is about
// "today's sun").

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

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
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

  // Are we in daylight right now? Hero icon picks sun (daylight)
  // vs moon (night).
  const now = new Date();
  const minsNow = now.getHours() * 60 + now.getMinutes();
  function minsFromIso(iso) {
    if (typeof iso !== "string" || !iso.includes("T")) return null;
    const [h, m] = iso.split("T")[1].slice(0, 5).split(":").map(Number);
    return h * 60 + m;
  }
  const riseMin = minsFromIso(data.sunrise);
  const setMin = minsFromIso(data.sunset);
  const inDay = riseMin != null && setMin != null && minsNow >= riseMin && minsNow < setMin;
  const heroIcon = inDay ? "ph-sun" : "ph-moon";
  const heroAccent = inDay ? "var(--accent-2)" : "var(--text-secondary)";

  shadow.innerHTML = `
    ${css}
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
