// ha_locks — Spectra list archetype. Lock / door / window / garage
// entries with a kind icon and a secure-state meta. Unsecured rows get
// the accent-1 alert colour so a glance at the widget surfaces what's
// open.

const KIND_PH = {
  lock: "ph-lock",
  door: "ph-door",
  window: "ph-rectangle",
  garage: "ph-garage",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function kindIcon(kind) {
  return KIND_PH[kind] || "ph-lock";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_locks">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Locks</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const entries = Array.isArray(data.entries) ? data.entries : [];
  const place = data.place || data.label || "Locks";
  const summary = data.summary || {};
  const unsecured = summary.unsecured ?? 0;

  if (entries.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_locks">
        <div class="w-title"><i class="ph-bold ph-lock"></i><h3>${escapeHtml(place)}</h3></div>
        <div class="w-body"><p class="u-muted">No entries.</p></div>
      </div>`;
    return;
  }

  const rows = entries.map((e, i) => {
    const accent = e.secured ? "var(--accent-3)" : "var(--accent-1)";
    const ph = kindIcon(e.kind);
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(e.name)}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${escapeHtml(e.state || (e.secured ? "secured" : "open"))}</span>
      </div>`;
  }).join("");

  const titleAccent = unsecured > 0 ? "var(--accent-1)" : "var(--accent-3)";
  const meta = unsecured > 0
    ? `<span class="w-title-meta" style="color:var(--accent-1)">${unsecured} OPEN</span>`
    : `<span class="w-title-meta">ALL SECURED</span>`;

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_locks">
      <div class="w-title">
        <i class="ph-bold ph-lock" style="color:${titleAccent}"></i>
        <h3>${escapeHtml(place)}</h3>
        ${meta}
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
