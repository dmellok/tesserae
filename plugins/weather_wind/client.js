// weather_wind — Spectra stat archetype with a compass rose that
// telegraphs both the current wind direction (needle pointing FROM
// the source) and the 24-hour directional distribution (petals sized
// by the speed-weighted rose the server pre-computes). Right column
// carries the speed hero + Beaufort chip + gust delta + (lg only) a
// 12-hour gust sparkline.

import { sparkline, tokens } from "../../static/spectra-chart.js";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtNum(v) {
  if (v == null) return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  return n >= 100 ? String(Math.round(n)) : String(Math.round(n * 10) / 10);
}

// Beaufort severity → accent token. Calm bands stay neutral; force
// climbs through green / ochre / terracotta / plum so the chip's tint
// reads as "how alarmed should I be" without needing the descriptor.
function beaufortAccent(n) {
  if (!Number.isFinite(n) || n < 0) return "var(--text-muted)";
  if (n === 0) return "var(--text-secondary)";
  if (n <= 3) return "var(--accent-3)"; // moss — light/gentle breezes
  if (n <= 5) return "var(--accent-2)"; // ochre — moderate/fresh
  if (n <= 7) return "var(--accent-1)"; // terracotta — strong/near gale
  return "var(--accent-6)";              // plum — gale and worse
}

// 8-point petal wind rose. Each cardinal+intercardinal direction gets
// a wedge whose outward extent is proportional to the speed-weighted
// magnitude the server reports for that bucket. The current direction
// needle stays overlaid in accent-2 so it reads on top of the petals
// rather than competing with them for the rose's centre.
function roseSvg(bearing, rose) {
  const points = Array.isArray(rose) ? rose : [];
  const maxV = Math.max(1, ...points.map((p) => Number(p?.v) || 0));
  const cardinals = [
    [0, "N"], [90, "E"], [180, "S"], [270, "W"],
  ];
  const intercardinals = [45, 135, 225, 315];

  // Petal wedges. halfWidth controls the angular thickness of each
  // petal (35 degrees = a chunky flower shape with a clear gap
  // between adjacent petals). innerR is the inner radius so petals
  // don't all stab through the centre dot.
  const innerR = 4;
  const outerMax = 42;
  const halfWidth = 17.5;
  const minScale = 0.18;
  const petals = points.map((p) => {
    const v = Number(p?.v) || 0;
    const scaled = v <= 0 ? minScale : minScale + (1 - minScale) * (v / maxV);
    const r = outerMax * scaled;
    const angle = _bearingFromCompass(p?.d);
    if (angle == null) return "";
    const a1 = ((angle - halfWidth - 90) * Math.PI) / 180;
    const a2 = ((angle + halfWidth - 90) * Math.PI) / 180;
    const ix1 = innerR * Math.cos(a1);
    const iy1 = innerR * Math.sin(a1);
    const ix2 = innerR * Math.cos(a2);
    const iy2 = innerR * Math.sin(a2);
    const ox1 = r * Math.cos(a1);
    const oy1 = r * Math.sin(a1);
    const ox2 = r * Math.cos(a2);
    const oy2 = r * Math.sin(a2);
    return `
      <path d="M ${ix1.toFixed(2)} ${iy1.toFixed(2)}
               L ${ox1.toFixed(2)} ${oy1.toFixed(2)}
               A ${r.toFixed(2)} ${r.toFixed(2)} 0 0 1 ${ox2.toFixed(2)} ${oy2.toFixed(2)}
               L ${ix2.toFixed(2)} ${iy2.toFixed(2)} Z"
            fill="var(--accent-4)" opacity="0.55"/>`;
  }).join("");

  // Cardinal tick labels (N / E / S / W).
  const labelAt = (deg, text) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    const r = 47;
    const x = r * Math.cos(rad);
    const y = r * Math.sin(rad);
    return `<text x="${x.toFixed(2)}" y="${y.toFixed(2)}"
                   text-anchor="middle" dominant-baseline="central"
                   font-size="7" font-weight="800"
                   fill="var(--text-secondary)">${text}</text>`;
  };
  const labels = cardinals.map(([deg, t]) => labelAt(deg, t)).join("");

  // Light intercardinal ticks so the rose still reads as a compass
  // even before petals + needle paint.
  const tickAt = (deg, len, color, weight) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    const r1 = 43;
    const r2 = 43 - len;
    return `<line x1="${(r1 * Math.cos(rad)).toFixed(2)}" y1="${(r1 * Math.sin(rad)).toFixed(2)}"
                   x2="${(r2 * Math.cos(rad)).toFixed(2)}" y2="${(r2 * Math.sin(rad)).toFixed(2)}"
                   stroke="${color}" stroke-width="${weight}" stroke-linecap="round"/>`;
  };
  const ticks = intercardinals.map((deg) => tickAt(deg, 3, "var(--text-muted)", 1)).join("");

  // Current direction needle — points OUTWARD at the bearing the wind
  // is coming FROM (meteorological convention). Outline form (no
  // fill) so the teal petals beneath remain visible through the
  // arrow's interior; the centre dot stays solid as the rotation
  // anchor. Accent-2 (ochre) holds against the teal petals without
  // swallowing them.
  const safeBearing = Number.isFinite(bearing) ? bearing : null;
  const needle = safeBearing != null ? `
    <g transform="rotate(${safeBearing.toFixed(1)})">
      <polygon points="0,-40 5,-2 -5,-2"
               fill="none" stroke="var(--accent-2)" stroke-width="2.6"
               stroke-linejoin="round" stroke-linecap="round"/>
      <circle r="3.5" fill="var(--accent-2)"/>
    </g>` : `<circle r="3" fill="var(--text-muted)"/>`;

  return `
    <svg class="wind-rose-svg" viewBox="-52 -52 104 104"
         preserveAspectRatio="xMidYMid meet"
         style="width:100%;height:100%;display:block">
      <circle r="43" fill="none" stroke="var(--surface-sunken)" stroke-width="1"/>
      ${ticks}
      ${petals}
      ${labels}
      ${needle}
    </svg>`;
}

