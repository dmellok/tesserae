// weather_now_scenic, opt-in "extended palette" weather widget.
//
// Renders a pill-shaped card whose background + decoration changes
// with the current weather + time-of-day. Uses arbitrary CSS colours
// (gradients, layered shapes), so this widget declares
// design.palette: "extended" in plugin.json. On 7-colour Spectra
// e-ink panels the renderer's Floyd-Steinberg dither approximates the
// soft transitions; on BW panels they collapse to coarse hatching.
//
// Layout (md/lg):
//   ┌──────────────────────────────────────────┐
//   │ ☀ Sunny                       09:30      │
//   │                              June 21     │
//   │       34°                    London      │
//   └──────────────────────────────────────────┘
//
// Six presets share the same layout machinery; each contributes a
// background, accent + decorative scene element. Add a new preset by
// extending PRESETS + drawing its scene() function.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTemp(v) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${Math.round(Number(v))}°`;
}

function fmtDate() {
  // Avoid locale surprises (panels in non-English locales rendering
  // half-translated dates). Server-side rendering uses the container's
  // system locale; we pin to a stable "Mon DD" form so the visual
  // matches across panels.
  const now = new Date();
  return now.toLocaleDateString("en-US", { month: "long", day: "numeric" });
}

function fmtTime() {
  const now = new Date();
  return now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
}

// --- preset table ---------------------------------------------------

const PRESETS = {
  sunny_day: {
    icon: "ph-sun",
    bg: "linear-gradient(125deg, #f06752 0%, #f5915f 45%, #ffb774 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.78)",
    accent: "#ffd166",
    scene: sceneSun,
  },
  clear_night: {
    icon: "ph-moon",
    bg: "linear-gradient(155deg, #0a1126 0%, #14346b 60%, #1b4a96 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.72)",
    accent: "#ffd166",
    scene: sceneNight,
  },
  partly_day: {
    icon: "ph-cloud-sun",
    bg: "linear-gradient(135deg, #4a8fbf 0%, #6daed3 55%, #aacde0 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.78)",
    accent: "#ffd166",
    scene: sceneSun,
  },
  partly_night: {
    icon: "ph-cloud-moon",
    bg: "linear-gradient(155deg, #0a1126 0%, #14346b 60%, #1b4a96 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.72)",
    accent: "#ffd166",
    scene: sceneNight,
  },
  cloudy_day: {
    icon: "ph-cloud",
    bg: "linear-gradient(135deg, #6c9bb8 0%, #8eb4cb 55%, #c3d6e0 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.78)",
    accent: "#ffffff",
    scene: sceneClouds,
  },
  cloudy_night: {
    icon: "ph-cloud-moon",
    bg: "linear-gradient(160deg, #1a2940 0%, #2b3f5e 55%, #3d557a 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.74)",
    accent: "#ffd166",
    scene: sceneClouds,
  },
  rain: {
    icon: "ph-cloud-rain",
    bg: "linear-gradient(145deg, #2d3f5e 0%, #466a8c 60%, #6c92b3 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.74)",
    accent: "#bcd8ee",
    scene: sceneRain,
  },
  snow: {
    icon: "ph-snowflake",
    /* Darker base than the original sketch so white text reads,
       light blue at the bottom keeps the snowy-vibe without going
       full pastel. */
    bg: "linear-gradient(150deg, #4a78a0 0%, #87a8c4 60%, #b6cee0 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.85)",
    accent: "#ffffff",
    scene: sceneSnow,
  },
  storm: {
    icon: "ph-cloud-lightning",
    bg: "linear-gradient(150deg, #0c1224 0%, #1e2a47 55%, #2f3f63 100%)",
    text: "#fff",
    textSoft: "rgba(255, 255, 255, 0.72)",
    accent: "#ffd166",
    scene: sceneStorm,
  },
};

const FALLBACK = PRESETS.cloudy_day;

