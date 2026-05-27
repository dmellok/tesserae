// sky_bom_warnings — Bauhaus card of current BoM severe-weather
// warnings. Each warning is a colour-blocked block keyed by its
// warning_group_type ("major" -> accent3 red, default -> surface2),
// with a phase tag (NEW / UPDATE / CANCELLED) and a Phosphor icon
// picked from the warning type.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function ago(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const TYPE_ICON = {
  flood_warning: "ph-drop",
  flood_watch: "ph-drop-half",
  severe_thunderstorm_warning: "ph-lightning",
  severe_weather_warning: "ph-wind",
  bushfire: "ph-fire",
  total_fire_ban: "ph-fire",
  fire_weather_warning: "ph-fire",
  frost_warning: "ph-snowflake",
  marine_wind_warning: "ph-waves",
  coastal: "ph-waves",
  tropical_cyclone_advice: "ph-tornado",
  tropical_cyclone_warning: "ph-tornado",
  sheep_graziers_warning: "ph-thermometer-cold",
  damaging_winds: "ph-wind",
  heat: "ph-thermometer-hot",
};
function iconFor(type) {
  return TYPE_ICON[type] || "ph-warning-octagon";
}

function shell(size, body) {
  return `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
    <link rel="stylesheet" href="/plugins/sky_bom_warnings/client.css">
    <div class="root size-${size}">${body}</div>
  `;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = shell(ctx.cell.size,
      `<div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>`);
    return;
  }
  const size = ctx.cell.size;
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  const stateLabel = data.state === "ALL" ? "Australia" : (data.state || "—");

  const bar = `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="bw-title">BoM warnings · ${escapeHtml(stateLabel)}</span>
      <i class="ph-bold ph-warning-octagon wb-bar-icon"></i>
    </header>
  `;

  if (!warnings.length) {
    shadow.innerHTML = shell(size, `
      ${bar}
      <div class="bw-empty">
        <i class="ph-duotone ph-shield-check" aria-hidden="true"></i>
        <div class="bw-empty-primary">All clear</div>
        <div class="bw-empty-secondary">No active warnings for ${escapeHtml(stateLabel)}.</div>
      </div>
    `);
    return;
  }

  const rows = warnings.map((w) => {
    const major = (w.group || "").toLowerCase() === "major";
    const phase = (w.phase || "").toUpperCase();
    const phaseCls = phase === "NEW" ? "is-new" : phase === "CANCELLED" ? "is-cancelled" : "is-update";
    return `
      <article class="bw-card ${major ? 'is-major' : 'is-minor'} ${phaseCls}">
        <div class="bw-card-icon" aria-hidden="true">
          <i class="ph-bold ${iconFor(w.type)}"></i>
        </div>
        <div class="bw-card-body">
          <div class="bw-card-head">
            <span class="bw-card-kind">${escapeHtml(w.short_title)}</span>
            <span class="bw-phase">${escapeHtml(phase)}</span>
          </div>
          <div class="bw-card-title">${escapeHtml(w.title)}</div>
          <div class="bw-card-meta">
            <span><i class="ph-bold ph-flag"></i>${escapeHtml((w.states || []).join(" · ") || w.state || "—")}</span>
            <span><i class="ph-bold ph-clock"></i>Issued ${escapeHtml(ago(w.issued))}</span>
          </div>
        </div>
      </article>
    `;
  }).join("");

  const summary = data.total > data.shown
    ? `<footer class="bw-foot">+${data.total - data.shown} more</footer>`
    : "";

  shadow.innerHTML = shell(size, `
    ${bar}
    <section class="bw-list">${rows}</section>
    ${summary}
  `);
}
