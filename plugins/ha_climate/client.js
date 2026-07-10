// ha_climate, Spectra status archetype with a radial thermostat
// dial as the centrepiece. Single entity → one big dial with the
// current temp at the centre, the target marked on the arc, and
// mode/action/humidity chips below. Multiple entities → a responsive
// grid of compact dials, one per climate entity, so a whole-house
// view reads at a glance.

const MODE_PH = {
  fire: "ph-fire",
  snowflake: "ph-snowflake",
  "thermometer-simple": "ph-thermometer-simple",
  drop: "ph-drop",
  fan: "ph-fan",
  power: "ph-power",
  question: "ph-question",
};

const MODE_ACCENT = {
  heat: "var(--accent-1)",      // terracotta
  cool: "var(--accent-4)",      // teal
  heat_cool: "var(--accent-3)", // moss
  auto: "var(--accent-3)",
  dry: "var(--accent-5)",       // slate blue
  fan_only: "var(--accent-5)",
  off: "var(--text-muted)",
};

const ACTION_PH = {
  heating: "ph-fire",
  cooling: "ph-snowflake",
  drying: "ph-drop",
  fan: "ph-fan",
  idle: "ph-power",
  off: "ph-power",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function modeIcon(name) {
  return MODE_PH[name] || "ph-thermometer-simple";
}

function modeAccent(mode) {
  return MODE_ACCENT[mode] || "var(--text-secondary)";
}

function tempStr(v, unit) {
  if (v == null || v === "") return "-";
  return `${escapeHtml(v)}${unit ? escapeHtml(unit) : "°"}`;
}

function tempBounds(item) {
  const lo = Number.parseFloat(item.min_temp);
  const hi = Number.parseFloat(item.max_temp);
  const min = Number.isFinite(lo) ? lo : 10;
  const max = Number.isFinite(hi) ? hi : 30;
  if (max - min < 1) return { min, max: min + 10 };
  return { min, max };
}

function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

// Convert a degree angle (0 = east, CCW positive in math; SVG's
// y-down convention means visually CCW maps to CW because the y
// axis is flipped) to a point at the given radius from (cx, cy).
function polarSvg(cx, cy, angleDeg, radius) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
}

