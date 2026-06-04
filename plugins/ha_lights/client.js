// ha_lights — Spectra list archetype. One row per light with a filled-
// bulb icon when on (accent-2 ochre, the "warm light" colour) and an
// empty bulb otherwise (text-muted). Right-aligned meta is the
// brightness percentage when on.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_lights">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Lights</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const lights = Array.isArray(data.lights) ? data.lights : [];
  const place = data.place || "Lights";
  const onCount = data.on_count ?? 0;
  const total = data.total ?? lights.length;

  if (data.empty || lights.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="ha_lights">
        <div class="w-title"><i class="ph-bold ph-lightbulb"></i><h3>${escapeHtml(place)}</h3></div>
        <div class="w-body"><p class="u-muted">No lights selected.</p></div>
      </div>`;
    return;
  }

  const rows = lights.map((l, i) => {
    const accent = l.on ? "var(--accent-2)" : "var(--text-muted)";
    const ph = l.on ? "ph-lightbulb-filament" : "ph-lightbulb";
    const meta = l.on
      ? (l.brightness_pct != null ? `${escapeHtml(String(l.brightness_pct))}%` : "ON")
      : "off";
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(l.name)}</span>
        </div>
        <span class="list-meta" style="color:${accent}">${meta}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="ha_lights">
      <div class="w-title">
        <i class="ph-bold ph-lightbulb" style="color:${onCount > 0 ? "var(--accent-2)" : "var(--accent-3)"}"></i>
        <h3>${escapeHtml(place)}</h3>
        <span class="w-title-meta">${onCount}/${total} ON</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
