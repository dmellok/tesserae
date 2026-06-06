// weather_pollen_count, Spectra status archetype, one tile per pollen
// type. Each tile carries: a botanical icon sized by severity (a
// "Very High" weed tile dwarfs a "Low" grass tile so the row reads
// "weed is the worst today" before you scan any text), the pollen
// name, its numeric reading, a 4-step severity bar (Low / Moderate /
// High / V.High) and the level word in the matching accent. Tile
// background uses the level's --*-soft tint so the row paints as
// three blocks of colour, not three identical cards.
//
// The server breakdown returns level as a 0-100 percent of the global
// scale; this client derives the LEVEL WORD from value against the
// same thresholds the server uses for the overall band (30 / 100 /
// 300), so the per-tile accent + bar agree with the title-bar
// headline.

const LEVEL_ORDER = ["Low", "Moderate", "High", "Very high"];

const LEVEL_ACCENT = {
  Low: "var(--accent-3)",         // moss, quiet good
  Moderate: "var(--accent-2)",    // ochre, caution
  High: "var(--accent-1)",        // terracotta, alert
  "Very high": "var(--accent-6)", // plum, extreme
};
const LEVEL_SOFT = {
  Low: "var(--accent-3-soft)",
  Moderate: "var(--accent-2-soft)",
  High: "var(--accent-1-soft)",
  "Very high": "var(--accent-6-soft)",
};
const LEVEL_SHORT = {
  Low: "LOW",
  Moderate: "MODERATE",
  High: "HIGH",
  "Very high": "V. HIGH",
};

// Per-level icon scale multiplier. Low pollen days look subdued, High
// days hit you in the face. The base font-size still clamps against
// cqmin so the icon stays readable on small cells.
const LEVEL_ICON_SCALE = {
  Low: 0.75,
  Moderate: 1.0,
  High: 1.25,
  "Very high": 1.5,
};

