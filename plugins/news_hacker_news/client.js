// news_hacker_news — top N stories.

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

const FEED_LABELS = { top: "Top", new: "New", best: "Best", show: "Show HN", ask: "Ask HN" };

export default async function render(shadow, ctx) {
  const data = ctx.data || {};
  if (data.error) {
    shadow.innerHTML = `
      <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
      <link rel="stylesheet" href="/plugins/news_hacker_news/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const stories = Array.isArray(data.stories) ? data.stories : [];
  const label = FEED_LABELS[data.feed] || "Hacker News";

  const rows = stories.map((s, i) => `
    <div class="hn-row">
      <span class="hn-num">${i + 1}</span>
      <div class="hn-body">
        <div class="hn-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</div>
        <div class="hn-meta">
          <span class="hn-score"><i class="ph-fill ph-fill-arrow-up"></i>${s.score}</span>
          <span class="hn-com"><i class="ph ph-chat-circle"></i>${s.comments}</span>
          <span class="hn-by">${escapeHtml(s.by)}</span>
          <span class="hn-when">${escapeHtml(ago(s.time))}</span>
        </div>
      </div>
    </div>
  `).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
    <link rel="stylesheet" href="/plugins/news_hacker_news/client.css">
    <div class="root size-${size}">
      <header class="hn-bar">
        <span class="hn-mark" aria-hidden="true"></span>
        <span class="hn-title-bar">Hacker News · ${escapeHtml(label)}</span>
        <i class="ph-bold ph-flame hn-bar-icon"></i>
      </header>
      <section class="hn-list">${rows || `<div class="hn-empty">No stories.</div>`}</section>
    </div>
  `;
}
