// ha_entities — Spectra list archetype. Generic entity list with a
// Phosphor icon per row + status label right-aligned.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// "on" / "open" / "playing" → live accent (teal); "off" / "closed" →
// muted; problem states → terracotta; everything else (numeric, named
// states) → text-secondary.
function statusAccent(status) {
  if (!status) return "var(--text-secondary)";
  const s = String(status).toLowerCase();
  if (["on", "open", "playing", "home"].includes(s)) return "var(--accent-4)";
  if (["off", "closed", "idle"].includes(s)) return "var(--text-muted)";
  if (["missing", "unavailable", "unknown"].includes(s)) return "var(--accent-1)";
  return "var(--text-secondary)";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_entities">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Entities</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (data.empty || !Array.isArray(data.items) || data.items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_entities">
        <div class="w-title"><i class="ph-bold ph-list"></i><h3>${escapeHtml(data.title || "Entities")}</h3></div>
        <div class="w-body"><p class="u-muted">No entities selected.</p></div>
      </div>`;
    return;
  }

  const items = data.items;
  const title = data.title || "Entities";

  const rows = items.map((it, i) => {
    const accent = statusAccent(it.status);
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ph-${escapeHtml(it.icon || "circle")}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(it.name)}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${escapeHtml(it.label || "—")}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_entities">
      <div class="w-title">
        <i class="ph-bold ph-list" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(title)}</h3>
        <span class="w-title-meta">${items.length}</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
