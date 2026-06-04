// ha_climate — Spectra status archetype.
//
// One entity → full status layout (hero icon + state pill + 2-col grid).
// Multiple → first one as hero, the rest as compact status rows below.

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

function tempStr(v) {
  if (v == null || v === "") return "—";
  return `${escapeHtml(v)}°`;
}

function renderHero(item) {
  const accent = modeAccent(item.mode);
  const muted = item.unavailable;
  const current = tempStr(item.current);
  const subBits = [];
  if (item.target) subBits.push(`Target ${tempStr(item.target)}`);
  else if (item.target_low && item.target_high) {
    subBits.push(`${tempStr(item.target_low)}–${tempStr(item.target_high)}`);
  }
  if (item.action && item.action !== item.mode) subBits.push(escapeHtml(item.action));
  const sub = subBits.join(" · ") || escapeHtml(item.mode_label || "");
  const pill = item.mode_label
    ? `<span class="pill" style="background:${muted ? "var(--text-muted)" : accent}">${escapeHtml(item.mode_label)}</span>`
    : "";
  return `
    <div class="status-hero">
      <i class="ph-bold ${modeIcon(item.icon)}" style="color:${muted ? "var(--text-muted)" : accent}"></i>
      <div class="lockup">
        <span class="status-state">${current}</span>
        <span class="status-sub">${sub}</span>
      </div>
    </div>
    ${pill}`;
}

function renderGrid(item) {
  const cells = [];
  if (item.target) cells.push(["Target", `${tempStr(item.target)}`]);
  if (item.target_low) cells.push(["Low", `${tempStr(item.target_low)}`]);
  if (item.target_high) cells.push(["High", `${tempStr(item.target_high)}`]);
  if (item.action) cells.push(["Action", escapeHtml(item.action)]);
  if (!cells.length) return "";
  return `
    <div class="status-grid">
      ${cells.map(([label, value]) => `
        <div class="status-cell">
          <span class="u-label">${escapeHtml(label)}</span>
          <span class="v">${value}</span>
        </div>
      `).join("")}
    </div>`;
}

function renderRow(item) {
  const accent = modeAccent(item.mode);
  const muted = item.unavailable;
  return `
    <div class="u-spread">
      <div class="u-row">
        <i class="ph-bold ${modeIcon(item.icon)}" style="color:${muted ? "var(--text-muted)" : accent}"></i>
        <span style="font-weight:var(--fw-semi)">${escapeHtml(item.name)}</span>
      </div>
      <span style="font-weight:var(--fw-bold)">${tempStr(item.current)}</span>
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
  const primary = items[0];
  const rest = items.slice(1);

  const title = data.title || (items.length > 1 ? "Climate" : primary.name);

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_climate">
      <div class="w-title">
        <i class="ph-bold ph-thermometer-simple" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(title)}</h3>
        ${items.length > 1 ? `<span class="w-title-meta">${items.length} ZONES</span>` : ""}
      </div>
      <div class="w-body status-body">
        ${renderHero(primary)}
        ${rest.length === 0 ? renderGrid(primary) : rest.map(renderRow).join("")}
      </div>
    </div>`;
}
