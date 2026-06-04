// weather_pollen_count — Spectra list archetype, one row per pollen type.
//
// Title bar shows place + overall level meta. Body is a zebra-striped
// list of the three CAMS pollen types (tree / grass / weed), each row
// carrying the level word (Low / Moderate / High / Very high) right-
// aligned with an accent tint when the level isn't Low.

const LEVEL_ACCENT = {
  Low: "var(--text-secondary)",
  Moderate: "var(--accent-2)",
  High: "var(--accent-1)",
  "Very high": "var(--accent-6)",
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
  return LEVEL_ACCENT[level] || "var(--text-secondary)";
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

  const rows = breakdown.map((item, i) => {
    const ph = POLLEN_PH[item.icon] || "ph-plant";
    const accent = levelAccent(item.level);
    const isAccent = item.level && item.level !== "Low";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(item.label)}</span>
        </div>
        <span class="list-meta ${isAccent ? "is-accent" : ""}" style="${isAccent ? `color:${accent}` : ""}">${escapeHtml(item.level || "—")}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="weather_pollen_count">
      <div class="w-title">
        <i class="ph-bold ph-flower" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(label || "Pollen")}</h3>
        ${overall ? `<span class="w-title-meta" style="color:${levelAccent(overall)}">${escapeHtml(overall)}</span>` : ""}
      </div>
      <div class="w-body list-body">${rows || '<p class="u-muted">No pollen data.</p>'}</div>
    </div>`;
}
