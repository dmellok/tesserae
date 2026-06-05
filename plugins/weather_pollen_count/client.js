// weather_pollen_count — Spectra status archetype, one tile per pollen type.
//
// Three categories (tree / grass / weed) render as colour-blocked
// tiles in a row, each one carrying the category icon as a hero glyph
// plus the level word ("LOW" / "MODERATE" / "HIGH" / "V. HIGH") in
// the matching accent. Tile background uses the level's -soft tint so
// at a glance you see a row of green / yellow / red blocks rather
// than three near-identical rows of text.

// Per-level accent + soft companion + short label used inside the tile.
const LEVEL_ACCENT = {
  Low: "var(--accent-3)",        // moss — quiet good
  Moderate: "var(--accent-2)",   // ochre — caution
  High: "var(--accent-1)",       // terracotta — alert
  "Very high": "var(--accent-6)" // plum — extreme
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
  return LEVEL_SHORT[level] || "—";
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
    const accent = levelAccent(item.level);
    const soft = levelSoft(item.level);
    const short = levelShort(item.level);
    return `
      <div class="pollen-tile" style="background:${soft}">
        <i class="ph-bold ${ph}" style="color:${accent}"></i>
        <span class="pollen-name">${escapeHtml(item.label || "")}</span>
        <span class="pollen-level" style="color:${accent}">${escapeHtml(short)}</span>
      </div>`;
  }).join("");

  const layout = `
    .pollen-row {
      flex: 1 1 auto;
      min-height: 0;
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: var(--space-3);
    }
    .pollen-tile {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--space-2);
      padding: var(--space-4) var(--space-3);
      border-radius: var(--radius-0);
      min-height: 0;
      container-type: size;
    }
    .pollen-tile .ph-bold {
      font-size: clamp(2.4em, 22cqmin, 5em);
      line-height: 1;
    }
    .pollen-name {
      font-size: var(--fs-label);
      font-weight: var(--fw-bold);
      letter-spacing: var(--ls-label);
      text-transform: var(--label-transform, uppercase);
      color: var(--text-secondary);
    }
    .pollen-level {
      font-size: clamp(1em, 9cqw, 1.8em);
      font-weight: var(--fw-black);
      letter-spacing: 0.04em;
      text-transform: var(--label-transform, uppercase);
      line-height: 1;
    }
    /* Tall portrait cells (sm/xs): stack the three tiles as a column
       instead of crushing them into 1/3 width slivers. */
    @container (max-aspect-ratio: 0.7) {
      .pollen-row { grid-template-columns: 1fr; }
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
