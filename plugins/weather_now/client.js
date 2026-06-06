// weather_now — Spectra weather archetype.
//
// Renders ``.w`` → optional ``.w-title`` → ``.w-body.wx-body`` with a
// hero (icon + temp + condition) and a 4-cell metric strip pulled from
// ctx.data.metrics. Size-tiered behaviour via container queries:
//
//   xs  hero only, drop the metric strip entirely.
//   sm  hero + 2 metrics, icon-only (no labels).
//   md  hero + 4 metrics (default layout).
//   lg  hero (grows to fill), 4 metrics, sunrise/sunset arc band.
//
// Semantic weather icon names map to Phosphor bold glyphs via
// PH_BY_NAME; metric icons via METRIC_PH.

const PH_BY_NAME = {
  sun: "ph-sun",
  moon: "ph-moon",
  cloud: "ph-cloud",
  partly: "ph-cloud-sun",
  "partly-night": "ph-cloud-moon",
  drizzle: "ph-drop",
  rain: "ph-cloud-rain",
  "rain-heavy": "ph-cloud-rain",
  showers: "ph-cloud-rain",
  snow: "ph-snowflake",
  storm: "ph-cloud-lightning",
  fog: "ph-cloud-fog",
};

// Condition → accent token. Sun/UV uses ochre, rain/water uses teal,
// stable cloud/cold uses slate blue, storms get the terracotta alert
// colour. Night-time icons fall back to text-secondary so a dark icon
// on a dark theme doesn't go invisible.
const COND_ACCENT = {
  sun: "var(--accent-2)",            // ochre
  moon: "var(--text-secondary)",
  cloud: "var(--accent-5)",          // slate blue
  partly: "var(--accent-2)",
  "partly-night": "var(--text-secondary)",
  drizzle: "var(--accent-4)",        // teal
  rain: "var(--accent-4)",
  "rain-heavy": "var(--accent-4)",
  showers: "var(--accent-4)",
  snow: "var(--accent-5)",
  storm: "var(--accent-1)",          // terracotta — alert
  fog: "var(--text-muted)",
};

const METRIC_PH = {
  humidity: "ph-drop",
  wind: "ph-wind",
  rainprob: "ph-cloud-rain",
  uv: "ph-sun",
  pressure: "ph-gauge",
  dew: "ph-drop-half",
  visibility: "ph-eye",
  cloud: "ph-cloud",
};

