// Shared helpers for the weather widget family. Imported by each
// weather/sky widget's client.js as:
//
//   import { WX } from "../weather_core/static/wx-common.js";
//
// Provides:
//   * WX.PH        — semantic-name → Phosphor glyph mapping (bold weight)
//   * WX.col(a)    — Spectra 6 accent token (--wx-{accent})
//   * WX.tint(a)   — paler tint of the same accent (--wx-{accent}-t)
//   * WX.inkOn(a)  — readable text colour on a coloured fill
//   * WX.icon(name, opts)        — <i class="ph-bold ph-…"> string
//   * WX.iconSvg(name, opts)     — same, but as inline SVG (for cases
//                                  where the font hasn't loaded yet —
//                                  e-ink renderer paints from a static
//                                  PNG snapshot, so the font is
//                                  always loaded, but admin preview
//                                  iframes occasionally race the load)
//   * WX.tnum(html)              — wraps text in a tabular-figures span
//   * WX.escapeHtml(s)           — XSS-safe text node
//   * WX.darkHeader({title, accent, right})
//                                — pre-baked "[chip] PLACE … TIME" bar
//   * WX.sunArc({rise, set, now, color, width, height})
//                                — small SVG of the sun's arc, dot at
//                                  current time
//   * WX.barChart({values, color, max})
//                                — horizontal bar (used by air/data
//                                  variants)
//
// All accent names use the wx-common vocabulary: blue / yellow / green
// / red / ink / muted. The mapping to roles (rain, UV, sun, …) is
// documented in the design handoff; widget code passes the role
// straight through and trusts the mapping.

const PH = {
  // conditions
  overcast: "cloud", cloud: "cloud", clouds: "clouds",
  rain: "cloud-rain", drizzle: "cloud-rain", showers: "cloud-rain",
  "rain-heavy": "cloud-rain", sleet: "cloud-snow",
  partly: "cloud-sun", "partly-night": "cloud-moon",
  "mostly-sunny": "cloud-sun", fair: "cloud-sun",
  sun: "sun", clear: "sun", uv: "sun", sunny: "sun",
  moon: "moon", "clear-night": "moon", "moon-stars": "moon-stars",
  fog: "cloud-fog", haze: "cloud-fog", mist: "cloud-fog",
  storm: "cloud-lightning", thunder: "cloud-lightning", lightning: "lightning",
  snow: "cloud-snow", snowflake: "snowflake",
  rainbow: "rainbow",
  // metrics
  humidity: "drop", drop: "drop", dew: "drop-half-bottom",
  rainprob: "umbrella-simple",
  wind: "wind", gust: "wind",
  gauge: "gauge", pressure: "gauge",
  eye: "eye", visibility: "eye",
  thermometer: "thermometer-simple", temp: "thermometer-simple",
  "thermo-cold": "thermometer-cold", "thermo-hot": "thermometer-hot",
  // sun & moon
  sunrise: "sun-horizon", sunset: "sun-horizon",
  daylength: "hourglass-medium", noon: "sun",
  clock: "clock", calendar: "calendar-blank",
  // pollen / nature
  tree: "tree", grass: "plant", weed: "flower", flower: "flower-tulip",
  leaf: "leaf", pollen: "flower-lotus",
  // wind / direction
  compass: "compass", arrow: "navigation-arrow", direction: "navigation-arrow",
  // air quality
  pm: "circles-three", pm2: "circle", ozone: "sun-dim", no2: "factory",
  so2: "drop", co: "wind", air: "wind", leafy: "leaf",
  // warnings
  warning: "warning", alert: "warning-octagon", hazard: "warning-octagon",
  marine: "waves", waves: "waves",
  flag: "flag-pennant", info: "warning-circle",
  flood: "drop", fire: "flame", heat: "thermometer-hot", frost: "thermometer-cold",
};

function phName(name) {
  return PH[name] || name; // pass-through for raw Phosphor names
}

