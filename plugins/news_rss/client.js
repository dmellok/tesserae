// news_rss — N headlines from one feed.

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

  const rows = items.map((it, i) => `
    <div class="nr-row">
      <span class="nr-num">${i + 1}</span>
      <span class="nr-title" title="${escapeHtml(it.title)}">${escapeHtml(it.title)}</span>
      <span class="nr-when">${escapeHtml(ago(it.published))}</span>
    </div>
  `).join("");

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
      <section class="nr-list">${rows || `<div class="nr-empty">No headlines.</div>`}</section>
    </div>
  `;
}