// Metric icon accent — water-themed metrics teal, sun-themed ochre,
// neutral measurements stay text-secondary so the grid keeps a steady
// rhythm rather than every cell shouting for attention.
const METRIC_ACCENT = {
  humidity: "var(--accent-4)",
  wind: "var(--text-secondary)",
  rainprob: "var(--accent-4)",
  uv: "var(--accent-2)",
  pressure: "var(--text-secondary)",
  dew: "var(--accent-4)",
  visibility: "var(--text-secondary)",
  cloud: "var(--text-secondary)",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTemp(v) {
  if (v == null) return "—";
  return Math.round(Number(v)) + "°";
}

function fmtMetric(m) {
  if (m == null || m.value == null) return "—";
  const v = m.value;
  if (typeof v === "number") {
    return (v >= 100 ? Math.round(v) : v).toString();
  }
  return String(v);
}

// Sunrise / sunset arc — only painted at lg. Plots a semicircle from
// rise to set with a marker at the current wall-clock position so the
// reader can see at a glance where in the day they are. Times print
// under each end of the arc. Falls back to an empty string when the
// upstream payload is missing rise/set values so md and below never
// reserve space for a band they won't draw.
function sunArc(sun) {
  if (!sun || sun.riseMin == null || sun.setMin == null) return "";
  const rise = sun.riseMin;
  const set = sun.setMin;
  const now = sun.nowMin ?? rise;
  const span = Math.max(1, set - rise);
  const t = Math.max(0, Math.min(1, (now - rise) / span));
  // Arc geometry: viewBox 100x52, semicircle centred at (50, 50) with
  // radius 40. Parametric position on the arc: angle = pi - pi*t goes
  // from pi (left horizon, t=0) round to 0 (right horizon, t=1).
  const cx = (50 - 40 * Math.cos(Math.PI * t)).toFixed(1);
  const cy = (50 - 40 * Math.sin(Math.PI * t)).toFixed(1);
  const dayLengthMin = Math.max(0, set - rise);
  const dayH = Math.floor(dayLengthMin / 60);
  const dayM = dayLengthMin % 60;
  const dayLabel = `${dayH}h ${String(dayM).padStart(2, "0")}m`;
  // Marker uses currentColor of the inner group set to accent-2 so
  // the sun reads as the same ochre as the hero icon at midday. The
  // arc itself is a faint dashed line in text-secondary so the
  // marker is what catches the eye.
  return `
    <div class="wx-sun">
      <svg viewBox="0 0 100 52" preserveAspectRatio="xMidYMax meet" aria-hidden="true">
        <line x1="0" y1="50" x2="100" y2="50"
              stroke="var(--text-muted)" stroke-width="0.6" opacity="0.5"/>
        <path d="M 10 50 A 40 40 0 0 1 90 50"
              fill="none" stroke="var(--text-secondary)"
              stroke-width="1.2" stroke-dasharray="2 2" opacity="0.55"/>
        <circle cx="${cx}" cy="${cy}" r="3.5" fill="var(--accent-2)"/>
        <circle cx="10" cy="50" r="1.6" fill="var(--text-muted)"/>
        <circle cx="90" cy="50" r="1.6" fill="var(--text-muted)"/>
      </svg>
      <div class="wx-sun-meta">
        <span class="wx-sun-end">
          <i class="ph-bold ph-sun-horizon" style="color:var(--accent-2)"></i>
          ${escapeHtml(sun.rise || "--:--")}
        </span>
        <span class="wx-sun-day">${escapeHtml(dayLabel)}</span>
        <span class="wx-sun-end">
          <i class="ph-bold ph-moon" style="color:var(--text-secondary)"></i>
          ${escapeHtml(sun.set || "--:--")}
        </span>
      </div>
    </div>
  `;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/style/spectra-widgets.css">
      <div class="w">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Weather</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.label || "";
  const icon = PH_BY_NAME[data.icon] || "ph-cloud";
  const heroAccent = COND_ACCENT[data.icon] || "var(--accent-4)";
  const temp = fmtTemp(data.temp);
  const cond = data.cond || "";
  const feels = data.feels != null ? `feels ${fmtTemp(data.feels)}` : "";
  const subParts = [cond, feels].filter(Boolean);

  const metrics = Array.isArray(data.metrics) ? data.metrics.slice(0, 4) : [];
  const cells = metrics.map((m) => {
    const ph = METRIC_PH[m.icon] || "ph-circle";
    const accent = METRIC_ACCENT[m.icon] || "var(--text-secondary)";
    const unit = m.unit ? `<span class="unit"> ${escapeHtml(m.unit)}</span>` : "";
    return `
      <div class="wx-cell">
        <span class="d">${escapeHtml(m.label || "")}</span>
        <i class="ph-bold ${ph}" style="color:${accent}"></i>
        <span class="t">${escapeHtml(fmtMetric(m))}${unit}</span>
      </div>`;
  }).join("");

  const titleBar = label
    ? `<div class="w-title"><i class="ph-bold ph-map-pin" style="color:var(--accent-4)"></i><h3>${escapeHtml(label)}</h3></div>`
    : "";

  const arcBlock = sunArc(data.sun);

  // Size-tiered layout. The base (.wx-body) is the md case: hero +
  // 4-cell metric strip. Container queries adjust either end:
  //
  //   xs (<=280px)  drop the metric strip entirely; hero claims the
  //                 whole body and stacks vertically so the icon +
  //                 temp lockup centres in the cell.
  //   sm (281-440)  show only 2 metrics, hide labels — icon + value
  //                 is enough at this size and removing labels saves
  //                 the cramped truncation we used to get ("HUMID...").
  //   lg (>=700)    grow the hero icon + temp to actually fill a
  //                 wide cell, light up the sunrise/sunset arc band
  //                 to occupy what used to be dead space.
  const layout = `
    @container (max-width: 280px) {
      .wx-body { justify-content: center; }
      .wx-forecast { display: none; }
      .wx-sun { display: none; }
      .wx-now {
        flex: 1 1 auto;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: var(--space-2);
        text-align: center;
      }
      .wx-now .ph-bold {
        font-size: clamp(2.6em, 30cqmin, 5em);
      }
      .wx-now .wx-lockup {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: var(--space-1);
      }
      .wx-now .wx-temp { font-size: clamp(2.4em, 22cqmin, 4em); }
      .wx-now .wx-cond { font-size: var(--fs-body); }
    }
    @container (min-width: 281px) and (max-width: 440px) {
      .wx-sun { display: none; }
      .wx-forecast .wx-cell:nth-child(n+3) { display: none; }
      .wx-cell .d { display: none; }
      .wx-forecast { gap: var(--space-3); }
      .wx-cell { gap: var(--space-1); }
    }
    @container (min-width: 441px) and (max-width: 699px) {
      .wx-sun { display: none; }
    }
    @container (min-width: 700px) {
      .wx-body { gap: var(--space-4); min-height: 0; }
      .wx-now {
        flex: 0 0 auto;
        min-height: 0;
        min-width: 0;
        align-items: center;
        justify-content: center;
        gap: var(--space-6);
      }
      .wx-now .ph-bold {
        flex: 0 1 auto;
        font-size: clamp(6em, 32cqmin, 14em);
        line-height: 1;
      }
      .wx-now .wx-lockup {
        flex: 0 1 auto;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: var(--space-1);
      }
      .wx-now .wx-temp {
        font-size: clamp(3em, 18cqmin, 7em);
        line-height: var(--lh-tight);
      }
      .wx-now .wx-cond {
        font-size: clamp(1em, 4cqmin, 1.6em);
      }
      .wx-forecast { flex: 0 0 auto; }
      .wx-sun {
        display: flex;
        flex: 1 1 auto;
        min-height: 0;
        flex-direction: column;
        align-items: stretch;
        justify-content: center;
        gap: var(--space-2);
        padding: var(--space-3) var(--space-4);
        background: var(--surface-sunken);
        border-radius: var(--radius-0);
      }
      .wx-sun svg {
        width: 100%;
        height: clamp(2.4em, 12cqmin, 5em);
        display: block;
      }
      .wx-sun-meta {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: var(--fs-caption);
        font-weight: var(--fw-bold);
        color: var(--text-muted);
        letter-spacing: var(--ls-label);
        text-transform: var(--label-transform, uppercase);
      }
      .wx-sun-end { display: inline-flex; align-items: center; gap: 0.35em; }
      .wx-sun-end .ph-bold { font-size: 1.1em; }
      .wx-sun-day { color: var(--text-secondary); }
    }
  `;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}</style>
    <div class="w" data-widget="weather_now">
      ${titleBar}
      <div class="w-body wx-body">
        <div class="wx-now">
          <i class="ph-bold ${icon}" style="color:${heroAccent}"></i>
          <div class="wx-lockup">
            <div class="wx-temp">${escapeHtml(temp)}</div>
            <div class="wx-cond">${escapeHtml(subParts.join(" · "))}</div>
          </div>
        </div>
        ${cells ? `<div class="wx-forecast">${cells}</div>` : ""}
        ${arcBlock}
      </div>
    </div>`;
}
