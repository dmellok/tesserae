// ha_sensor — Spectra stat (single) or list (multi).
//
// One sensor → hero number (stat archetype) with the unit as a small
// trailing label. Two or more → list archetype with the sensor icon
// leading and value + unit right-aligned.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function sensorAccent(icon) {
  // Pick a coherent accent per sensor category. Mirrors weather_now's
  // metric palette: water = teal, sun/light = ochre, lightning =
  // ochre too (hot), neutral measurements stay text-secondary.
  switch (icon) {
    case "drop":
    case "thermometer-simple":
      return "var(--accent-4)";
    case "sun":
    case "lightning":
    case "battery-medium":
      return "var(--accent-2)";
    case "fan":
    case "wifi-high":
      return "var(--accent-5)";
    default:
      return "var(--text-secondary)";
  }
}

function renderStat(item, title) {
  const accent = sensorAccent(item.icon);
  const muted = item.unavailable;
  const ph = `ph-${item.icon || "gauge"}`;
  return `
    <div class="w-title">
      <i class="ph-bold ${ph}" style="color:${muted ? "var(--text-muted)" : accent}"></i>
      <h3>${escapeHtml(title)}</h3>
    </div>
    <div class="w-body stat-body">
      <div class="stat-value">
        ${escapeHtml(item.value ?? "—")}
        ${item.unit ? `<span class="unit">${escapeHtml(item.unit)}</span>` : ""}
      </div>
    </div>`;
}

function renderList(items, title) {
  const rows = items.map((it, i) => {
    const accent = it.unavailable ? "var(--text-muted)" : sensorAccent(it.icon);
    const ph = `ph-${it.icon || "gauge"}`;
    const unit = it.unit ? `<span class="u-muted" style="font-weight:var(--fw-semi)"> ${escapeHtml(it.unit)}</span>` : "";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(it.name)}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${escapeHtml(it.value ?? "—")}${unit}</span>
      </div>`;
  }).join("");
  return `
    <div class="w-title">
      <i class="ph-bold ph-gauge" style="color:var(--accent-3)"></i>
      <h3>${escapeHtml(title)}</h3>
      <span class="w-title-meta">${items.length}</span>
    </div>
    <div class="w-body list-body">${rows}</div>`;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_sensor">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Sensors</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const title = data.title || (items.length === 1 ? items[0].name : "Sensors");

  if (data.empty || items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_sensor">
        <div class="w-title"><i class="ph-bold ph-gauge"></i><h3>${escapeHtml(title)}</h3></div>
        <div class="w-body"><p class="u-muted">No sensors selected.</p></div>
      </div>`;
    return;
  }

  const body = items.length === 1 ? renderStat(items[0], title) : renderList(items, title);
  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_sensor">${body}</div>`;
}
