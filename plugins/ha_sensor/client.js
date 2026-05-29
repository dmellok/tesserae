// ha_sensor — one or more HA entities as Bauhaus value blocks.
// One entity → a 50/50 hero (big number + icon panel). Several → a grid
// of solid colour-blocked stat tiles cycling the primary triad.

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
  <link rel="stylesheet" href="/plugins/ha_sensor/client.css">`;

const TONES = ["accent", "surface2", "accent2", "accent3"];

function bar(title, icon) {
  return `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="wb-title">${escapeHtml(title)}</span>
      <i class="wb-bar-icon ph ph-${icon}" aria-hidden="true"></i>
    </header>`;
}

function valueHtml(it) {
  const v = it.unavailable ? "—" : escapeHtml(it.value);
  const u = it.unavailable ? "" : escapeHtml(it.unit);
  return `${v}${u ? `<small>${u}</small>` : ""}`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};

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
        ${bar(data.title || "Sensors", "gauge")}
        <div class="hs-stub">
          <i class="ph-duotone ph-gauge" aria-hidden="true"></i>
          <div class="hs-stub-primary">Pick entities</div>
          <div class="hs-stub-secondary">List Home Assistant entity ids in the cell options.</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const items = data.items;

  if (items.length === 1) {
    const it = items[0];
    shadow.innerHTML = `${HEAD}
      <div class="root size-${size} is-hero">
        ${bar(data.title, it.icon)}
        <section class="hs-hero${it.unavailable ? " is-unavailable" : ""}">
          <div class="hs-hero-text">
            <div class="hs-hero-value">${valueHtml(it)}</div>
            ${it.unavailable ? `<div class="hs-hero-sub">unavailable</div>` : ""}
          </div>
          <div class="hs-hero-icon" aria-hidden="true"><i class="ph-bold ph-${it.icon}"></i></div>
        </section>
      </div>`;
    return;
  }

  const tiles = items.map((it, i) => `
    <article class="hs-tile hs-tile--${TONES[i % TONES.length]}${it.unavailable ? " is-unavailable" : ""}">
      <i class="hs-tile-icon ph-bold ph-${it.icon}" aria-hidden="true"></i>
      <div class="hs-tile-name">${escapeHtml(it.name)}</div>
      <div class="hs-tile-value">${valueHtml(it)}</div>
    </article>`).join("");

  shadow.innerHTML = `${HEAD}
    <div class="root size-${size}" style="--hs-count:${items.length}">
      ${bar(data.title, "gauge")}
      <section class="hs-grid">${tiles}</section>
    </div>`;
}
