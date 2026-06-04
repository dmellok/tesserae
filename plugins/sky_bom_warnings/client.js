// sky_bom_warnings — Spectra list archetype. Each Bureau of
// Meteorology warning is a zebra row with a severity-coloured icon
// (red → terracotta, orange → ochre, yellow → moss, blue/info →
// slate), tag-uppercase in the meta column. Title meta surfaces the
// worst-active severity so a glance reads the state.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const SEV_ACCENT = {
  red: "var(--accent-1)",
  orange: "var(--accent-2)",
  yellow: "var(--accent-3)",
  blue: "var(--accent-5)",
};

const ICON_PH = {
  fire: "ph-fire",
  flood: "ph-drop",
  thunderstorm: "ph-cloud-lightning",
  cyclone: "ph-tornado",
  wind: "ph-wind",
  heat: "ph-sun",
  snow: "ph-snowflake",
  rain: "ph-cloud-rain",
  warning: "ph-warning",
};

function sevAccent(sev) {
  return SEV_ACCENT[sev] || "var(--text-secondary)";
}

function iconFor(name) {
  return ICON_PH[name] || "ph-warning";
}

const SEV_RANK = { red: 4, orange: 3, yellow: 2, blue: 1 };

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_bom_warnings">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>BoM</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  const warnings = Array.isArray(data.warnings) ? data.warnings : [];

  if (warnings.length === 0) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="sky_bom_warnings">
        <div class="w-title">
          <i class="ph-bold ph-shield-check" style="color:var(--accent-3)"></i>
          <h3>BoM</h3>
          <span class="w-title-meta">ALL CLEAR</span>
        </div>
        <div class="w-body"><p class="u-muted">No active warnings.</p></div>
      </div>`;
    return;
  }

  // Worst-case severity drives the title icon + meta colour.
  const worst = warnings.reduce((w, cur) => (SEV_RANK[cur.severity] || 0) > (SEV_RANK[w.severity] || 0) ? cur : w, warnings[0]);
  const worstAccent = sevAccent(worst.severity);

  const rows = warnings.map((w, i) => {
    const accent = sevAccent(w.severity);
    const ph = iconFor(w.icon);
    return `
      <div class="list-row ${i % 2 ? "is-zebra" : ""}">
        <div class="list-lead">
          <i class="ph-bold ${ph}" style="color:${accent}"></i>
          <span class="list-title">${escapeHtml(w.area || w.tag)}</span>
        </div>
        <span class="list-meta" style="color:${accent};font-weight:var(--fw-black);letter-spacing:var(--ls-label);text-transform:uppercase">${escapeHtml(w.tag)}</span>
      </div>`;
  }).join("");

  shadow.innerHTML = `
    ${css}
    <div class="w" data-widget="sky_bom_warnings">
      <div class="w-title">
        <i class="ph-bold ph-warning" style="color:${worstAccent}"></i>
        <h3>BoM</h3>
        <span class="w-title-meta" style="color:${worstAccent}">${warnings.length} ACTIVE</span>
      </div>
      <div class="w-body list-body">${rows}</div>
    </div>`;
}
