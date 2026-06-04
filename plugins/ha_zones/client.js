// ha_zones — Spectra list archetype. One row per person/device tracker
// with their zone state as right-aligned meta (home = accent-3 live,
// away = text-secondary, named zone = accent-5 categorical). Profile
// picture replaces the leading icon when HA exposes one.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function stateAccent(state) {
  if (!state) return "var(--text-secondary)";
  const s = String(state).toLowerCase();
  if (s === "home") return "var(--accent-3)";
  if (s === "not_home" || s === "away") return "var(--text-muted)";
  return "var(--accent-5)";
}

function stateLabel(state) {
  if (!state) return "—";
  if (state === "not_home") return "Away";
  if (state === "home") return "Home";
  return state;
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_zones">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Zones</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const items = Array.isArray(data.items) ? data.items : [];
  const place = data.label || "Zones";
  const summary = data.summary || {};
  const home = summary.home ?? items.filter((i) => i.state === "home").length;
  const total = summary.total ?? items.length;

  if (items.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_zones">
        <div class="w-title"><i class="ph-bold ph-users-three"></i><h3>${escapeHtml(place)}</h3></div>
        <div class="w-body"><p class="u-muted">No people tracked.</p></div>
      </div>`;
    return;
  }

  const rows = items.map((it, i) => {
    const accent = stateAccent(it.state);
    // Profile picture replaces the icon when HA exposes one.
    const lead = it.entity_picture
      ? `<img src="${escapeHtml(it.entity_picture)}" alt="" style="width:1.4em;height:1.4em;border-radius:999px;object-fit:cover;flex:0 0 auto" />`
      : `<i class="ph-bold ph-user" style="color:${accent}"></i>`;
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          ${lead}
          <span class="list-title">${escapeHtml(it.name)}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${escapeHtml(stateLabel(it.state))}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_zones">
      <div class="w-title">
        <i class="ph-bold ph-users-three" style="color:var(--accent-3)"></i>
        <h3>${escapeHtml(place)}</h3>
        <span class="w-title-meta">${home}/${total} HOME</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
