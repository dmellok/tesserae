// news_wikipedia_otd — Wikipedia "On This Day". Bauhaus shape: the
// year is the "rank" — gets the hero treatment on the lede block and
// the big mono number on the rest of the rows.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const TYPE_LABEL = { events: "Events", births: "Births", deaths: "Deaths", holidays: "Holidays" };
const TYPE_ICON = {
  events: "ph-calendar-blank",
  births: "ph-baby",
  deaths: "ph-flower",
  holidays: "ph-confetti",
};

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
  const barIcon = TYPE_ICON[data.kind] || "ph-book-open";

  const lede = items[0];
  const rest = items.slice(1);

  const ledeHtml = lede ? `
    <article class="wo-lede">
      <div class="wo-lede-year">${escapeHtml(String(lede.year || "—"))}</div>
      <div class="wo-lede-body">
        <p class="wo-lede-text">${escapeHtml(lede.text)}</p>
      </div>
    </article>
  ` : "";

  const restHtml = rest.map((it) => `
    <article class="wo-row">
      <span class="wo-year">${escapeHtml(String(it.year || "—"))}</span>
      <div class="wo-body">
        <div class="wo-text">${escapeHtml(it.text)}</div>
      </div>
    </article>
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
        <i class="ph-bold ${barIcon} wb-bar-icon"></i>
      </header>
      ${ledeHtml}
      <section class="wo-list">${restHtml || (!lede ? `<div class="wo-empty">Nothing notable.</div>` : "")}</section>
    </div>
  `;
}