// Radial thermostat dial, 240° arc, gap at the bottom. Track in
// surface-sunken; the filled arc grows from min → current temp in the
// mode accent. Target temp shown as a chunky perpendicular tick that
// crosses the arc. Setpoint range (target_low → target_high) shown as
// a translucent band on the arc when both are present. Centre carries
// the current temp number + mode label.
function thermostatDial(item, opts = {}) {
  const { min, max } = tempBounds(item);
  const range = max - min;
  const accent = modeAccent(item.mode);
  const muted = item.unavailable;
  const color = muted ? "var(--text-muted)" : accent;
  // The dial is rendered at a fixed viewBox; the parent container
  // controls its actual on-screen size. Use a 200-unit viewBox so
  // proportions scale cleanly and SVG strokes don't look chunky at
  // sm or wispy at lg.
  const big = opts.size !== "compact";
  const W = 200, H = 200;
  const cx = W / 2, cy = H / 2;
  const r = big ? 78 : 76;
  const strokeW = big ? 14 : 12;

  // 240° sweep starting at 150° (lower-left), going CW through 270°
  // (top), 360°/0° (right), down to 30° (lower-right). Gap of 120°
  // sits at the bottom.
  const startAngle = 150;
  const sweep = 240;
  const startPt = polarSvg(cx, cy, startAngle, r);
  const endPt = polarSvg(cx, cy, startAngle + sweep, r);

  function tToAngle(t) {
    return startAngle + clamp01((t - min) / range) * sweep;
  }
  function tToPt(t, radius = r) {
    return polarSvg(cx, cy, tToAngle(t), radius);
  }

  // Track, sunken arc the gauge sits in.
  const trackPath =
    `M ${startPt.x.toFixed(2)} ${startPt.y.toFixed(2)} ` +
    `A ${r} ${r} 0 1 1 ${endPt.x.toFixed(2)} ${endPt.y.toFixed(2)}`;

  // Filled arc from start to current temp.
  const currentT = Number.parseFloat(item.current);
  let fillPath = "";
  if (Number.isFinite(currentT)) {
    const norm = clamp01((currentT - min) / range);
    if (norm > 0.001) {
      const curPt = tToPt(currentT);
      const largeArc = norm * sweep > 180 ? 1 : 0;
      fillPath =
        `M ${startPt.x.toFixed(2)} ${startPt.y.toFixed(2)} ` +
        `A ${r} ${r} 0 ${largeArc} 1 ${curPt.x.toFixed(2)} ${curPt.y.toFixed(2)}`;
    }
  }

  // Setpoint band (auto / heat_cool with target_low + target_high).
  const lowT = Number.parseFloat(item.target_low);
  const highT = Number.parseFloat(item.target_high);
  const hasBand = Number.isFinite(lowT) && Number.isFinite(highT) && Math.abs(highT - lowT) > 0;
  let bandPath = "";
  if (hasBand) {
    const lo = Math.min(lowT, highT);
    const hi = Math.max(lowT, highT);
    const loPt = tToPt(lo);
    const hiPt = tToPt(hi);
    const largeArc = ((hi - lo) / range) * sweep > 180 ? 1 : 0;
    bandPath =
      `M ${loPt.x.toFixed(2)} ${loPt.y.toFixed(2)} ` +
      `A ${r} ${r} 0 ${largeArc} 1 ${hiPt.x.toFixed(2)} ${hiPt.y.toFixed(2)}`;
  }

  // Target marker, a perpendicular tick crossing the arc.
  const targetT = Number.parseFloat(item.target);
  let targetTick = "";
  if (Number.isFinite(targetT)) {
    const inner = tToPt(targetT, r - strokeW / 2 - 4);
    const outer = tToPt(targetT, r + strokeW / 2 + 4);
    targetTick = `
      <line x1="${inner.x.toFixed(2)}" y1="${inner.y.toFixed(2)}"
            x2="${outer.x.toFixed(2)}" y2="${outer.y.toFixed(2)}"
            stroke="var(--text-primary)" stroke-width="2.5"
            stroke-linecap="round"/>
      <circle cx="${outer.x.toFixed(2)}" cy="${outer.y.toFixed(2)}" r="3"
              fill="var(--text-primary)"/>`;
  }

  // Calibration tick marks: every 10% along the arc.
  const ticks = [];
  for (let i = 0; i <= 10; i++) {
    const tt = startAngle + (i / 10) * sweep;
    const inner = polarSvg(cx, cy, tt, r - strokeW / 2 - 2);
    const outer = polarSvg(cx, cy, tt, r - strokeW / 2 - 6);
    const major = i === 0 || i === 5 || i === 10;
    ticks.push(`
      <line x1="${inner.x.toFixed(2)}" y1="${inner.y.toFixed(2)}"
            x2="${outer.x.toFixed(2)}" y2="${outer.y.toFixed(2)}"
            stroke="var(--text-muted)" stroke-width="${major ? 1.4 : 0.8}"
            opacity="${major ? 0.8 : 0.45}"/>`);
  }

  // Min / max labels at the bottom corners.
  const minPt = polarSvg(cx, cy, startAngle, r + strokeW + 8);
  const maxPt = polarSvg(cx, cy, startAngle + sweep, r + strokeW + 8);

  // Centre lockup: big temp number + small mode label below. Sized
  // proportional to the 200-unit viewBox so the SVG scales cleanly
  // with whatever the parent decides for the wrapper.
  const fontTemp = big ? 56 : 44;
  const fontLabel = big ? 13 : 11;
  const fontUnit = big ? 22 : 18;
  const modeLabel = (item.mode_label || "").toUpperCase();
  const currentLabel = Number.isFinite(currentT) ? Math.round(currentT) : "-";
  const unitTxt = item.unit || "°";

  return `
    <svg viewBox="0 0 ${W} ${H}" aria-hidden="true"
         style="width:100%;height:100%" preserveAspectRatio="xMidYMid meet">
      <path d="${trackPath}" fill="none" stroke="var(--surface-sunken)"
            stroke-width="${strokeW}" stroke-linecap="round"/>
      ${hasBand ? `
        <path d="${bandPath}" fill="none" stroke="${color}"
              stroke-width="${strokeW}" stroke-linecap="round" opacity="0.35"/>` : ""}
      ${fillPath ? `
        <path d="${fillPath}" fill="none" stroke="${color}"
              stroke-width="${strokeW}" stroke-linecap="round"/>` : ""}
      ${ticks.join("")}
      ${targetTick}
      <text x="${minPt.x.toFixed(2)}" y="${minPt.y.toFixed(2)}"
            text-anchor="end" font-size="11" font-weight="700"
            fill="var(--text-muted)" font-family="var(--font-family)">${Math.round(min)}°</text>
      <text x="${maxPt.x.toFixed(2)}" y="${maxPt.y.toFixed(2)}"
            text-anchor="start" font-size="11" font-weight="700"
            fill="var(--text-muted)" font-family="var(--font-family)">${Math.round(max)}°</text>
      <text x="${cx}" y="${cy + 10}" text-anchor="middle"
            font-size="${fontTemp}" font-weight="900" fill="${color}"
            font-family="var(--font-family)" font-variant-numeric="tabular-nums">${currentLabel}<tspan font-size="${fontUnit}" dx="2">${escapeHtml(unitTxt)}</tspan></text>
      <text x="${cx}" y="${cy + 34}" text-anchor="middle"
            font-size="${fontLabel}" font-weight="900" fill="var(--text-muted)"
            font-family="var(--font-family)" letter-spacing=".1em">${escapeHtml(modeLabel)}</text>
    </svg>`;
}

