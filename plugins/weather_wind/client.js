// weather_wind — Spectra stat archetype, with a compass rose carrying
// the wind direction next to the hero number.
//
// Left half: 8-point compass rose with a needle pointing at the bearing
// the wind is *coming from*. Right half: wind speed as the hero number,
// caption with Beaufort state + cardinal direction, gust delta when
// gusts run meaningfully above the steady speed. The rose gives the
// widget a real visual centrepiece instead of a tiny arrow glyph next
// to the number.

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

// 8-point compass rose. The cardinal points (N/E/S/W) get a longer
// outer tick; intercardinals are subtler. The needle is a triangular
// arrow rotated to ``bearing`` degrees — points at where the wind is
// blowing FROM (meteorological convention).
function compassSvg(bearing, accent) {
  const safeBearing = Number.isFinite(bearing) ? bearing : null;
  const tickAt = (deg, len, color, weight) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    const r1 = 44;
    const r2 = 44 - len;
    return `<line x1="${(r1 * Math.cos(rad)).toFixed(2)}" y1="${(r1 * Math.sin(rad)).toFixed(2)}"
                   x2="${(r2 * Math.cos(rad)).toFixed(2)}" y2="${(r2 * Math.sin(rad)).toFixed(2)}"
                   stroke="${color}" stroke-width="${weight}" stroke-linecap="round"/>`;
  };
  const labelAt = (deg, text, big) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    const r = 33;
    const x = r * Math.cos(rad);
    const y = r * Math.sin(rad);
    const size = big ? 7 : 5;
    const weight = big ? 800 : 700;
    return `<text x="${x.toFixed(2)}" y="${y.toFixed(2)}"
                   text-anchor="middle" dominant-baseline="central"
                   font-size="${size}" font-weight="${weight}"
                   fill="var(--text-secondary)">${text}</text>`;
  };
  const cardinals = [
    [0, "N"], [90, "E"], [180, "S"], [270, "W"],
  ];
  const intercardinals = [45, 135, 225, 315];
  const ticks = [
    ...cardinals.map(([deg]) => tickAt(deg, 8, "var(--text-secondary)", 1.5)),
    ...intercardinals.map((deg) => tickAt(deg, 4, "var(--text-muted)", 1)),
  ].join("");
  const labels = [
    ...cardinals.map(([deg, t]) => labelAt(deg, t, true)),
  ].join("");
  // Needle — a triangle pointing toward the bearing. Rotates from the
  // centre. When bearing is missing, the needle is hidden and only the
  // rose is shown.
  const needle = safeBearing != null ? `
    <g transform="rotate(${safeBearing.toFixed(1)})">
      <polygon points="0,-32 6,8 0,4 -6,8" fill="${accent}"/>
      <circle r="3" fill="${accent}"/>
    </g>` : "";
  return `
    <svg viewBox="-50 -50 100 100" preserveAspectRatio="xMidYMid meet"
         style="width:100%;height:100%;display:block">
      <circle r="44" fill="none" stroke="var(--surface-sunken)" stroke-width="1.5"/>
      <circle r="36" fill="none" stroke="var(--surface-sunken)" stroke-width="0.8" opacity="0.5"/>
      ${ticks}
      ${labels}
      ${needle}
    </svg>`;
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
  const beaufortLabel = data.beaufortLabel || "";
  const gust = Number(data.gust);
  const speedN = Number(data.speed);
  const gustDelta = !Number.isNaN(gust) && !Number.isNaN(speedN) && gust > speedN
    ? `<span class="stat-delta" style="color:var(--accent-1)"><i class="ph-bold ph-arrow-fat-up"></i>${fmtNum(gust)} gust</span>`
    : "";

  const captionBits = [beaufortLabel, dir].filter(Boolean).map(escapeHtml).join(" · ");
  const bearing = Number(data.bearing);
  const compass = compassSvg(Number.isFinite(bearing) ? bearing : null, "var(--accent-4)");

  const layout = `
    .wind-body {
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: var(--space-5);
    }
    .wind-compass {
      flex: 0 0 auto;
      width: clamp(5em, 38cqmin, 12em);
      aspect-ratio: 1;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .wind-data {
      flex: 1 1 auto;
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
    .wind-caption {
      font-size: var(--fs-body);
      font-weight: var(--fw-semi);
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      gap: var(--space-3);
      flex-wrap: wrap;
    }
    /* Tall portrait cells: stack the compass over the data so neither
       has to crush itself into a sliver. */
    @container (max-aspect-ratio: 0.9) {
      .wind-body { flex-direction: column; gap: var(--space-3); }
      .wind-compass { width: clamp(5em, 60cqw, 9em); }
      .wind-data { align-items: center; text-align: center; }
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
          <div class="wind-compass">${compass}</div>
          <div class="wind-data">
            <div class="wind-speed">
              ${escapeHtml(speed)}<span class="unit">${escapeHtml(unit)}</span>
            </div>
            <div class="wind-caption">
              ${captionBits ? `<span>${captionBits}</span>` : ""}
              ${gustDelta}
            </div>
          </div>
        </div>
      </div>
    </div>`;
}
