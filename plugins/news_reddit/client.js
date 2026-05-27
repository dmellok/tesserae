// news_reddit — top of /r/something. Bauhaus shape: header bar, a
// lede block for the top post, then a clean list of remaining posts.

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
function fmtScore(v) {
  if (v == null) return "—";
  if (v >= 10000) return (v / 1000).toFixed(1) + "k";
  if (v >= 1000) return (v / 1000).toFixed(1) + "k";
  return String(v);
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

  function meta(p) {
    return `
      <span class="rd-score"><i class="ph-bold ph-arrow-fat-up"></i>${fmtScore(p.score)}</span>
      <span class="rd-com"><i class="ph-bold ph-chat-circle-text"></i>${p.comments}</span>
      <span class="rd-by"><i class="ph-bold ph-user"></i>u/${escapeHtml(p.author)}</span>
      <span class="rd-when"><i class="ph-bold ph-clock"></i>${escapeHtml(ago(p.time))}</span>
    `;
  }

  const lede = posts[0];
  const rest = posts.slice(1);

  const ledeHtml = lede ? `
    <article class="rd-lede">
      <div class="rd-lede-num">01</div>
      <div class="rd-lede-body">
        <h3 class="rd-lede-title" title="${escapeHtml(lede.title)}">
          ${lede.is_self ? '<i class="ph-bold ph-chat-circle-text rd-self" aria-hidden="true"></i>' : ""}
          ${escapeHtml(lede.title)}
        </h3>
        <div class="rd-meta rd-meta--lede">${meta(lede)}</div>
      </div>
    </article>
  ` : "";

  const restHtml = rest.map((p, i) => {
    const n = String(i + 2).padStart(2, "0");
    return `
      <article class="rd-row">
        <span class="rd-num">${n}</span>
        <div class="rd-body">
          <div class="rd-title" title="${escapeHtml(p.title)}">
            ${p.is_self ? '<i class="ph-bold ph-chat-circle-text rd-self" aria-hidden="true"></i>' : ""}
            ${escapeHtml(p.title)}
          </div>
          <div class="rd-meta">${meta(p)}</div>
        </div>
      </article>
    `;
  }).join("");

  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
    <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
    <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
    <link rel="stylesheet" href="/plugins/news_reddit/client.css">
    <div class="root size-${size}">
      <header class="wb-bar">
        <span class="wb-mark" aria-hidden="true"></span>
        <span class="wb-title">r/${escapeHtml(data.subreddit)} · ${escapeHtml(data.sort)}${data.sort === "top" ? " · " + escapeHtml(data.window) : ""}</span>
        <i class="ph-bold ph-reddit-logo wb-bar-icon"></i>
      </header>
      ${ledeHtml}
      <section class="rd-list">${restHtml || (!lede ? `<div class="rd-empty">No posts.</div>` : "")}</section>
    </div>
  `;
}
