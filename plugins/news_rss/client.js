// news_rss — N headlines from one feed. Bauhaus shape: accent lede
// block for the top headline, neutral list of the rest.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function ago(iso) {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  if (s < 604800) return `${Math.floor(s / 86400)}d`;
  return `${Math.floor(s / 604800)}w`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/news_rss/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const items = Array.isArray(data.items) ? data.items : [];
  const showSource = ctx.cell.options.show_source !== false;
  const title = showSource && data.feed_title ? data.feed_title : "Headlines";

  const lede = items[0];
  const rest = items.slice(1);

  const ledeHtml = lede ? `
    <article class="nr-lede">
      <div class="nr-lede-num">01</div>
      <div class="nr-lede-body">
        <h3 class="nr-lede-title" title="${escapeHtml(lede.title)}">${escapeHtml(lede.title)}</h3>
        ${lede.published ? `
        <div class="nr-meta nr-meta--lede">
          <span class="nr-when"><i class="ph-bold ph-clock"></i>${escapeHtml(ago(lede.published))}</span>
        </div>` : ""}
      </div>
    </article>
  ` : "";

  const restHtml = rest.map((it, i) => {
    const n = String(i + 2).padStart(2, "0");
    return `
      <article class="nr-row">
        <span class="nr-num">${n}</span>
        <div class="nr-body">
          <div class="nr-title" title="${escapeHtml(it.title)}">${escapeHtml(it.title)}</div>
          ${it.published ? `
          <div class="nr-meta">
            <span class="nr-when"><i class="ph-bold ph-clock"></i>${escapeHtml(ago(it.published))}</span>
          </div>` : ""}
        </div>
      </article>
    `;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/news_rss/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">${escapeHtml(title)}</span>
        <i class="ph-bold ph-rss wb-bar-icon"></i>
      </header>
      ${ledeHtml}
      <section class="nr-list">${restHtml || (!lede ? `<div class="nr-empty">No headlines.</div>` : "")}</section>
    </div>
  `;
}