// Compass label → bearing in degrees. Server's rose buckets carry the
// label (N, NE, ...) which is more legible than indexing by position
// in case the array order changes upstream.
function _bearingFromCompass(label) {
  const map = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 };
  return Object.prototype.hasOwnProperty.call(map, label) ? map[label] : null;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="weather_wind">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Wind</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.place || data.label || "";
  const speed = fmtNum(data.speed);
  const unit = data.unit || "km/h";
  const dir = data.dir || "";
  const beaufortN = Number(data.beaufort);
  const beaufortLabel = data.beaufortLabel || "";
  const bftAccent = beaufortAccent(beaufortN);
  const gust = Number(data.gust);
  const speedN = Number(data.speed);
  const gustDelta = !Number.isNaN(gust) && !Number.isNaN(speedN) && gust > speedN
    ? `<span class="wind-gust" title="Gusting to ${fmtNum(gust)} ${escapeHtml(unit)}">
         <i class="ph-bold ph-arrow-fat-up"></i>${fmtNum(gust)}
       </span>`
    : "";

  const beaufortChip = Number.isFinite(beaufortN) ? `
    <span class="wind-beaufort"
          style="background:color-mix(in oklab, ${bftAccent} 16%, transparent);color:${bftAccent}">
      <span class="wind-beaufort-num">B${beaufortN}</span>
      ${beaufortLabel ? `<span class="wind-beaufort-label">${escapeHtml(beaufortLabel)}</span>` : ""}
    </span>` : "";

  const bearing = Number(data.bearing);
  const rose = roseSvg(Number.isFinite(bearing) ? bearing : null, data.rose);

  const gustSeries = Array.isArray(data.gustSeries) ? data.gustSeries : [];
  const hasGustChart = gustSeries.length >= 2;

  const layout = `
    .wind-body {
      flex: 1 1 auto;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, auto) minmax(0, 1fr);
      grid-template-rows: 1fr;
      align-items: center;
      gap: var(--space-5);
    }
    .wind-rose {
      grid-column: 1;
      grid-row: 1;
      width: clamp(6em, 42cqmin, 14em);
      aspect-ratio: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      /* The rose has an intrinsic width (clamp above), so without an
         explicit justify-self the grid item left-aligns inside its
         track when the layout stacks for portrait cells. Centering
         here keeps it on the cell's horizontal centreline regardless
         of whether the layout is row or column. */
      justify-self: center;
    }
    .wind-data {
      grid-column: 2;
      grid-row: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: var(--space-2);
      justify-content: center;
    }
    .wind-speed {
      font-size: var(--fs-display);
      font-weight: var(--fw-black);
      line-height: var(--lh-tight);
      letter-spacing: var(--ls-tight);
      display: flex;
      align-items: baseline;
      gap: 0.15em;
    }
    .wind-speed .unit {
      font-size: 0.4em;
      font-weight: var(--fw-bold);
      color: var(--text-secondary);
    }
    .wind-dir {
      font-size: var(--fs-body);
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
    }
    .wind-chips {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--space-2) var(--space-3);
    }
    /* Beaufort chip — number + descriptor pill in the band accent. The
       background uses color-mix so the chip works on every theme
       without per-band overrides. */
    .wind-beaufort {
      display: inline-flex;
      align-items: baseline;
      gap: 0.4em;
      padding: 0.25em 0.7em;
      border-radius: var(--pill-radius, var(--radius-0));
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      font-size: var(--fs-label);
      text-transform: var(--label-transform, uppercase);
      line-height: 1.1;
    }
    .wind-beaufort-num {
      font-weight: var(--fw-black);
      font-variant-numeric: tabular-nums;
    }
    .wind-gust {
      display: inline-flex;
      align-items: center;
      gap: 0.2em;
      font-size: var(--fs-body);
      font-weight: var(--fw-bold);
      color: var(--accent-1);
      font-variant-numeric: tabular-nums;
    }
    .wind-gust .ph-bold { font-size: 0.85em; }

    /* lg: add the 12-hour gust sparkline below the main row. The
       chart spans the full body width so it reads as a connected
       trend rather than tucked into a corner of the data column. */
    .wind-gust-chart { display: none; }
    @container (min-width: 700px) {
      .wind-body {
        grid-template-rows: 1fr auto;
        row-gap: var(--space-4);
      }
      .wind-rose { width: clamp(8em, 40cqmin, 16em); }
      .wind-gust-chart {
        display: block;
        grid-column: 1 / -1;
        grid-row: 2;
        height: clamp(2.5em, 12cqmin, 6em);
        position: relative;
      }
    }

    /* xs/sm tight: shrink the chips so they don't wrap into a third
       row beneath a tiny cell. */
    @container (max-width: 280px) {
      .wind-beaufort-label { display: none; }
      .wind-body { gap: var(--space-3); }
    }

    /* Tall portrait cells: stack the rose over the data column. */
    @container (max-aspect-ratio: 0.9) {
      .wind-body { grid-template-columns: 1fr; grid-template-rows: auto auto; gap: var(--space-3); }
      .wind-rose { grid-column: 1; grid-row: 1; width: clamp(6em, 60cqw, 11em); }
      .wind-data { grid-column: 1; grid-row: 2; align-items: center; text-align: center; }
      .wind-chips { justify-content: center; }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="weather_wind">
      <div class="w-title">
        <i class="ph-bold ph-wind" style="color:var(--accent-4)"></i>
        <h3>${escapeHtml(label || "Wind")}</h3>
        <span class="w-title-meta">${escapeHtml(data.time || "")}</span>
      </div>
      <div class="w-body">
        <div class="wind-body">
          <div class="wind-rose">${rose}</div>
          <div class="wind-data">
            <div class="wind-speed">
              ${escapeHtml(speed)}<span class="unit">${escapeHtml(unit)}</span>
            </div>
            ${dir ? `<div class="wind-dir">From ${escapeHtml(dir)}</div>` : ""}
            <div class="wind-chips">
              ${beaufortChip}
              ${gustDelta}
            </div>
          </div>
          ${hasGustChart ? '<div class="wind-gust-chart"><canvas></canvas></div>' : ""}
        </div>
      </div>
    </div>`;

  if (hasGustChart) {
    const canvas = shadow.querySelector(".wind-gust-chart canvas");
    if (canvas) {
      const t = tokens(shadow.host);
      sparkline(canvas, gustSeries, t.accent1);
    }
  }
}
