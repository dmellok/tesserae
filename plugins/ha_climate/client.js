// ha_climate — one or more HA thermostat tiles, Bauhaus style.
// One entity → a 50/50 hero. Several → a grid of solid tiles tinted by
// what each thermostat is doing (heating / cooling / idle).

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
  <link rel="stylesheet" href="/plugins/ha_climate/client.css">`;

function bar(title, icon) {
  return `
    <header class="wb-bar">
      <span class="wb-mark" aria-hidden="true"></span>
      <span class="wb-title">${escapeHtml(title)}</span>
      <i class="wb-bar-icon ph ph-${icon}" aria-hidden="true"></i>
    </header>`;
}

// Map mode/action to a tone class so the block colour reads at a glance.
function tone(it) {
  const a = (it.action || it.mode || "").toLowerCase();
  if (a.includes("heat")) return "heat";
  if (a.includes("cool")) return "cool";
  if (a === "off" || a === "idle") return "idle";
  return "neutral";
}

function targetText(it) {
  if (it.target) return `Set ${escapeHtml(it.target)}°`;
  if (it.target_low && it.target_high)
    return `${escapeHtml(it.target_low)}°–${escapeHtml(it.target_high)}°`;
  return "";
}

function chip(it) {
  const t = escapeHtml(it.action || it.mode_label || "");
  return t ? `<span class="hc-chip">${t}</span>` : "";
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
        ${bar(data.title || "Climate", "thermometer-simple")}
        <div class="hc-stub">
          <i class="ph-duotone ph-thermometer-simple" aria-hidden="true"></i>
          <div class="hc-stub-primary">Pick climate entities</div>
          <div class="hc-stub-secondary">List thermostat entity ids in the cell options.</div>
        </div>
      </div>`;
    return;
  }

  const size = ctx.cell.size;
  const items = data.items;

  if (items.length === 1) {
    const it = items[0];
    const cur = it.unavailable ? "—" : (escapeHtml(it.current) || "—");
    const tgt = targetText(it);
    shadow.innerHTML = `${HEAD}
      <div class="root size-${size} is-hero">
        ${bar(data.title, it.icon)}
        <section class="hc-hero tone-${tone(it)}">
          <div class="hc-hero-text">
            <div class="hc-current">${cur}<span class="hc-deg">°</span></div>
            <div class="hc-meta">${tgt ? `<span class="hc-target">${tgt}</span>` : ""}${chip(it)}</div>
          </div>
          <div class="hc-hero-icon" aria-hidden="true"><i class="ph-bold ph-${it.icon}"></i></div>
        </section>
      </div>`;
    return;
  }

  const tiles = items.map((it) => {
    const cur = it.unavailable ? "—" : (escapeHtml(it.current) || "—");
    const tgt = targetText(it);
    return `
      <article class="hc-tile tone-${tone(it)}${it.unavailable ? " is-unavailable" : ""}">
        <div class="hc-tile-head">
          <i class="hc-tile-icon ph-bold ph-${it.icon}" aria-hidden="true"></i>
          <span class="hc-tile-name">${escapeHtml(it.name)}</span>
        </div>
        <div class="hc-tile-current">${cur}<span class="hc-deg">°</span></div>
        <div class="hc-meta">${tgt ? `<span class="hc-target">${tgt}</span>` : ""}${chip(it)}</div>
      </article>`;
  }).join("");

  shadow.innerHTML = `${HEAD}
    <div class="root size-${size}" style="--hc-count:${items.length}">
      ${bar(data.title, "thermometer-simple")}
      <section class="hc-grid">${tiles}</section>
    </div>`;
}
