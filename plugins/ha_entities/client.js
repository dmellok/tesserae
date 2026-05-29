// ha_entities — Bauhaus status grid. Each entity is a row with a bold,
// status-coloured icon block, its name, and a humanised value. The icon
// block colour reads the state at a glance (on / off / other / missing).

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const HEAD = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/ha_entities/client.css">`;

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  const title = escapeHtml(data.title || "Entities");

  if (data.error) {
    shadow.innerHTML = `${HEAD}
      <div class="root error">
        <i class="ph ph-warning-circle" aria-hidden="true"></i>
        <span>${escapeHtml(data.error)}</span>
      </div>`;
    return;
  }
  if (data.empty || !(data.items && data.items.length)) {
    shadow.innerHTML = `${HEAD}
      <div class="root">
        <header class="wb-bar">
          <span class="wb-mark" aria-hidden="true"></span>
          <span class="wb-title">${title}</span>
          <i class="wb-bar-icon ph ph-squares-four" aria-hidden="true"></i>
        </header>
        <div class="he-stub">
          <i class="ph-duotone ph-squares-four" aria-hidden="true"></i>
          <div class="he-stub-primary">No entities</div>
          <div class="he-stub-secondary">List entity ids in the cell options, one per line.</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const rows = data.items.map((it) => `
    <article class="he-row is-${escapeHtml(it.status)}">
      <span class="he-chip" aria-hidden="true"><i class="ph-bold ph-${escapeHtml(it.icon)}"></i></span>
      <span class="he-name">${escapeHtml(it.name)}</span>
      <span class="he-label">${escapeHtml(it.label)}</span>
    </article>`).join("");

  shadow.innerHTML = `${HEAD}
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${title}</span>
        <i class="wb-bar-icon ph ph-squares-four" aria-hidden="true"></i>
      </header>
      <section class="he-list">${rows}</section>
    </div>`;
}
