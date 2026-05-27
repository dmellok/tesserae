// news_wikipedia_otd — Wikipedia "On This Day".

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const TYPE_LABEL = { events: "Events", births: "Births", deaths: "Deaths", holidays: "Holidays" };

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/news_wikipedia_otd/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const items = Array.isArray(data.items) ? data.items : [];
  const label = TYPE_LABEL[data.kind] || "On this day";

  const rows = items.map((it) => `
    <div class="wo-row">
      <span class="wo-year">${escapeHtml(String(it.year || "—"))}</span>
      <span class="wo-text">${escapeHtml(it.text)}</span>
    </div>
  `).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/news_wikipedia_otd/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${escapeHtml(data.date)} · ${escapeHtml(label)}</span>
        <i class="ph-bold ph-book-open wb-bar-icon"></i>
      </header>
      <section class="wo-list">${rows || `<div class="wo-empty">Nothing notable.</div>`}</section>
    </div>
  `;
}