// --- scene builders -------------------------------------------------
// Each returns an HTML fragment that renders inside .scene (absolutely
// positioned behind the .content layer). Use inline SVG + CSS shapes
// so a widget reload doesn't depend on extra image files.

function sceneSun() {
  // Three nested circles bleeding off the right edge, each a slightly
  // different yellow so the dither gives a soft ring rather than a
  // hard disc on the panel.
  return `
    <div class="ring ring-3"></div>
    <div class="ring ring-2"></div>
    <div class="ring ring-1"></div>`;
}

function sceneNight() {
  // Crescent moon (SVG with a circle mask, so the mask can never
  // leak past the moon's own disc, the old two-div hack showed the
  // mask floating in the sky) plus scattered star dots. Star SVG
  // viewBox is wide so dots spread across the pill rather than
  // clustering, ``slice`` preserves the round shape.
  const stars = [
    [22, 18, 0.9], [44, 28, 0.7], [68, 14, 1.1], [88, 24, 0.7], [110, 32, 0.9],
    [128, 18, 0.7], [148, 28, 0.9], [168, 14, 0.7], [188, 22, 0.9], [208, 30, 0.7],
    [38, 48, 0.7], [62, 56, 0.9], [88, 50, 0.7], [110, 58, 1.0], [134, 48, 0.7],
    [158, 62, 0.9], [180, 50, 0.7], [202, 60, 0.9],
    [28, 78, 0.7], [56, 84, 0.9], [82, 76, 0.7], [108, 88, 0.9], [136, 78, 0.7],
    [162, 84, 0.9], [190, 76, 0.7],
  ]
    .map(([cx, cy, r]) => `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#fff" opacity="0.85"/>`)
    .join("");
  return `
    <svg class="stars" viewBox="0 0 300 100" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      ${stars}
    </svg>
    <svg class="moon-svg" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <defs>
        <mask id="moon-crescent">
          <rect width="100" height="100" fill="white"/>
          <circle cx="38" cy="42" r="36" fill="black"/>
        </mask>
      </defs>
      <circle cx="50" cy="50" r="38" fill="#ffd166" mask="url(#moon-crescent)"/>
    </svg>`;
}

function sceneClouds() {
  // Three SVG cumulus silhouettes at varying sizes + depths so the
  // composition reads as a sky-with-clouds rather than a smudgy band.
  // Paths are simple cubic-arc puffs; each cloud is one closed path.
  return `
    <svg class="cloud cloud-far" viewBox="0 0 120 60" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="M 18 46 Q 6 46 6 34 Q 6 22 22 24 Q 28 8 46 12 Q 64 4 72 22 Q 92 18 98 36 Q 100 50 80 48 Q 60 56 42 50 Q 28 52 18 46 Z"
            fill="#fff" opacity="0.55"/>
    </svg>
    <svg class="cloud cloud-mid" viewBox="0 0 120 60" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="M 16 48 Q 4 48 6 36 Q 8 22 24 24 Q 30 6 48 10 Q 66 2 74 20 Q 94 16 100 34 Q 102 48 84 48 Q 60 56 40 50 Q 26 52 16 48 Z"
            fill="#fff" opacity="0.85"/>
    </svg>
    <svg class="cloud cloud-near" viewBox="0 0 120 60" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="M 18 46 Q 6 46 6 34 Q 6 22 22 24 Q 28 8 46 12 Q 64 4 72 22 Q 92 18 98 36 Q 100 50 80 48 Q 60 56 42 50 Q 28 52 18 46 Z"
            fill="#fff" opacity="0.95"/>
    </svg>`;
}

