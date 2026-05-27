// news_hacker_news — top N stories in a Bauhaus list.
//
// The first item is a lede block (big accent panel with huge number,
// large title, and prominent score/comments icons). Subsequent items
// rotate through a tinted row palette so the eye walks down naturally
// instead of looking at a wall of grey.

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
      <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
      <link rel="stylesheet" href="/plugins/news_hacker_news/client.css">
      <div class="root error"><i class="ph ph-warning-circle"></i><span>${escapeHtml(data.error)}</span></div>
    `;
    return;
  }
  const size = ctx.cell.size;
  const stories = Array.isArray(data.stories) ? data.stories : [];
  const label = FEED_LABELS[data.feed] || "Hacker News";

  function meta(s) {
    return `
      <span class="hn-score"><i class="ph-bold ph-arrow-fat-up"></i>${s.score}</span>
      <span class="hn-com"><i class="ph-bold ph-chat-circle-text"></i>${s.comments}</span>
      <span class="hn-by"><i class="ph-bold ph-user"></i>${escapeHtml(s.by)}</span>
      <span class="hn-when"><i class="ph-bold ph-clock"></i>${escapeHtml(ago(s.time))}</span>
    `;
  }

  const lede = stories[0];
  const rest = stories.slice(1);

  const ledeHtml = lede ? `
    <article class="hn-lede">
      <div class="hn-lede-num">01</div>
      <div class="hn-lede-body">
        <h3 class="hn-lede-title" title="${escapeHtml(lede.title)}">${escapeHtml(lede.title)}</h3>
        <div class="hn-meta hn-meta--lede">${meta(lede)}</div>
      </div>
    </article>
  ` : "";

  const restHtml = rest.map((s, i) => {
    const n = String(i + 2).padStart(2, "0");
    return `
      <article class="hn-row">
        <span class="hn-num">${n}</span>
        <div class="hn-body">
          <div class="hn-title" title="${escapeHtml(s.title)}">${escapeHtml(s.title)}</div>
          <div class="hn-meta">${meta(s)}</div>
        </div>
      </article>
    `;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/news_hacker_news/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">Hacker News · ${escapeHtml(label)}</span>
        <i class="ph-bold ph-flame wb-bar-icon"></i>
      </header>
      ${ledeHtml}
      <section class="hn-list">${restHtml || (!lede ? `<div class="hn-empty">No stories.</div>` : "")}</section>
    </div>
  `;
}
