// weather_now — Spectra weather archetype.
//
// Renders ``.w`` → optional ``.w-title`` → ``.w-body.wx-body`` with a
// hero (icon + temp + condition) and a 4-cell metric strip pulled from
// ctx.data.metrics. Semantic weather icon names map to Phosphor bold
// glyphs via PH_BY_NAME; metric icons via METRIC_PH.

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

// Sky backdrop — two flat horizontal bands of accent-soft tone per
// condition family. The top band's hue sets the mood (warm for sun,
// cool for rain, dim for night); the bottom band is the surface
// sunken token so the backdrop reads as "sky above ground". The
// hero icon + temp lockup sit on top via z-index, so this layer
// only hints at mood — the hero does the condition-specific work.
//
// Solid blocks only (no decorative shapes, no gradients) — both
// dither into mush on Spectra 6, and the user explicitly asked for
// the quieter two-tone version.
function skyBackdrop(iconName) {
  // Per-condition top band token. Anything we don't have a tone for
  // falls back to a neutral cool slate.
  const TOP_BY_ICON = {
    sun: "--accent-2-soft",
    partly: "--accent-2-soft",
    moon: "--surface-sunken",
    "partly-night": "--surface-sunken",
    cloud: "--accent-5-soft",
    drizzle: "--accent-4-soft",
    rain: "--accent-4-soft",
    "rain-heavy": "--accent-4-soft",
    showers: "--accent-4-soft",
    snow: "--accent-5-soft",
    storm: "--accent-1-soft",
    fog: "--surface-sunken",
  };
  const top = TOP_BY_ICON[iconName] || "--accent-5-soft";
  return `
    <svg viewBox="0 0 400 200" preserveAspectRatio="none"
         xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect width="400" height="120" fill="var(${top})"/>
      <rect y="120" width="400" height="80" fill="var(--surface-sunken)"/>
    </svg>`;
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

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showSky = !!opts.sky_backdrop;
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

  // Hero row sizing: icon, temp, and caption all scale against
  // ``cqmin`` (the smaller of cell width / height) so a wide-and-short
  // cell never lets the icon overflow vertically, and a square cell
  // gets a balanced size. The hero row flex-grows + centres
  // horizontally so it claims the body's middle band instead of
  // hugging the left edge and leaving dead space on the right. The
  // forecast strip takes its share of vertical room below. Wide cells
  // (>700px) crank the cqmin cap up so the icon really fills the
  // available width on lg dashboards.
  // Sizing rules — only override at wide cells (>700px) where the
  // default 2.6em hero icon looks lost in space. Below that, leave
  // the spectra-widgets.css defaults alone so .wx-forecast cells keep
  // their labels + values intact at md/sm. The icon sizes against
  // cqmin so a wide-and-short cell can't blow it past the cell
  // height; the row flex-grows + centres horizontally so the icon +
  // temp lockup read as the cell's focal point instead of hugging
  // the left edge.
  const layout = `
    /* When the sky backdrop is on, the .wx-now block becomes the
       canvas: positioning context, clip the SVG to its bounds, and
       keep the hero / lockup on top of the sky via z-index. Using
       .wx-lockup as an explicit class (rather than the generic
       ``.wx-now > div`` from before) so the rule doesn't also paint
       the .wx-sky child back to position: relative — that's what
       used to leave the backdrop sitting beside the hero instead of
       behind it. */
    .wx-now { position: relative; isolation: isolate; overflow: hidden; }
    .wx-sky {
      position: absolute;
      inset: 0;
      z-index: 0;
      pointer-events: none;
    }
    .wx-sky svg { width: 100%; height: 100%; display: block; }
    .wx-now > i.ph-bold,
    .wx-now > .wx-lockup { position: relative; z-index: 1; }
    /* Hide the backdrop at xs — the cell is too small to read both
       the illustration and the hero icon. The hero alone carries
       the condition at that size. */
    @container (max-width: 220px) {
      .wx-sky { display: none; }
    }
    @container (min-width: 700px) {
      .wx-body { gap: var(--space-4); min-height: 0; }
      .wx-now {
        flex: 1 1 auto;
        min-height: 0;
        min-width: 0;
        align-items: center;
        justify-content: center;
        gap: var(--space-6);
      }
      .wx-now .ph-bold {
        flex: 0 1 auto;
        font-size: clamp(5em, 28cqmin, 12em);
        line-height: 1;
      }
      .wx-now > .wx-lockup {
        flex: 0 1 auto;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: var(--space-1);
      }
      .wx-now .wx-temp {
        font-size: clamp(3em, 16cqmin, 6.5em);
        line-height: var(--lh-tight);
      }
      .wx-now .wx-cond {
        font-size: clamp(1em, 4cqmin, 1.5em);
      }
    }
  `;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}</style>
    <div class="w" data-widget="weather_now">
      ${titleBar}
      <div class="w-body wx-body">
        <div class="wx-now">
          ${showSky ? `<div class="wx-sky">${skyBackdrop(data.icon)}</div>` : ""}
          <i class="ph-bold ${icon}" style="color:${heroAccent}"></i>
          <div class="wx-lockup">
            <div class="wx-temp">${escapeHtml(temp)}</div>
            <div class="wx-cond">${escapeHtml(subParts.join(" · "))}</div>
          </div>
        </div>
        ${cells ? `<div class="wx-forecast">${cells}</div>` : ""}
      </div>
    </div>`;
}
