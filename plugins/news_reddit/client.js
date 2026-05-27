// news_reddit — top of /r/something.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function ago(t) {
  if (!t) return "";
  const s = Math.floor(Date.now() / 1000 - t);
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/news_reddit/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const posts = Array.isArray(data.posts) ? data.posts : [];

  const rows = posts.map((p, i) => `
    <div class="rd-row">
      <span class="rd-score">${p.score}</span>
      <div class="rd-body">
        <div class="rd-title" title="${escapeHtml(p.title)}">${p.is_self ? '<i class="ph-fill ph-fill-chat-circle rd-self"></i>' : ""}${escapeHtml(p.title)}</div>
        <div class="rd-meta">
          <span class="rd-com"><i class="ph ph-chat-circle"></i>${p.comments}</span>
          <span class="rd-author">u/${escapeHtml(p.author)}</span>
          <span class="rd-when">${escapeHtml(ago(p.time))}</span>
        </div>
      </div>
    </div>
  `).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/plugins/news_reddit/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">r/${escapeHtml(data.subreddit)} · ${escapeHtml(data.sort)}${data.sort === "top" ? " · " + escapeHtml(data.window) : ""}</span>
        <i class="ph-bold ph-reddit-logo wb-bar-icon"></i>
      </header>
      <section class="rd-list">${rows || `<div class="rd-empty">No posts.</div>`}</section>
    </div>
  `;
}