const POLLEN_PH = {
  tree: "ph-tree",
  grass: "ph-plant",
  weed: "ph-flower",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function levelAccent(level) {
  return LEVEL_ACCENT[level] || "var(--text-muted)";
}
function levelSoft(level) {
  return LEVEL_SOFT[level] || "var(--surface-sunken)";
}
function levelShort(level) {
  return LEVEL_SHORT[level] || "-";
}

// 30 / 100 / 300 grams-per-m³ band thresholds match the server's
// OVERALL_BANDS so per-tile and overall colour bands always agree.
function levelWordFromValue(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  if (n <= 30) return "Low";
  if (n <= 100) return "Moderate";
  if (n <= 300) return "High";
  return "Very high";
}

// 4-segment severity bar. Segments up to and including the current
// level fill in that level's accent; empty segments use a translucent
// overlay of --text-primary so they hold contrast against every tile
// background (the previous surface-sunken disappeared into the
// accent-*-soft tile fills). Reads as "this pollen sits in band 2 of
// 4" without parsing the word.
function severityBar(levelWord) {
  const idx = LEVEL_ORDER.indexOf(levelWord);
  const fillColor = levelAccent(levelWord);
  const emptyColor = "color-mix(in oklab, var(--text-primary) 25%, transparent)";
  return `
    <div class="pollen-bar" aria-hidden="true">
      ${[0, 1, 2, 3].map((i) => `
        <span class="pollen-bar-seg" style="background:${i <= idx && idx >= 0 ? fillColor : emptyColor}"></span>
      `).join("")}
    </div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="weather_pollen_count">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Pollen</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const label = data.place || data.label || "";
  const overall = data.level || "";
  const breakdown = Array.isArray(data.breakdown) ? data.breakdown : [];

  const tiles = breakdown.map((item) => {
    const ph = POLLEN_PH[item.icon] || "ph-plant";
    // Server's item.level is a percentage; derive the word from value
    // so the accent + bar reflect the same thresholds the title-bar
    // headline uses. null means the upstream had no reading.
    const levelWord = levelWordFromValue(item.value);
    const hasData = levelWord != null;
    const displayLevel = levelWord || "-";
    const accent = levelAccent(displayLevel);
    const soft = levelSoft(displayLevel);
    const short = levelShort(displayLevel);
    const iconScale = LEVEL_ICON_SCALE[displayLevel] || 1.0;
    // No-data tiles collapse the value + bar + level word into a
    // single centered glyph. Three stacked em-dashes read as a tiny
    // mess; a ph-minus-circle reads as "no data" at any size.
    const dataBlock = hasData
      ? `
        <span class="pollen-value">${escapeHtml(String(item.value))}</span>
        ${severityBar(levelWord)}
        <span class="pollen-level" style="color:${accent}">${escapeHtml(short)}</span>`
      : `
        <i class="ph-bold ph-minus-circle pollen-no-data" aria-label="No data"></i>`;
    return `
      <div class="pollen-tile ${hasData ? "" : "is-no-data"}" data-level="${escapeHtml(displayLevel)}" style="background:${soft}">
        <i class="ph-bold ${ph} pollen-icon" style="color:${accent};--pollen-icon-scale:${iconScale}"></i>
        <span class="pollen-name">${escapeHtml(item.label || "")}</span>
        ${dataBlock}
      </div>`;
  }).join("");

  const layout = `
    .pollen-row {
      flex: 1 1 auto;
      min-height: 0;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: var(--space-3);
    }
    /* Tile is a flex column with everything centered, so the icon +
       name + value + bar + level word sit as a single lockup in the
       middle of the tile. Vertical centering is critical: when a tile
       is tall (lg cells get ~400 wide × 800 tall slices), a content
       cluster pinned to top or bottom reads as broken. */
    .pollen-tile {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--space-2);
      padding: var(--space-4) var(--space-3);
      border-radius: var(--radius-0);
      min-height: 0;
      min-width: 0;
      container-type: size;
      text-align: center;
    }
    /* Icon scales against cqmin and multiplies by --pollen-icon-scale
       so a Very High tile's icon visibly dwarfs a Low tile's. Cap
       lifted to 8em so tall lg slices use the vertical space. */
    .pollen-tile .pollen-icon {
      font-size: calc(clamp(2em, 28cqmin, 8em) * var(--pollen-icon-scale, 1));
      line-height: 1;
      flex: 0 0 auto;
    }
    .pollen-name {
      font-size: var(--fs-label);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-secondary);
      line-height: 1.1;
    }
    .pollen-value {
      font-size: clamp(1.1em, 8cqmin, 2.8em);
      font-weight: var(--fw-black);
      line-height: 1;
      color: var(--text-primary);
      font-variant-numeric: tabular-nums;
    }
    /* 4-segment bar, same step-bar idiom the AQI widget uses. The
       height jumps up to 0.5em so the bar reads as a row of chunky
       blocks instead of a hairline; the previous --stroke-3 (~3px)
       was indistinguishable from a single em-dash, especially on a
       large cell. */
    .pollen-bar {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: var(--stroke-1);
      width: 100%;
      max-width: 10em;
      height: 0.55em;
    }
    .pollen-bar-seg {
      display: block;
      height: 100%;
    }
    .pollen-level {
      font-size: var(--fs-label);
      font-weight: var(--fw-black);
      letter-spacing: 0.04em;
      text-transform: var(--label-transform, uppercase);
      line-height: 1;
    }
    /* No-data glyph, single centered ph-minus-circle in muted grey.
       Reads as "no data" without competing with the bar tints for
       attention. */
    .pollen-no-data {
      font-size: clamp(1.4em, 9cqmin, 2.6em);
      line-height: 1;
      color: var(--text-muted);
      opacity: 0.7;
    }

    /* xs: drop the numeric value + bar, keep just icon / name / level
      , three tiny tiles trying to fit five lockup elements get
       illegible fast. */
    @container (max-width: 280px) {
      .pollen-value { display: none; }
      .pollen-bar { display: none; }
      .pollen-tile { gap: var(--space-1); padding: var(--space-2); }
    }

    /* Tall portrait cells: stack the three tiles vertically (1
       column) and lay each tile out as a horizontal row, icon on
       the left, name + bar + value + level word as a lockup on the
       right. Keeps the tile's vertical footprint compact when a 3-
       tile column would otherwise crush each tile to a sliver. */
    @container (max-aspect-ratio: 0.7) {
      .pollen-row { grid-template-columns: 1fr; }
      .pollen-tile {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        grid-template-rows: auto auto;
        grid-template-areas:
          "icon name level"
          "icon bar  value";
        align-items: center;
        text-align: left;
        gap: 0.15em var(--space-3);
        padding: var(--space-3) var(--space-4);
      }
      .pollen-tile .pollen-icon {
        grid-area: icon;
        font-size: calc(clamp(2em, 14cqh, 3.5em) * var(--pollen-icon-scale, 1));
      }
      .pollen-name { grid-area: name; }
      .pollen-bar { grid-area: bar; max-width: 100%; align-self: end; }
      .pollen-value { grid-area: value; text-align: right; align-self: end; }
      .pollen-level { grid-area: level; text-align: right; }
    }

    /* lg: more breathing room, bigger bars + no-data glyph scales
       up so a missing-data tile doesn't look empty against neighbours
       carrying full data. */
    @container (min-width: 700px) {
      .pollen-row { gap: var(--space-4); }
      .pollen-tile { padding: var(--space-5) var(--space-4); gap: var(--space-3); }
      .pollen-bar { max-width: 14em; height: 0.7em; }
      .pollen-no-data { font-size: clamp(2em, 14cqmin, 4em); }
    }
  `;

  shadow.innerHTML = `
    ${css}
    <style>${layout}</style>
    <div class="w" data-widget="weather_pollen_count">
      <div class="w-title">
        <i class="ph-bold ph-flower" style="color:${levelAccent(overall) || 'var(--accent-3)'}"></i>
        <h3>${escapeHtml(label || "Pollen")}</h3>
        ${overall ? `<span class="w-title-meta" style="color:${levelAccent(overall)}">${escapeHtml(overall)}</span>` : ""}
      </div>
      <div class="w-body">
        ${tiles ? `<div class="pollen-row">${tiles}</div>` : '<p class="u-muted">No pollen data.</p>'}
      </div>
    </div>`;
}
