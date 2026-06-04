// ha_battery — Spectra list archetype. One zebra row per battery,
// level meta accented by severity (critical → accent-1 terracotta,
// low → accent-2 ochre, healthy → text-secondary).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function batteryIcon(level) {
  if (level == null) return "ph-battery-empty";
  if (level >= 90) return "ph-battery-full";
  if (level >= 60) return "ph-battery-high";
  if (level >= 30) return "ph-battery-medium";
  if (level >= 10) return "ph-battery-low";
  return "ph-battery-empty";
}

function levelAccent(item) {
  if (item.critical) return "var(--accent-1)";
  if (item.low) return "var(--accent-2)";
  return "var(--text-secondary)";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_battery">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Batteries</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const summary = data.summary || {};
  const title = data.label || "Batteries";

  let meta = "";
  if (summary.critical > 0) {
    meta = `<span class="w-title-meta" style="color:var(--accent-1)">${summary.critical} CRITICAL</span>`;
  } else if (summary.low > 0) {
    meta = `<span class="w-title-meta" style="color:var(--accent-2)">${summary.low} LOW</span>`;
  } else if (summary.shown != null && summary.count != null) {
    meta = `<span class="w-title-meta">${summary.shown}/${summary.count}</span>`;
  }

  const rows = items.map((it, i) => {
    const accent = levelAccent(it);
    const ph = batteryIcon(it.level);
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(it.name)}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${it.level == null ? "—" : escapeHtml(String(it.level)) + "%"}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_battery">
      <div class="w-title">
        <i class="ph-bold ph-battery-medium" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(title)}</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows || '<p class="u-muted">No batteries.</p>'}</div>
    </div>`;
}