function humidityChip(item) {
  if (!item.humidity) return "";
  const target = item.humidity_target;
  const tip = target ? `humidity ${item.humidity}% (target ${target}%)` : `humidity ${item.humidity}%`;
  return `
    <span class="climate-humidity" title="${escapeHtml(tip)}">
      <i class="ph-bold ph-drop" style="color:var(--accent-4)"></i>
      <span class="climate-humidity-val">${escapeHtml(item.humidity)}<small>%</small></span>
    </span>`;
}

function actionChip(item, accent) {
  if (!item.action || item.action === item.mode) return "";
  const ph = ACTION_PH[item.action.toLowerCase()] || "ph-arrows-clockwise";
  return `
    <span class="climate-action" style="color:${accent};background:color-mix(in oklab, ${accent} 12%, var(--surface))">
      <i class="ph-bold ${ph}"></i>${escapeHtml(item.action)}
    </span>`;
}

function modePill(item, accent) {
  const muted = item.unavailable;
  if (!item.mode_label) return "";
  return `<span class="pill" style="background:${muted ? "var(--text-muted)" : accent}">${escapeHtml(item.mode_label)}</span>`;
}

// Big-dial layout for a single entity. Dial on the left, chips
// (mode pill, action, humidity) on the right + target sub-text.
function renderHero(item) {
  const accent = modeAccent(item.mode);
  const muted = item.unavailable;
  const color = muted ? "var(--text-muted)" : accent;
  const subBits = [];
  if (item.target) subBits.push(`Target ${tempStr(item.target, item.unit)}`);
  else if (item.target_low && item.target_high) {
    subBits.push(`${tempStr(item.target_low, item.unit)}–${tempStr(item.target_high, item.unit)}`);
  }
  if (!subBits.length) subBits.push(escapeHtml(item.mode_label || ""));
  return `
    <div class="climate-hero">
      <div class="climate-dial-wrap">${thermostatDial(item, { size: "big" })}</div>
      <div class="climate-hero-text">
        <span class="climate-name">${escapeHtml(item.name)}</span>
        <span class="climate-target">${subBits.join(" · ")}</span>
        <div class="climate-chip-row">
          ${modePill(item, color)}
          ${actionChip(item, color)}
          ${humidityChip(item)}
        </div>
      </div>
    </div>`;
}