const ACCENTS = ["blue", "yellow", "green", "red", "ink", "muted"];
function col(a) {
  if (a === "muted") return "var(--wx-ink-60)";
  if (a === "ink") return "var(--wx-ink)";
  if (ACCENTS.includes(a)) return `var(--wx-${a})`;
  return "var(--wx-ink)";
}
function tint(a) {
  if (a === "ink") return "var(--wx-paper-3)";
  if (a === "muted") return "var(--wx-paper-2)";
  if (ACCENTS.includes(a)) return `var(--wx-${a}-t)`;
  return "var(--wx-paper-2)";
}
function inkOn(a) {
  // Yellow + muted want dark text on top; everything else is light.
  if (a === "yellow" || a === "muted") return "var(--wx-ink)";
  return "var(--wx-paper)";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function tnum(html) {
  return `<span class="wx-tnum">${html}</span>`;
}

function icon(name, { size = 18, color = "var(--wx-ink)", weight = "bold" } = {}) {
  const cls = weight === "fill" ? "ph-fill" : weight === "regular" ? "ph" : "ph-bold";
  return `<i class="${cls} ph-${phName(name)}" style="font-size:${size}px;color:${color};line-height:1" aria-hidden="true"></i>`;
}

function darkHeader({ title, accent = "blue", right = "" }) {
  // ``right`` is treated as HTML so callers can drop in an icon
  // followed by a time/label (e.g. ``WX.icon("play") · PLAYING``).
  // Title remains escaped since it's the variable user-supplied piece.
  return `
    <div class="wx-header-dark">
      <span class="wx-header-chip" style="background:${col(accent)}"></span>
      <span class="wx-header-title">${escapeHtml(title || "").toUpperCase()}</span>
      ${right ? `<span class="wx-header-meta">${right}</span>` : ""}
    </div>
  `;
}

// SVG sun arc — the rising/setting curve, with a dot at "now".
// rise/set/now are minutes-since-midnight; pass color as a CSS value.
function sunArc({ rise, set, now, color = "var(--wx-yellow)", width = 230, height = 120 } = {}) {
  const cx = width / 2;
  const cy = height - 6;
  const r = Math.min(width / 2 - 4, height - 20);
  const span = Math.max(1, set - rise);
  const f = Math.max(0, Math.min(1, (now - rise) / span));
  const ang = Math.PI - f * Math.PI;
  const px = cx + r * Math.cos(ang);
  const py = cy - r * Math.sin(ang);
  return `
    <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
      <path d="M${cx - r} ${cy} A${r} ${r} 0 0 1 ${cx + r} ${cy}"
            fill="none" stroke="rgba(27,26,22,.16)" stroke-width="2.5" stroke-dasharray="2 5" />
      <path d="M${cx - r} ${cy} A${r} ${r} 0 0 1 ${px} ${py}"
            fill="none" stroke="${color}" stroke-width="3" />
      <circle cx="${px}" cy="${py}" r="6" fill="${color}" />
    </svg>
  `;
}

// Horizontal value-bar — used by air-quality + the "Data" current
// variant for stat rows. ``value`` and ``max`` set the fill width.
function barChart({ value, max = 100, color = "var(--wx-blue)", height = 9, bg = "rgba(27,26,22,.1)" } = {}) {
  const f = Math.max(0, Math.min(1, Number(value) / Math.max(1, Number(max))));
  return `
    <div style="height:${height}px;background:${bg};">
      <div style="width:${(f * 100).toFixed(1)}%;height:100%;background:${color};"></div>
    </div>
  `;
}

export const WX = {
  PH,
  phName,
  col,
  tint,
  inkOn,
  icon,
  escapeHtml,
  tnum,
  darkHeader,
  sunArc,
  barChart,
};

// Some widgets prefer a global rather than ES-module access (cell
// client.js modules are imported dynamically and some browsers race
// the resolution). Expose it on window too for those.
if (typeof window !== "undefined") {
  window.WX = WX;
}