function sceneRain() {
  // Three layers: a base streak gradient (the falling-sheet), a
  // secondary streak gradient at a slightly different angle + offset
  // (depth + variation, so it doesn't look like a fence), and an SVG
  // overlay of discrete drops scattered across the card (foreground
  // detail). Combined, the eye reads "rain falling at varying
  // distances".
  const drops = [
    [180, 18], [210, 30], [240, 14], [270, 38], [196, 56],
    [226, 70], [256, 64], [186, 82], [216, 86], [246, 78],
    [276, 88], [200, 44], [232, 50], [262, 32],
  ]
    .map(
      ([x, y]) => `
    <path d="M ${x} ${y} q -1.8 4 -3.6 8" stroke="#fff" stroke-width="1.6" stroke-linecap="round" fill="none" opacity="0.7"/>`
    )
    .join("");
  return `
    <div class="rain-streaks rain-streaks-back"></div>
    <div class="rain-streaks rain-streaks-front"></div>
    <svg class="rain-drops" viewBox="0 0 300 100" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      ${drops}
    </svg>`;
}

function sceneSnow() {
  // Mix of small + medium flakes scattered across the pill, all at
  // reduced opacity so the scenery hangs back instead of fighting
  // the temperature. Small flakes give depth, medium ones the focal
  // points.
  const small = [
    [42, 28], [78, 18], [118, 32], [162, 24], [200, 30], [240, 18], [272, 26],
    [56, 60], [94, 52], [134, 64], [176, 58], [216, 66], [254, 54],
    [30, 86], [76, 90], [112, 80], [154, 88], [196, 84], [232, 90], [268, 80],
  ]
    .map(
      ([x, y]) => `
    <g transform="translate(${x},${y})" opacity="0.5">
      <line x1="-3.5" y1="0" x2="3.5" y2="0" stroke="#fff" stroke-width="0.9" stroke-linecap="round"/>
      <line x1="0" y1="-3.5" x2="0" y2="3.5" stroke="#fff" stroke-width="0.9" stroke-linecap="round"/>
      <line x1="-2.5" y1="-2.5" x2="2.5" y2="2.5" stroke="#fff" stroke-width="0.9" stroke-linecap="round"/>
      <line x1="-2.5" y1="2.5" x2="2.5" y2="-2.5" stroke="#fff" stroke-width="0.9" stroke-linecap="round"/>
    </g>`
    )
    .join("");
  const large = [
    [58, 38], [142, 22], [220, 48], [108, 70], [184, 78], [250, 32],
  ]
    .map(
      ([x, y]) => `
    <g transform="translate(${x},${y})" opacity="0.7">
      <line x1="-7" y1="0" x2="7" y2="0" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>
      <line x1="0" y1="-7" x2="0" y2="7" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>
      <line x1="-5" y1="-5" x2="5" y2="5" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>
      <line x1="-5" y1="5" x2="5" y2="-5" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>
    </g>`
    )
    .join("");
  return `
    <svg class="flakes" viewBox="0 0 300 100" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      ${small}
      ${large}
    </svg>`;
}

function sceneStorm() {
  // Two-stage bolt: a soft yellow glow (large blurred shape) behind a
  // sharp zig-zag bolt in front, so it reads as electric rather than
  // a flat lemon-coloured triangle. Plus a darker storm cloud above
  // so the bolt has somewhere to come from.
  return `
    <div class="cloud cloud-far storm-cloud"></div>
    <svg class="bolt-glow" viewBox="0 0 80 120" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="M 50 4 L 22 56 L 38 56 L 14 116 L 64 48 L 44 48 L 60 12 Z"
            fill="#ffd166" opacity="0.45"/>
    </svg>
    <svg class="bolt" viewBox="0 0 80 120" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d="M 50 4 L 22 56 L 38 56 L 14 116 L 64 48 L 44 48 L 60 12 Z"
            fill="#ffd166" stroke="#000" stroke-width="1.2" stroke-linejoin="round"/>
    </svg>`;
}

