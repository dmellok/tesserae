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

// Sky backdrop — two flat-coloured horizontal bands plus one
// decorative element (rays / hatches / dots / etc.) painted from the
// active theme's --accent-*-soft tokens, so the same illustration
// composes cleanly in every Spectra theme. The hero icon and text sit
// on top via z-index, so this layer only hints at the mood (warm /
// cool / dim) without restating what the hero already shows.
//
// Solid blocks, not gradients — gradients dither into mush on
// Spectra 6. Decoration is a few simple paths only; nothing more
// elaborate would survive the panel's 6-colour palette.
function skyBackdrop(iconName) {
  // (topBandToken, decoration) by condition family. ``decoration`` is
  // an SVG fragment in the local coordinate system 0..400 x 0..200.
  const SKY_BY_ICON = {
    sun: { top: "--accent-2-soft", deco: sunRays() },
    partly: { top: "--accent-2-soft", deco: sunRays() },
    moon: { top: "--surface-sunken", deco: stars() },
    "partly-night": { top: "--surface-sunken", deco: stars() },
    cloud: { top: "--accent-5-soft", deco: cloudStreak() },
    drizzle: { top: "--accent-4-soft", deco: rainHatches(8) },
    rain: { top: "--accent-4-soft", deco: rainHatches(14) },
    "rain-heavy": { top: "--accent-4-soft", deco: rainHatches(20) },
    showers: { top: "--accent-4-soft", deco: rainHatches(14) },
    snow: { top: "--accent-5-soft", deco: snowDots() },
    storm: { top: "--accent-1-soft", deco: lightningBolt() },
    fog: { top: "--surface-sunken", deco: fogStrips() },
  };
  const spec = SKY_BY_ICON[iconName] || { top: "--accent-5-soft", deco: "" };
  return `
    <svg class="wx-sky-svg" viewBox="0 0 400 200" preserveAspectRatio="none"
         xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect width="400" height="120" fill="var(${spec.top})"/>
      <rect y="120" width="400" height="80" fill="var(--surface-sunken)"/>
      ${spec.deco}
    </svg>`;
}

function sunRays() {
  // Diagonal ray streaks emanating from a notional sun on the right
  // edge. Drawn as long thin rotated rectangles so they hit the
  // theme's accent-2 colour family without fighting the hero icon.
  return `
    <g fill="var(--accent-2)" opacity="0.65">
      <rect x="270" y="-10" width="3" height="60" transform="rotate(-25 270 -10)"/>
      <rect x="305" y="-10" width="3" height="60" transform="rotate(-15 305 -10)"/>
      <rect x="340" y="-10" width="3" height="60" transform="rotate(-5 340 -10)"/>
      <rect x="375" y="-10" width="3" height="60" transform="rotate(5 375 -10)"/>
    </g>`;
}

function stars() {
  // Pinpricks across the upper band. Sizes vary slightly so the
  // constellation doesn't read as a grid.
  return `
    <g fill="var(--accent-2)" opacity="0.75">
      <circle cx="40" cy="30" r="1.8"/>
      <circle cx="95" cy="55" r="1.3"/>
      <circle cx="155" cy="22" r="1.6"/>
      <circle cx="220" cy="48" r="1.2"/>
      <circle cx="290" cy="32" r="1.7"/>
      <circle cx="350" cy="60" r="1.4"/>
    </g>`;
}

function cloudStreak() {
  // Single horizontal cloud silhouette across the band — three
  // overlapping ellipses give the lumpy edge cumulus needs to read
  // as a cloud without drawing a full multi-shape illustration.
  return `
    <g fill="var(--accent-5)" opacity="0.55">
      <ellipse cx="100" cy="80" rx="55" ry="22"/>
      <ellipse cx="160" cy="72" rx="48" ry="26"/>
      <ellipse cx="220" cy="84" rx="42" ry="20"/>
    </g>`;
}

function rainHatches(count) {
  // Diagonal strokes scattered through the lower band. Density
  // (``count``) ratchets up with intensity so showers vs heavy rain
  // visibly differ.
  const lines = [];
  for (let i = 0; i < count; i++) {
    // Pseudo-random but deterministic — a fixed seed so the SVG is
    // identical across renders (idempotent, contract-friendly).
    const x = ((i * 67) % 400) + 5;
    const y = 110 + ((i * 41) % 70);
    lines.push(`<line x1="${x}" y1="${y}" x2="${x - 6}" y2="${y + 14}" stroke="var(--accent-4)" stroke-width="2" stroke-linecap="round" opacity="0.6"/>`);
  }
  return lines.join("");
}

function snowDots() {
  // Small filled circles scattered through the lower half — denser
  // than stars so it reads as "snow falling" not "starry night".
  const dots = [];
  for (let i = 0; i < 22; i++) {
    const x = ((i * 53) % 400) + 8;
    const y = 105 + ((i * 31) % 80);
    dots.push(`<circle cx="${x}" cy="${y}" r="2" fill="var(--surface)" opacity="0.85"/>`);
  }
  return dots.join("");
}

function lightningBolt() {
  // Single zigzag bolt punching down from the top-right of the band.
  // Drawn as a closed polygon so the colour reads as a solid block,
  // which matters on Spectra 6 where stroked outlines dither badly.
  return `
    <polygon points="290,15 245,90 280,90 250,180 320,75 285,75 310,15"
             fill="var(--accent-2)" opacity="0.8"/>`;
}

function fogStrips() {
  // Three horizontal wavy strips suggesting layered fog. Bezier
  // curves rather than straight lines so they don't feel like
  // architectural strata.
  return `
    <g fill="none" stroke="var(--accent-5)" stroke-width="3" opacity="0.55" stroke-linecap="round">
      <path d="M 0 50 Q 100 40 200 50 T 400 50"/>
      <path d="M 0 90 Q 100 80 200 90 T 400 90"/>
      <path d="M 0 130 Q 100 120 200 130 T 400 130"/>
    </g>`;
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
       canvas. Make it a positioning context, clip the SVG to its
       rounded edges, and keep the hero/text on top of the sky via
       z-index. The SVG itself is z-index 0; foreground children
       (icon + lockup) sit at z-index 1. */
    .wx-now { position: relative; isolation: isolate; overflow: hidden; }
    .wx-sky {
      position: absolute;
      inset: 0;
      z-index: 0;
      pointer-events: none;
    }
    .wx-sky svg { width: 100%; height: 100%; display: block; }
    .wx-now > i.ph-bold,
    .wx-now > div { position: relative; z-index: 1; }
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

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}</style>
    <div class="w" data-widget="weather_now">
      ${titleBar}
      <div class="w-body wx-body">
        <div class="wx-now">
          ${showSky ? `<div class="wx-sky">${skyBackdrop(data.icon)}</div>` : ""}
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