// Compact dial tile for multi-entity grid view. Name above the dial,
// humidity + action drop below as small text. Sized to slot into a
// `repeat(auto-fit, minmax(120px, 1fr))` grid.
function renderTile(item) {
  const accent = modeAccent(item.mode);
  const muted = item.unavailable;
  const color = muted ? "var(--text-muted)" : accent;
  const tip = item.target ? `Target ${tempStr(item.target, item.unit)}` : "";
  const humBit = item.humidity
    ? `<span class="climate-tile-hum"><i class="ph-bold ph-drop" style="color:var(--accent-4)"></i>${escapeHtml(item.humidity)}%</span>`
    : "";
  const actionBit = item.action && item.action !== item.mode
    ? `<span class="climate-tile-action" style="color:${color}">${escapeHtml(item.action)}</span>`
    : "";
  return `
    <div class="climate-tile">
      <div class="climate-tile-head">
        <span class="climate-tile-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
        ${humBit}
      </div>
      <div class="climate-tile-dial">${thermostatDial(item, { size: "compact" })}</div>
      <div class="climate-tile-meta">
        ${tip ? `<small class="climate-tile-target">${escapeHtml(tip)}</small>` : ""}
        ${actionBit}
      </div>
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_climate">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Climate</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (data.empty || !Array.isArray(data.items) || data.items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_climate">
        <div class="w-title"><i class="ph-bold ph-thermometer-simple"></i><h3>${escapeHtml(data.title || "Climate")}</h3></div>
        <div class="w-body"><p class="u-muted">No entities selected.</p></div>
      </div>`;
    return;
  }

  const items = data.items;
  const isMulti = items.length > 1;
  const primary = items[0];

  const title = data.title || (isMulti ? "Climate" : primary.name);
  const heroAccent = modeAccent(primary.mode);

  const layout = `
    .climate-hero {
      display: flex;
      gap: var(--space-3);
      align-items: center;
      flex: 1 1 auto;
      min-height: 0;
    }
    /* Dial scales with the smaller of cqw/cqh so it fills the cell
       rather than capping at a fixed pixel size. Aspect-ratio keeps
       it square; flex-basis: 0 lets the text column take its natural
       width. */
    .climate-dial-wrap {
      flex: 0 0 auto;
      width: min(70%, 60cqmin);
      aspect-ratio: 1;
      max-height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .climate-hero-text {
      display: flex;
      flex-direction: column;
      gap: var(--space-1);
      flex: 1 1 auto;
      min-width: 0;
      justify-content: center;
    }
    .climate-name {
      font-size: var(--fs-headline);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-tight);
      line-height: var(--lh-tight);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .climate-target {
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
    }
    .climate-chip-row {
      display: flex;
      gap: var(--space-2);
      flex-wrap: wrap;
      align-items: center;
    }
    .climate-action {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
    }
    .climate-humidity {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 1px var(--space-1);
      border-radius: 999px;
      background: color-mix(in oklab, var(--accent-4) 12%, var(--surface));
      color: var(--accent-4);
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      font-variant-numeric: tabular-nums;
    }
    .climate-humidity-val small {
      font-size: .7em;
    }
    /* Multi-entity grid, every entity gets a compact dial card.
       Tiles are 200px+ each so the dial inside has room to breathe;
       on lg cells the grid fits more columns and each dial stays
       a generous size. */
    .climate-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: var(--space-3);
      width: 100%;
    }
    .climate-tile {
      display: flex;
      flex-direction: column;
      align-items: stretch;
      gap: var(--space-1);
      padding: var(--space-2);
      border-radius: var(--radius-1);
      background: color-mix(in oklab, var(--text-primary) 3%, transparent);
      min-width: 0;
    }
    .climate-tile-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-1);
    }
    .climate-tile-name {
      font-size: var(--fs-caption);
      font-weight: var(--fw-black);
      letter-spacing: var(--ls-label);
      text-transform: uppercase;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1 1 auto;
      min-width: 0;
    }
    .climate-tile-hum {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      font-size: var(--fs-caption);
      font-weight: var(--fw-bold);
      color: var(--accent-4);
      font-variant-numeric: tabular-nums;
      flex: 0 0 auto;
    }
    .climate-tile-dial {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      aspect-ratio: 1;
      max-height: 14em;
    }
    .climate-tile-meta {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: var(--space-1);
      font-size: var(--fs-caption);
      color: var(--text-muted);
    }
    .climate-tile-target {
      font-weight: var(--fw-bold);
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
    }
    .climate-tile-action {
      font-weight: var(--fw-bold);
      text-transform: uppercase;
      letter-spacing: var(--ls-label);
    }
    /* Portrait cells: stack the dial above the text instead of side-
       by-side, so the dial keeps its breathing room. */
    @container (max-aspect-ratio: 1 / 1) {
      .climate-hero {
        flex-direction: column;
        align-items: center;
      }
      .climate-dial-wrap {
        width: min(80%, 50cqmin);
      }
      .climate-hero-text {
        text-align: center;
        align-items: center;
      }
    }
  `;

  // Fragments (issue #60): the Panels canvas can place just one part of the
  // widget. ``ctx.fragment`` selects which; "full" (default) is the whole
  // card. The dial + chips fragments key off the primary entity.
  const frag = ctx?.fragment || "full";
  if (frag === "dial") {
    shadow.innerHTML = `
      ${css}
      <style>.climate-frag-dial { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; }</style>
      <div class="w" data-widget="ha_climate"><div class="w-body"><div class="climate-frag-dial">${thermostatDial(primary, { size: "big" })}</div></div></div>`;
    return;
  }
  if (frag === "chips") {
    const color = primary.unavailable ? "var(--text-muted)" : heroAccent;
    const subBits = [];
    if (primary.target) subBits.push(`Target ${tempStr(primary.target, primary.unit)}`);
    else if (primary.target_low && primary.target_high) {
      subBits.push(`${tempStr(primary.target_low, primary.unit)}–${tempStr(primary.target_high, primary.unit)}`);
    }
    shadow.innerHTML = `
      ${css}
      <style>${layout}
        .climate-frag-chips { display: flex; flex-direction: column; gap: var(--space-1); justify-content: center; height: 100%; }
      </style>
      <div class="w" data-widget="ha_climate"><div class="w-body"><div class="climate-frag-chips">
        <span class="climate-name">${escapeHtml(primary.name)}</span>
        ${subBits.length ? `<span class="climate-target">${subBits.join(" · ")}</span>` : ""}
        <div class="climate-chip-row">
          ${modePill(primary, color)}
          ${actionChip(primary, color)}
          ${humidityChip(primary)}
        </div>
      </div></div></div>`;
    return;
  }

  // Title-bar icon picks up the primary entity's mode accent.
  const body = isMulti
    ? `<div class="climate-grid">${items.map(renderTile).join("")}</div>`
    : renderHero(primary);

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="ha_climate">
      <div class="w-title">
        <i class="ph-bold ph-thermometer-simple" style="color:${heroAccent}"></i>
        <h3>${escapeHtml(title)}</h3>
        ${isMulti ? `<span class="w-title-meta">${items.length} ZONES</span>` : ""}
      </div>
      <div class="w-body">${body}</div>
    </div>`;
}