// --- render entry point ---------------------------------------------

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

  const presetName = PRESETS[data.preset] ? data.preset : "cloudy_day";
  const preset = PRESETS[presetName] || FALLBACK;
  const temp = fmtTemp(data.temp);
  const cond = data.cond || "";
  const label = data.label || "";
  const time = fmtTime();
  const date = fmtDate();
  const scene = preset.scene ? preset.scene() : "";

  const layout = `
    :host {
      /* container-type must be size (not inline-size) so cqh
         resolves to the host element's own height. inline-size only
         contains the inline axis, so cqh falls back to the viewport
         height there and every cqh-sized decoration ends up
         viewport-relative. The size variant contains both axes. */
      container-type: size;
      display: block;
      width: 100%;
      height: 100%;
    }
    .card {
      position: relative;
      width: 100%;
      height: 100%;
      border-radius: clamp(14px, 4.5cqmin, 28px);
      overflow: hidden;
      background: ${preset.bg};
      color: ${preset.text};
      box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45);
      isolation: isolate;
    }
    .scene {
      position: absolute;
      inset: 0;
      z-index: 0;
      overflow: hidden;
      pointer-events: none;
    }
    .content {
      position: relative;
      z-index: 1;
      width: 100%;
      height: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
      grid-template-rows: auto 1fr auto;
      padding: clamp(0.7em, 4cqmin, 1.4em) clamp(0.9em, 5cqmin, 1.8em);
      box-sizing: border-box;
      gap: clamp(0.3em, 1cqmin, 0.6em);
    }
    .cond {
      grid-column: 1;
      grid-row: 1;
      display: flex;
      align-items: center;
      gap: clamp(0.3em, 1.3cqmin, 0.6em);
      font-size: clamp(0.9em, 4.5cqmin, 1.3em);
      font-weight: 600;
      letter-spacing: 0.02em;
    }
    .cond i {
      font-size: 1.1em;
      color: ${preset.accent};
    }
    .meta {
      grid-column: 2;
      grid-row: 1 / 4;
      display: grid;
      grid-template-rows: auto 1fr auto;
      justify-items: end;
      align-items: start;
      text-align: right;
      gap: clamp(0.2em, 0.6cqmin, 0.5em);
    }
    .meta .time {
      font-size: clamp(1.6em, 9.5cqmin, 3.6em);
      font-weight: 600;
      line-height: 1;
      letter-spacing: -0.01em;
    }
    .meta .date,
    .meta .place {
      font-size: clamp(0.7em, 3.2cqmin, 1em);
      color: ${preset.textSoft};
      font-weight: 500;
    }
    .meta .date { align-self: end; }
    .meta .place { align-self: end; }
    .temp {
      grid-column: 1;
      grid-row: 2 / 4;
      display: flex;
      align-items: flex-end;
      font-size: clamp(2.6em, 18cqmin, 5.4em);
      font-weight: 700;
      line-height: 0.95;
      letter-spacing: -0.02em;
    }

    /* --- scenes ----------------------------------------------------- */
    /* All decoration sizes use cqh (container height) so circles stay
       circular on wide pill cards. cqw was the original mistake, on a
       3:1 pill it makes a "circle" 3x wider than tall. */
    .ring {
      position: absolute;
      border-radius: 50%;
    }
    /* Sun rings: each disc is bigger than the last and sits a bit
       further off the top-right corner, so only a quarter of each
       shows. The bright yellow core sits closest to the visible card;
       progressively softer/darker haloes pad outward. */
    .ring-1 {
      width: 85cqh;
      height: 85cqh;
      right: -22cqh;
      top: -32cqh;
      background: #ffd166;
    }
    .ring-2 {
      width: 120cqh;
      height: 120cqh;
      right: -40cqh;
      top: -52cqh;
      background: #ffb259;
      opacity: 0.7;
    }
    .ring-3 {
      width: 160cqh;
      height: 160cqh;
      right: -60cqh;
      top: -78cqh;
      background: #f59055;
      opacity: 0.55;
    }
    .stars,
    .flakes {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }
    /* Moon: a single inline SVG with a circle mask cuts the crescent
       geometrically, so the mask can never leak past the moon's own
       disc (the previous two-div approach left a navy ghost-disc
       floating in the sky). */
    .moon-svg {
      position: absolute;
      right: 14cqh;
      top: 10cqh;
      width: 72cqh;
      height: 72cqh;
    }
    /* Cloud SVGs: explicit cumulus silhouettes (was a blurred-blob
       band, which read as smudge rather than weather). Three layered
       passes give depth: a distant pale cloud sits high, a mid-
       opacity main cloud below it, and a closer foreground cloud
       overlapping the temperature corner. */
    .cloud {
      position: absolute;
    }
    .cloud-far {
      right: 8cqh;
      top: 8cqh;
      width: 95cqh;
      height: 48cqh;
    }
    .cloud-mid {
      right: 38cqh;
      top: 26cqh;
      width: 125cqh;
      height: 62cqh;
    }
    .cloud-near {
      right: -10cqh;
      bottom: -4cqh;
      width: 110cqh;
      height: 55cqh;
    }
    /* Rain: layered streak gradients at slightly different angles +
       a discrete drop SVG so the result reads as a sheet of falling
       rain with varied depth, not a flat fence. */
    .rain-streaks {
      position: absolute;
      inset: -10% 0 -10% 40%;
    }
    .rain-streaks-back {
      background: repeating-linear-gradient(
        100deg,
        transparent 0,
        transparent 14px,
        rgba(255, 255, 255, 0.35) 14px,
        rgba(255, 255, 255, 0.35) 16px
      );
      opacity: 0.55;
    }
    .rain-streaks-front {
      background: repeating-linear-gradient(
        110deg,
        transparent 0,
        transparent 7px,
        rgba(255, 255, 255, 0.55) 7px,
        rgba(255, 255, 255, 0.55) 8px
      );
      opacity: 0.65;
      transform: translateX(8px);
    }
    .rain-drops {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }
    .storm-cloud {
      right: 30cqh;
      top: 10cqh;
      width: 130cqh;
      height: 55cqh;
      background: #1a2640;
      opacity: 0.75;
      filter: blur(2px);
      border-radius: 50%;
    }
    /* Lightning: a wider glow disc behind a sharper bolt path in
       front, so the bolt reads as electric rather than a flat
       triangle. The blur on the glow is what makes the impression. */
    .bolt {
      position: absolute;
      right: 14cqh;
      bottom: 4cqh;
      height: 90%;
      width: auto;
    }
    .bolt-glow {
      position: absolute;
      right: 4cqh;
      bottom: -6cqh;
      height: 110%;
      width: auto;
      filter: blur(8px);
    }

    /* --- container-query response ----------------------------------- */
    @container (max-width: 280px) {
      /* sm collapses the meta column to just temp + tiny condition */
      .content {
        grid-template-columns: 1fr;
        grid-template-rows: auto 1fr;
        text-align: left;
      }
      .cond { font-size: clamp(0.8em, 5cqmin, 1em); }
      .meta {
        grid-column: 1;
        grid-row: 2;
        justify-items: start;
        text-align: left;
        align-self: end;
      }
      .meta .time { font-size: clamp(1.4em, 11cqmin, 2.6em); }
      .meta .date,
      .meta .place { font-size: clamp(0.7em, 4cqmin, 0.95em); }
      .temp {
        grid-column: 1;
        grid-row: 2;
        justify-self: end;
        font-size: clamp(2.4em, 18cqmin, 4em);
      }
    }
  `;

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css">
    <style>${layout}</style>
    <div class="card preset-${escapeHtml(presetName)}">
      <div class="scene" aria-hidden="true">${scene}</div>
      <div class="content">
        <div class="cond">
          <i class="ph-fill ${preset.icon}" aria-hidden="true"></i>
          <span>${escapeHtml(cond)}</span>
        </div>
        <div class="temp">${escapeHtml(temp)}</div>
        <div class="meta">
          <div class="time">${escapeHtml(time)}</div>
          <div class="date">${escapeHtml(date)}</div>
          <div class="place">${escapeHtml(label)}</div>
        </div>
      </div>
    </div>`;
}
