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

// Flat-style sky background. One of five condition stops drives the
// gradient + foreground silhouette:
//   clear / partly / cloud / rain / night
// Returns SVG markup that absolutely fills the widget body when the
// ``sky_background`` cell option is on. Paints from condition-keyed
// hexes rather than theme tokens so the sky reads as the sky on any
// theme — the data widgets stay readable on top because they paint
// from text-primary which the theme tunes for contrast on whatever
// surface they're drawn over.
const SKY_STOPS = {
  clear:        ["#FCE7A8", "#F1B468", "#C76A3C"],   // dawn-warm yellow → terracotta
  partly:       ["#C9DDED", "#7AA4C2", "#E8B07A"],   // pale cyan → warm horizon
  "partly-night": ["#1B2240", "#3B3E66", "#5C547A"],
  cloud:        ["#D8DEE5", "#A6AFBA", "#6F7986"],
  drizzle:      ["#B9C5D2", "#7A8C9D", "#465260"],
  rain:         ["#7B8693", "#4A5563", "#1F2731"],
  "rain-heavy": ["#5E6976", "#33404D", "#161E27"],
  showers:      ["#7B8693", "#4A5563", "#1F2731"],
  snow:         ["#E1E6EC", "#B2BDC8", "#7F8C99"],
  storm:        ["#52596B", "#2A2F3D", "#0E1119"],
  fog:          ["#D5D5D2", "#9C9E9C", "#5D605F"],
  moon:         ["#0D1424", "#1B2240", "#3B3E66"],
  sun:          ["#FCE7A8", "#F1B468", "#C76A3C"],
};

function skySvg(iconKey) {
  const stops = SKY_STOPS[iconKey] || SKY_STOPS.clear;
  const isNight = iconKey === "moon" || iconKey === "partly-night";
  const isWet = iconKey === "rain" || iconKey === "rain-heavy" || iconKey === "showers" || iconKey === "drizzle" || iconKey === "storm";
  // Optional sun / moon disc — drawn only on clear-ish keys so a
  // stormy / overcast frame doesn't have a luminary punching through.
  const luminary = (iconKey === "clear" || iconKey === "sun" || iconKey === "partly") ?
    `<circle cx="80" cy="32" r="18" fill="#FFF3C8" opacity="0.95"/>` :
    (isNight ? `<circle cx="82" cy="28" r="14" fill="#F2E9D5" opacity="0.92"/><circle cx="76" cy="24" r="5" fill="${stops[0]}" opacity="1"/>` : "");
  // Three flat cloud silhouettes layered against the foreground.
  const clouds = (iconKey === "cloud" || iconKey === "partly" || iconKey === "drizzle" || iconKey === "rain" || iconKey === "rain-heavy" || iconKey === "showers" || iconKey === "snow" || iconKey === "storm") ?
    `<ellipse cx="30" cy="38" rx="22" ry="8" fill="${stops[1]}" opacity="0.7"/>
     <ellipse cx="55" cy="44" rx="24" ry="9" fill="${stops[1]}" opacity="0.55"/>` : "";
  // Rain streaks under the cloud line.
  const rain = isWet ?
    `<g stroke="${stops[0]}" stroke-width="1" opacity="0.6" stroke-linecap="round">
       ${[15, 22, 31, 40, 48, 58, 66, 75, 84].map((x) =>
         `<line x1="${x}" y1="48" x2="${x - 2}" y2="58"/>`).join("")}
     </g>` : "";
  // Lightning bolt for storms.
  const bolt = (iconKey === "storm") ?
    `<polygon points="50,42 56,42 52,52 58,52 48,68 52,58 46,58" fill="#FBE38B"/>` : "";
  // Foreground horizon — a single low silhouette so the sky doesn't
  // feel like an infinite gradient.
  const horizon = `<path d="M 0 62 Q 25 56 50 60 T 100 58 L 100 70 L 0 70 Z"
                         fill="${stops[2]}" opacity="0.9"/>`;
  return `
    <svg viewBox="0 0 100 70" preserveAspectRatio="xMidYMid slice"
         style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0">
      <defs>
        <linearGradient id="sky-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${stops[0]}"/>
          <stop offset="60%" stop-color="${stops[1]}"/>
          <stop offset="100%" stop-color="${stops[2]}"/>
        </linearGradient>
      </defs>
      <rect width="100" height="70" fill="url(%23sky-grad)"/>
      ${luminary}
      ${clouds}
      ${rain}
      ${bolt}
      ${horizon}
    </svg>`.replace("%23", "#");
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
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

  // Experimental sky background. When opted in via the cell option,
  // paint a flat-style sky SVG behind the data and add a subtle dark/
  // light scrim under the text + forecast strip so the hero numbers
  // stay legible against any condition stop.
  const skyOn = opts.sky_background === true;
  const skyKey = data.icon || "clear";
  const skyLayer = skyOn ? skySvg(skyKey) : "";

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
      .wx-now > div {
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

  // Extra layout when sky-background is on: make .w position:relative
  // so the SVG can absolute-fill underneath, and add a subtle scrim on
  // the body so text stays legible over any condition stop.
  const skyLayout = skyOn ? `
    .w { position: relative; }
    .w-title, .w-body { position: relative; z-index: 1; }
    .w-title { background: linear-gradient(to bottom, rgba(255,255,255,0.65), rgba(255,255,255,0)); }
    .wx-now, .wx-forecast { background: rgba(255,255,255,0.0); }
    .wx-temp, .wx-cond, .wx-cell .d, .wx-cell .t { text-shadow: 0 1px 0 rgba(255,255,255,0.45); }
  ` : "";

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}${skyLayout}</style>
    <div class="w" data-widget="weather_now"${skyOn ? ` data-sky="${escapeHtml(skyKey)}"` : ""}>
      ${skyLayer}
      ${titleBar}
      <div class="w-body wx-body">
        <div class="wx-now">
          <i class="ph-bold ${icon}" style="color:${heroAccent}"></i>
          <div>
            <div class="wx-temp">${escapeHtml(temp)}</div>
            <div class="wx-cond">${escapeHtml(subParts.join(" · "))}</div>
          </div>
        </div>
        ${cells ? `<div class="wx-forecast">${cells}</div>` : ""}
      </div>
    </div>`;
}
